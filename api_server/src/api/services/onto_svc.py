# 지식카드를 온톨로지 객체에 잇는 서비스 — 카드 구조에서 낱말 뽑기 · 허브 사전으로 잇기 · 전문가 프로필 · 질문 맞추기
"""카드 ↔ 온톨로지 객체 연결(HWAXPortal docs/expert-ontology 6절 — 사용자 결정 11-21).

두 층이다.
    1. 카드 낱말(``record_mentions``) — 카드가 스스로 가진 구조(열쇠말 · 물성 줄 · 카드 종류와 티어)에서 뽑는다. 허브가
       없어도 어느 박스에서나 같은 줄이 나온다(``build_mentions``).
    2. 객체 연결(``record_entities``) — 허브가 exact 로 확인한 이름(``bind_entities`` 의 entries)이 카드 낱말과 같거나
       카드 글(제목 · 요약 · 절)에 낱말 단위로 나온 카드를 ``(hub, type_slug, key)`` 에 잇는다.

LLM 은 부르지 않는다 — 같은 카드 · 같은 규칙 · 같은 사전이면 같은 줄이다. 허브를 부르는 것도 여기 일이 아니다
(사전은 포털 ``infra/scripts/card-entity-bind.py`` 가 받아 넘긴다 — 이 서비스는 게이트웨이를 모른다).

낱말 규칙은 포털 탐침(``infra/scripts/card-entity-probe.py`` — EO-5)과 같다. 두 곳의 ``tokens`` · ``strip_particle`` ·
``term_ok`` 는 글자까지 같아야 한다(포털 시험이 두 구현에 같은 글을 넣어 견준다).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import OntoState, OntoTerm, Record, RecordEntity, RecordMention, RecordSection

log = logging.getLogger(__name__)

RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "onto_rules.json"
# 한국어 조사 · 어미 — 끝 낱말에서만 뗀 꼴을 함께 본다(긴 것부터). 떼고 남은 것이 두 글자 아래면 떼지 않는다.
PARTICLES = tuple(sorted((
    "에서는", "으로는", "에서도", "이라는", "에서", "으로", "에게", "까지", "부터", "처럼", "보다", "이며", "이고", "에는", "에도", "과의", "와의",
    "라는", "이다", "의", "이", "가", "은", "는", "을", "를", "에", "로", "와", "과", "도", "만"), key=len, reverse=True))
_TOKEN = re.compile(r"[0-9a-z]+|[가-힣]+")
_HANGUL = re.compile(r"[가-힣]")
# 굵은 연결(카드가 그 객체를 다룬다) — 열쇠말 · 물성 줄 · 제목. 그 밖(요약 · 절)은 본문 연결(지나가며 말했다).
STRONG = ("keyword", "property", "title")
_RANK = {"keyword": 0, "property": 1, "title": 2, "summary": 3, "section": 4}
_CHUNK = 5000
SEAT_WEIGHT = float(os.environ.get("AIDH_ONTO_SEAT_WEIGHT", "1.0"))
# 덧점이 다 차는 카드 수 — 굵은 연결 한 장을 1, 본문 연결 한 장을 0.5 로 센다.
SEAT_DEPTH = 3.0


# ── 글 다루기 ─────────────────────────────────────────────────────────────
def tokens(text: str) -> list[str]:
    """NFKC · 대소문자 무시 뒤 영문 · 숫자 덩어리와 한글 덩어리."""
    return _TOKEN.findall(unicodedata.normalize("NFKC", text or "").casefold())


def strip_particle(tok: str) -> str:
    """한글 낱말 끝의 조사 하나를 뗀다 — 남는 것이 두 글자 아래면 그대로."""
    if not _HANGUL.search(tok):
        return tok
    for p in PARTICLES:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[: -len(p)]
    return tok


def term_ok(toks: tuple[str, ...], *, max_ngram: int = 8, min_hangul: int = 2, min_latin: int = 3) -> bool:
    """맞출 만한 낱말인가 — 한글이 들면 min_hangul 글자 이상, 영문만이면 min_latin 이상, 숫자뿐이거나 너무 길면 뺀다."""
    if not toks or len(toks) > max_ngram:
        return False
    joined = "".join(toks)
    if joined.isdigit():
        return False
    if _HANGUL.search(joined):
        return len(joined) >= min_hangul
    return len(joined) >= min_latin


# ── 규칙 ─────────────────────────────────────────────────────────────────
def _patch(a: Any, b: Any) -> Any:
    if not isinstance(b, dict):
        return b
    out = dict(a) if isinstance(a, dict) else {}
    for k, v in b.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = _patch(out.get(k), v)
    return out


def load_rules(path: Path | None = None) -> dict:
    """규칙 파일 + 박스 덧씌우기(``onto_rules.local.json`` — JSON merge patch). 덧씌우기가 깨졌으면 버리고 기본으로 간다."""
    path = path or RULES_PATH
    rules = json.loads(path.read_text(encoding="utf-8"))
    local = path.with_name(path.stem + ".local.json")
    overlay = False
    if local.exists():
        try:
            patch = json.loads(local.read_text(encoding="utf-8"))
            if not isinstance(patch, dict):
                raise ValueError("객체가 아니다")
            rules = _patch(rules, patch)
            overlay = True
        except (ValueError, OSError) as exc:
            log.warning("onto_rules.local.json 을 못 읽어 기본 규칙으로 간다: %s", exc)
    rules.pop("_doc", None)
    rules["_overlay"] = overlay
    return rules


def rules_fingerprint(rules: dict) -> str:
    body = {k: v for k, v in rules.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _limits(rules: dict) -> dict:
    return {"max_ngram": int(rules.get("max_ngram", 8)), "min_hangul": int(rules.get("min_hangul", 2)),
            "min_latin": int(rules.get("min_latin", 3))}


def _stop(rules: dict) -> set[tuple[str, ...]]:
    return {tuple(tokens(s)) for s in rules.get("stop") or [] if isinstance(s, str) and tokens(s)}


def card_types(rules: dict, doc_type: str | None, card: Any) -> list[str]:
    """카드 종류 · 티어가 말하는 코어 타입 — 규칙에 적힌 것만(차례를 지키고 겹침은 한 번)."""
    spec = (rules.get("card_types") or {}).get(doc_type or "")
    if not isinstance(spec, dict):
        return []
    out: list[str] = list(spec.get("always") or [])
    card = card if isinstance(card, dict) else {}
    for field in ("type", "tier"):
        by = spec.get(field)
        value = card.get(field)
        if isinstance(by, dict) and isinstance(value, str):
            out.extend(by.get(value) or [])
    return [t for t in dict.fromkeys(out) if isinstance(t, str) and t]


def extract(rules: dict, *, doc_type: str | None, keywords: Iterable[Any] = (), card: Any = None,
            properties: Any = None) -> list[tuple[str, str, str, str]]:
    """카드 한 장의 줄 — ``(source, type_slug, norm, term)``. 같은 카드 · 같은 규칙이면 같은 줄 · 같은 차례다."""
    lim, stop = _limits(rules), _stop(rules)
    rows: dict[tuple[str, str, str], str] = {}
    for t in card_types(rules, doc_type, card):
        rows.setdefault(("card", t, ""), "")

    def add(source: str, raw: Any) -> None:
        if not isinstance(raw, str) or not raw.strip():
            return
        toks = tuple(tokens(raw))
        if toks in stop or not term_ok(toks, **lim):
            return
        rows.setdefault((source, "", " ".join(toks)), raw.strip()[:200])

    if rules.get("keywords", True):
        for kw in keywords or ():
            add("keyword", kw)
    if doc_type in (rules.get("properties") or []) and isinstance(properties, list):
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            add("property", prop.get("name"))
            key = prop.get("key")
            add("property", key)
            if isinstance(key, str) and "." in key:
                add("property", key.rsplit(".", 1)[1])      # 'mechanical.youngs_modulus' 의 뒤 마디
    return [(src, typ, norm, term) for (src, typ, norm), term in rows.items()]


# ── 상태 ─────────────────────────────────────────────────────────────────
async def _state_get(session: AsyncSession, name: str) -> dict | None:
    row = await session.get(OntoState, name)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else None


async def _state_set(session: AsyncSession, name: str, value: dict) -> None:
    row = await session.get(OntoState, name)
    if row is None:
        session.add(OntoState(name=name, value=value))
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)


async def _records_stamp(session: AsyncSession) -> dict:
    n, newest = (await session.execute(
        select(func.count(Record.id), func.max(Record.updated_at)).where(Record.deleted_at.is_(None)))).one()
    return {"records": int(n or 0), "newest": newest.isoformat() if hasattr(newest, "isoformat") else str(newest or "")}


async def _insert(session: AsyncSession, model, rows: list[dict]) -> None:
    for i in range(0, len(rows), _CHUNK):
        await session.execute(insert(model), rows[i:i + _CHUNK])


# ── 1) 카드 낱말 짓기 ─────────────────────────────────────────────────────
async def build_mentions(session: AsyncSession, *, force: bool = False, rules: dict | None = None) -> dict:
    """살아 있는 레코드 전부에서 카드 낱말을 다시 짓는다. 지문(규칙 · 레코드 수 · 가장 새 수정 시각)이 같으면 건너뛴다.

    통째로 지우고 다시 넣는다 — 줄이 카드와 규칙에서만 나오므로 덧붙이기보다 단순하고 늘 같은 결과다. 호출자가 커밋한다.
    """
    rules = rules or load_rules()
    stamp = {"rules": rules_fingerprint(rules), **(await _records_stamp(session))}
    prev = await _state_get(session, "mentions")
    if not force and prev and all(prev.get(k) == v for k, v in stamp.items()):
        return {**prev, "skipped": True}

    need_content = set((rules.get("card_types") or {}).keys()) | set(rules.get("properties") or [])
    rows: list[dict] = []
    by_source: Counter = Counter()
    by_type: Counter = Counter()
    by_doc: Counter = Counter()

    def take(rid: str, doc_type: str | None, found: list[tuple[str, str, str, str]]) -> None:
        if found:
            by_doc[doc_type or ""] += 1
        for source, type_slug, norm, term in found:
            rows.append({"record_id": rid, "source": source, "type_slug": type_slug, "norm": norm, "term": term})
            by_source[source] += 1
            if source == "card":
                by_type[type_slug] += 1

    # 구조를 읽어야 하는 문서 종류만 content 를 읽는다 — 논문 · 특허의 content 는 본문 전체라 무겁다.
    light = select(Record.id, Record.doc_type, Record.subject_keywords).where(Record.deleted_at.is_(None))
    if need_content:
        light = light.where(Record.doc_type.notin_(need_content) | Record.doc_type.is_(None))
    for rid, doc_type, kws in (await session.execute(light)).all():
        take(rid, doc_type, extract(rules, doc_type=doc_type, keywords=kws or ()))
    if need_content:
        heavy = (select(Record.id, Record.doc_type, Record.subject_keywords, Record.content)
                 .where(Record.deleted_at.is_(None), Record.doc_type.in_(need_content))
                 .execution_options(yield_per=500))
        result = await session.stream(heavy)
        async for rid, doc_type, kws, content in result:
            content = content if isinstance(content, dict) else {}
            take(rid, doc_type, extract(rules, doc_type=doc_type, keywords=kws or (), card=content.get("card"),
                                        properties=content.get("properties")))

    await session.execute(delete(RecordMention))
    await _insert(session, RecordMention, rows)
    state = {**stamp, "rows": len(rows), "by_source": dict(by_source), "by_type": dict(by_type),
             "records_with_rows": dict(by_doc), "overlay": bool(rules.get("_overlay")),
             "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "skipped": False}
    await _state_set(session, "mentions", state)
    return state


# ── 사전 ─────────────────────────────────────────────────────────────────
class Lexicon:
    """낱말(토큰 묶음) → {(타입, 키)}. 글에서 낱말 단위로 맞춘다 — 끝 낱말은 조사를 뗀 꼴도 본다."""

    def __init__(self) -> None:
        self.terms: dict[tuple[str, ...], set[tuple[str, str]]] = defaultdict(set)
        self._first: set[str] | None = None
        self._max_n = 0
        self._strip: dict[str, str] = {}

    def add(self, toks: tuple[str, ...], type_slug: str, key: str) -> None:
        self.terms[toks].add((type_slug, key))
        self._first = None

    def _stripped(self, tok: str) -> str:
        got = self._strip.get(tok)
        if got is None:
            if len(self._strip) > 2_000_000:
                self._strip.clear()
            got = self._strip[tok] = strip_particle(tok)
        return got

    def match_tokens(self, toks: list[str]) -> dict[tuple[str, ...], int]:
        if self._first is None:
            self._first = {t[0] for t in self.terms}
            self._max_n = max((len(t) for t in self.terms), default=0)
        first, terms, n_max = self._first, self.terms, self._max_n
        found: dict[tuple[str, ...], int] = {}
        size = len(toks)
        for i in range(size):
            tok = toks[i]
            if tok not in first and self._stripped(tok) not in first:
                continue
            for n in range(1, min(n_max, size - i) + 1):
                head = tuple(toks[i:i + n - 1])
                last = toks[i + n - 1]
                cand = head + (last,)
                if cand in terms:
                    found[cand] = found.get(cand, 0) + 1
                bare = self._stripped(last)
                if bare != last:
                    cand = head + (bare,)
                    if cand in terms:
                        found[cand] = found.get(cand, 0) + 1
        return found

    def match(self, text: str) -> dict[tuple[str, ...], int]:
        return self.match_tokens(tokens(text)) if text else {}


def build_lexicon(entries: Iterable[Any], rules: dict) -> tuple[Lexicon, dict]:
    """허브가 exact 로 확인한 ``{type_slug, key, term}`` 줄로 사전을 짓는다 → (사전, 셈).

    한 타입 안에서 같은 낱말이 키 둘에 걸리면 그 타입의 그 낱말을 버린다(exact 는 하나를 가리켜야 한다). 타입이 다르면 둘 다 둔다.
    """
    lim, stop = _limits(rules), _stop(rules)
    seen: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
    counts: Counter = Counter()
    for e in entries:
        if not isinstance(e, dict):
            counts["bad"] += 1
            continue
        type_slug, key, term = e.get("type_slug"), e.get("key"), e.get("term")
        if not all(isinstance(x, str) and x.strip() for x in (type_slug, key, term)):
            counts["bad"] += 1
            continue
        toks = tuple(tokens(term))
        if toks in stop:
            counts["stop"] += 1
        elif not term_ok(toks, **lim):
            counts["short"] += 1
        else:
            seen[(toks, type_slug.strip())].add(key.strip())
            counts["given"] += 1
    lex = Lexicon()
    objects: dict[str, set[str]] = defaultdict(set)
    for (toks, type_slug), keys in seen.items():
        if len(keys) > 1:
            counts["ambiguous"] += 1
            continue
        key = next(iter(keys))
        lex.add(toks, type_slug, key)
        objects[type_slug].add(key)
        counts["terms"] += 1
    return lex, {**{k: counts.get(k, 0) for k in ("given", "terms", "short", "stop", "ambiguous", "bad")},
                 "objects": {t: len(v) for t, v in sorted(objects.items())}}


# ── 2) 잇기 ───────────────────────────────────────────────────────────────
async def bind_entities(session: AsyncSession, *, hub: str, entries: Iterable[Any], dict_version: str = "",
                        rules: dict | None = None, progress=None) -> dict:
    """사전으로 카드를 잇고 그 허브의 연결을 통째로 다시 쓴다. 호출자가 커밋한다.

    맞추는 자리 — 카드 낱말(열쇠말 · 물성 줄 — 굵은 연결) · 제목(굵은 연결) · 요약 · 절(본문 연결). 한 카드가 한 객체에 여러 자리에서
    맞으면 가장 굵은 자리를 적고 횟수를 더한다. 사전이 비면(허브에 닿지 못했다) 그 허브의 연결을 지우지 않고 그대로 둔다.
    """
    rules = rules or load_rules()
    hub = (hub or "").strip()
    if not hub:
        raise ValueError("hub 가 비었다")
    lex, lex_counts = build_lexicon(entries, rules)
    if not lex.terms:
        return {"hub": hub, "written": False, "why": "맞출 낱말이 없다 — 연결은 그대로 둔다", "dictionary": lex_counts}

    links: dict[tuple[str, str, str], list] = {}          # (record, type, key) → [가장 굵은 자리, 횟수]

    def hit(rid: str, toks: tuple[str, ...], where: str, n: int) -> None:
        for type_slug, key in lex.terms.get(toks, ()):
            cur = links.get((rid, type_slug, key))
            if cur is None:
                links[(rid, type_slug, key)] = [where, n]
            else:
                if _RANK[where] < _RANK[cur[0]]:
                    cur[0] = where
                cur[1] += n

    doc_of: dict[str, str] = {}
    for rid, doc_type, title, summary in (await session.execute(
            select(Record.id, Record.doc_type, Record.title, Record.summary).where(Record.deleted_at.is_(None)))).all():
        doc_of[rid] = doc_type or ""
        for toks, n in lex.match(title or "").items():
            hit(rid, toks, "title", n)
        for toks, n in lex.match(summary or "").items():
            hit(rid, toks, "summary", n)
    for rid, source, norm in (await session.execute(
            select(RecordMention.record_id, RecordMention.source, RecordMention.norm)
            .where(RecordMention.source != "card"))).all():
        toks = tuple(norm.split(" "))
        if rid in doc_of and toks in lex.terms:
            hit(rid, toks, source, 1)
    scanned = 0
    result = await session.stream(
        select(RecordSection.record_id, RecordSection.title, RecordSection.content_text)
        .execution_options(yield_per=2000))
    async for rid, title, body in result:
        scanned += 1
        if rid not in doc_of:
            continue
        for toks, n in lex.match(f"{title or ''}\n{body or ''}").items():
            hit(rid, toks, "section", n)
        if progress and scanned % 100_000 == 0:
            progress(scanned)

    rows = [{"record_id": rid, "hub": hub, "type_slug": type_slug, "key": key, "found_in": where,
             "strong": where in STRONG, "hits": int(n)}
            for (rid, type_slug, key), (where, n) in sorted(links.items())]
    linked_objects = {(r["type_slug"], r["key"]) for r in rows}
    term_rows = sorted({(type_slug, " ".join(toks), key) for toks, objs in lex.terms.items()
                        for type_slug, key in objs if (type_slug, key) in linked_objects})
    await session.execute(delete(RecordEntity).where(RecordEntity.hub == hub))
    await session.execute(delete(OntoTerm).where(OntoTerm.hub == hub))
    await _insert(session, RecordEntity, rows)
    await _insert(session, OntoTerm, [{"hub": hub, "type_slug": t, "norm": n, "key": k} for t, n, k in term_rows])

    by_type: Counter = Counter()
    by_where: Counter = Counter()
    rec_by_doc: dict[str, set[str]] = defaultdict(set)
    rec_by_type: dict[str, set[str]] = defaultdict(set)
    obj_by_type: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_type[r["type_slug"]] += 1
        by_where[r["found_in"]] += 1
        rec_by_doc[doc_of.get(r["record_id"], "")].add(r["record_id"])
        rec_by_type[r["type_slug"]].add(r["record_id"])
        obj_by_type[r["type_slug"]].add(r["key"])
    doc_total = Counter(doc_of.values())
    state = {
        # bind_id — 잇기마다 새로 뽑는다. 질문 사전의 캐시가 이것으로 낡았는지 본다(시각은 초 단위라 같은 초의 두 번을 못 가른다).
        "hub": hub, "written": True, "bind_id": uuid.uuid4().hex[:12], "dict_version": dict_version or "",
        "rules": rules_fingerprint(rules),
        "dictionary": lex_counts, "links": len(rows), "sections_scanned": scanned,
        "records": len(doc_of), "records_linked": len({r["record_id"] for r in rows}),
        "links_by_type": dict(sorted(by_type.items())), "links_by_where": dict(sorted(by_where.items())),
        "records_by_type": {t: len(v) for t, v in sorted(rec_by_type.items())},
        "objects_linked": {t: len(v) for t, v in sorted(obj_by_type.items())},
        "records_by_doc_type": {d: {"linked": len(v), "total": doc_total.get(d, 0)} for d, v in sorted(rec_by_doc.items())},
        "terms_kept": len(term_rows),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    await _state_set(session, f"bind:{hub}", state)
    return state


async def status(session: AsyncSession) -> dict:
    """짓기 · 잇기의 마지막 상태와 지금 줄 수(읽기만)."""
    rows = (await session.execute(select(OntoState.name, OntoState.value))).all()
    state = {name: value for name, value in rows}
    return {
        "mentions": state.get("mentions"),
        "binds": {name.split(":", 1)[1]: value for name, value in sorted(state.items()) if name.startswith("bind:")},
        "rows": {"record_mentions": int((await session.execute(select(func.count(RecordMention.id)))).scalar() or 0),
                 "record_entities": int((await session.execute(select(func.count(RecordEntity.id)))).scalar() or 0),
                 "onto_terms": int((await session.execute(select(func.count()).select_from(OntoTerm))).scalar() or 0)},
    }


# ── 3) 전문가 프로필 ──────────────────────────────────────────────────────
async def profile(session: AsyncSession) -> dict:
    """전문가 · 도메인(키의 첫 마디)마다 타입별 셈 — structural(카드 종류가 말하는 타입의 카드 수) · linked(그 타입 객체에 이어진 카드 수) ·
    objects(이어진 그 타입 객체 수). 손 줄 없이 카드(``records.agents``)로 저절로 묶인다."""
    agents_of: dict[str, list[str]] = {}
    cards: Counter = Counter()
    for rid, agents in (await session.execute(
            select(Record.id, Record.agents).where(Record.deleted_at.is_(None)))).all():
        agents = [a for a in (agents or []) if isinstance(a, str) and a]
        agents_of[rid] = agents
        for a in agents:
            cards[a] += 1
    structural: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rid, type_slug in (await session.execute(
            select(RecordMention.record_id, RecordMention.type_slug).where(RecordMention.source == "card"))).all():
        for a in agents_of.get(rid, ()):
            structural[(a, type_slug)].add(rid)
    linked: dict[tuple[str, str], set[str]] = defaultdict(set)
    objects: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for rid, hub, type_slug, key in (await session.execute(
            select(RecordEntity.record_id, RecordEntity.hub, RecordEntity.type_slug, RecordEntity.key))).all():
        for a in agents_of.get(rid, ()):
            linked[(a, type_slug)].add(rid)
            objects[(a, type_slug)].add((hub, key))

    def roll(group) -> dict:
        out: dict[str, dict] = {}
        total: Counter = Counter()
        s_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        l_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        o_ids: dict[tuple[str, str], set] = defaultdict(set)
        card_ids: dict[str, set[str]] = defaultdict(set)
        for rid, agents in agents_of.items():
            for a in agents:
                card_ids[group(a)].add(rid)
        for (a, t), ids in structural.items():
            s_ids[(group(a), t)] |= ids
        for (a, t), ids in linked.items():
            l_ids[(group(a), t)] |= ids
        for (a, t), ids in objects.items():
            o_ids[(group(a), t)] |= ids
        for g, ids in card_ids.items():
            total[g] = len(ids)
        for g in sorted(total):
            types = {}
            for t in sorted({t for (gg, t) in list(s_ids) + list(l_ids) if gg == g}):
                types[t] = {"structural": len(s_ids.get((g, t), ())), "linked": len(l_ids.get((g, t), ())),
                            "objects": len(o_ids.get((g, t), ()))}
            out[g] = {"cards": total[g], "types": types}
        return out

    return {"agents": roll(lambda a: a), "domains": roll(lambda a: a.split("-", 1)[0])}


# ── 4) 질문 맞추기 · 전문가 신호 ──────────────────────────────────────────
_LEX_CACHE: dict[str, Any] = {"stamp": None, "lex": None, "hub_of": None}


async def _question_lexicon(session: AsyncSession) -> tuple[Lexicon | None, dict]:
    """이어진 객체의 이름으로 지은 사전 — 잇기 상태가 바뀔 때만 다시 읽는다."""
    stamp = tuple((name, (value or {}).get("bind_id")) for name, value in (await session.execute(
        select(OntoState.name, OntoState.value).where(OntoState.name.like("bind:%")).order_by(OntoState.name))).all())
    if not stamp:
        return None, {}
    if _LEX_CACHE["stamp"] != stamp:
        lex = Lexicon()
        hub_of: dict[tuple[str, str], str] = {}
        for hub, type_slug, norm, key in (await session.execute(
                select(OntoTerm.hub, OntoTerm.type_slug, OntoTerm.norm, OntoTerm.key))).all():
            lex.add(tuple(norm.split(" ")), type_slug, key)
            hub_of[(type_slug, key)] = hub
        _LEX_CACHE.update(stamp=stamp, lex=lex, hub_of=hub_of)
    return _LEX_CACHE["lex"], _LEX_CACHE["hub_of"]


async def question_entities(session: AsyncSession, q: str) -> list[dict]:
    """질문 글에 낱말 단위로 나온 이어진 객체 — ``[{hub, type_slug, key}]``. 허브를 부르지 않는다."""
    lex, hub_of = await _question_lexicon(session)
    if not lex or not lex.terms or not (q or "").strip():
        return []
    found: dict[tuple[str, str], None] = {}
    for toks in lex.match(q):
        for obj in sorted(lex.terms[toks]):
            found.setdefault(obj)
    return [{"hub": hub_of.get(obj, ""), "type_slug": obj[0], "key": obj[1]} for obj in found]


# 코어 데이터 타입 — 과제 · 모델 · 프로젝트(정본 허브의 기준정보). 질문에서 이것만 풀리면 전문가의 지식이 아니라 기준정보를 묻는 질문이다.
CORE_DATA_TYPES = frozenset({"plm_task", "plm_model", "plm_project"})


def verdict(ents: list[dict], sig: dict) -> str | None:
    """질문의 객체와 전문가 신호에서 읽는 판정 **후보**(HWAXPortal docs/expert-ontology PLAN 2.4 · EO-12) — 알리기만 하고 순위 · 좌석은 바꾸지 않는다.

      ``core_question`` 풀린 객체가 전부 코어 데이터 타입(과제 · 모델 · 프로젝트)이다 — 기준정보를 묻는 질문일 수 있다(좌석은 그대로 부른다, D-E5-6).
      ``no_owner``      도메인 객체(고장모드 · 시험 항목 · 물성 …)가 풀렸는데 그 객체에 카드가 이어진 전문가가 아무도 없다 — 맡은 사람이 없을 수 있다.
      ``None``          객체가 하나도 안 풀렸거나(말할 것이 없다) 이어진 전문가가 있다.

    정밀도를 재기 전의 후보다 — 켠 박스의 그림자 기록으로 재고, 사람에게 보이는 것은 그 뒤에 정한다."""
    types = {str(e.get("type_slug") or "") for e in (ents or []) if isinstance(e, dict)}
    types.discard("")
    if not types:
        return None
    if types <= CORE_DATA_TYPES:
        return "core_question"
    return None if sig else "no_owner"


async def seat_signal(session: AsyncSession, q: str) -> tuple[dict[str, dict], list[dict]]:
    """질문의 객체에 이어진 카드를 가진 전문가 → ``{전문가: {objects, strong, weak, bonus}}`` 와 맞은 객체 목록.

    덧점 = 무게 × 맞은 객체들의 평균( 깊이 × 드묾 ).
        깊이(전문가, 객체) = min(1, (굵은 연결 카드 + 0.5 × 본문 연결 카드) / 3)
        드묾(객체) = 1 / (1 + log2(그 객체에 카드가 이어진 전문가 수))
    드묾이 없으면 흔한 객체 하나가 수백 명에게 같은 덧점을 준다 — dev 실데이터 시험(11-21, 흔한 열쇠말을 객체로 삼음)에서 486명이 모두
    상한을 받았다. 혼자 다루는 객체는 1, 여덟 명이 다루면 0.25 다.
    """
    ents = await question_entities(session, q)
    if not ents:
        return {}, []
    want = {(e["type_slug"], e["key"]) for e in ents}
    hits = [(rid, type_slug, key, bool(strong)) for rid, type_slug, key, strong in (await session.execute(
        select(RecordEntity.record_id, RecordEntity.type_slug, RecordEntity.key, RecordEntity.strong)
        .where(RecordEntity.key.in_(sorted({k for _, k in want}))))).all() if (type_slug, key) in want]
    if not hits:
        return {}, ents
    rids = sorted({h[0] for h in hits})
    agents_of: dict[str, list[str]] = {}
    for i in range(0, len(rids), _CHUNK):
        for rid, agents in (await session.execute(
                select(Record.id, Record.agents)
                .where(Record.id.in_(rids[i:i + _CHUNK]), Record.deleted_at.is_(None)))).all():
            agents_of[rid] = [a for a in (agents or []) if isinstance(a, str) and a]
    # (객체, 전문가) → [굵은 연결 카드, 본문 연결 카드]
    per: dict[tuple[str, str], dict[str, list[set]]] = defaultdict(lambda: defaultdict(lambda: [set(), set()]))
    for rid, type_slug, key, strong in hits:
        for a in agents_of.get(rid, ()):
            per[(type_slug, key)][a][0 if strong else 1].add(rid)
    score: dict[str, float] = defaultdict(float)
    objs: Counter = Counter()
    strong_ids: dict[str, set] = defaultdict(set)
    weak_ids: dict[str, set] = defaultdict(set)
    for obj, by_agent in per.items():
        rarity = 1.0 / (1.0 + math.log2(len(by_agent)))
        for a, (st, wk) in by_agent.items():
            wk = wk - st
            score[a] += min(1.0, (len(st) + 0.5 * len(wk)) / SEAT_DEPTH) * rarity
            objs[a] += 1
            strong_ids[a] |= st
            weak_ids[a] |= wk
    out: dict[str, dict] = {}
    for a in sorted(objs):
        out[a] = {"objects": objs[a], "strong": len(strong_ids[a]), "weak": len(weak_ids[a] - strong_ids[a]),
                  "bonus": round(SEAT_WEIGHT * score[a] / len(want), 4)}
    return out, ents
