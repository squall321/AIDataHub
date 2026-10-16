# 지식카드에서 한국어 검색 평가 질의(짧은 검색어 · 자연어 질문)를 만드는 생성기 — 시드 고정 표본, 이어하기, 로컬 LLM.
"""한국어 검색 평가셋 생성기.

하는 일
  1. 표본(``cards.jsonl``) — 전문가 키의 접두사(도메인, ``discover_svc.build_domain_rollup`` 과 같은 규칙)마다
     같은 수의 카드를 고른다. 도메인 안에서는 전문가를 돌아가며 하나씩 고르므로 한 전문가에 몰리지 않는다.
     시드와 DB 가 같으면 같은 표본이 나온다. 파일이 이미 있으면 다시 뽑지 않는다.
  2. 질의(``queries.jsonl``) — 카드마다 LLM 에서 두 질의를 받는다. ``keyword``(조사 없는 2~4 낱말)와
     ``question``(한 문장 자연어 질문)이다. 정답은 그 카드다. 이미 만든 카드는 건너뛴다(이어하기).
     규칙(한국어 낱말 · 한자 없음 · 낱말 수 · 물음표)을 어긴 판은 사유를 붙여 되묻고(최대 ``ATTEMPTS`` 번), 그래도
     안 되면 ``gen_failures.jsonl`` 에 사유를 남기고 건너뛴다.

제목을 그대로 옮긴 질의는 어휘 검색을 거저 맞히게 하므로 ``title_overlap``(질의의 글자 바이그램 중 제목에 있는
몫)과 ``title_copy`` 로 따로 표시한다. 실행기는 이 표시로 나눠 센다.

DB 는 읽기 전용 세션으로만 연다(``default_transaction_read_only``). 카드 본문은 LLM 요청에만 싣고 파일에는
남기지 않는다. 평가셋은 사내 내용에서 나온 것이라 리포에 넣지 않는다(기본 위치는 gitignore 된 ``data/``).

실행(리포 루트에서)
  api_server/.venv/bin/python api_server/scripts/ko_search_eval/gen_queries.py --per-domain 15

LLM 은 OpenAI 호환 끝점이다. 주소·모델은 ``KOSEARCH_LLM_BASE_URL`` · ``KOSEARCH_LLM_MODEL`` 로 주고, 없으면
``VLLM_BASE_URL`` · ``VLLM_MODEL``, 그것도 없으면 ``http://127.0.0.1:8000/v1`` 의 첫 모델을 쓴다. 키가 필요하면
``KOSEARCH_LLM_API_KEY``(또는 ``VLLM_API_KEY``) 환경변수로만 준다 — 명령줄에 비밀을 싣지 않는다.
DB 접속값은 ``deploy/apptainer/.env``(``--env-file`` 로 바꾼다)의 ``POSTGRES_*`` 이고 같은 이름의 환경변수가 있으면
그것이 앞선다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[3]          # 리포 루트(api_server/scripts/ko_search_eval/ 의 세 칸 위)
DEFAULT_DATA = Path(__file__).resolve().parent / "data"
ENV_FILE = ROOT / "deploy" / "apptainer" / ".env"

BODY_CHARS = 2000       # LLM 에 보이는 본문 앞부분(글자). 7B 로컬 모델의 16K 창에 넉넉히 든다.
SUMMARY_CHARS = 600
MIN_BODY_CHARS = 300    # 본문이 이보다 짧은 카드는 질의를 만들 재료가 모자라 표본에서 뺀다.
ATTEMPTS = 4            # 되묻기를 포함한 시도 수. 되물을수록 온도를 올려 같은 답의 반복을 끊는다.

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_HAN = re.compile(r"[\u3400-\u9fff]")   # 한자 — 작은 Qwen 은 한국어를 쓰다 중국어로 샌다
_NORM_DROP = re.compile(r"[^0-9a-z가-힣]")
_IMAGE_MARK = re.compile(r"<!--\s*image\s*-->")

SYSTEM = (
    "너는 사내 엔지니어링 지식 검색 시스템을 평가할 질의를 만든다. "
    "주어진 지식카드 하나를 찾으려는 한국인 엔지니어가 검색창에 칠 법한 질의를 쓴다."
)
PROMPT = """아래 지식카드를 찾으려는 사람이 검색할 질의 두 개를 만들어라.

규칙
1. keyword 는 조사 없이 띄어 쓴 2~4 낱말의 짧은 검색어다.
2. question 은 물음표로 끝나는 한 문장의 자연스러운 한국어 질문이다.
3. 둘 다 한국어로 쓴다. keyword 에도 한국어 낱말이 하나 이상 들어가야 한다. 영어 약어 · 부품명 · 모델명처럼 원어로 쓰는 전문용어는 그대로 둬도 된다.
4. 카드 제목을 그대로 옮기지 마라. 제목의 낱말을 그대로 늘어놓지 말고, 제목에 없는 말(동의어 · 증상 · 목적 · 상위 개념)을 하나 이상 섞어 사용자가 쓸 법한 말로 바꿔 써라.
5. 카드에 실제로 있는 내용만 묻는다. 이 카드가 답이 되는 질의여야 한다.
6. 출력은 {{"keyword": "...", "question": "..."}} 모양의 JSON 하나뿐이다.

예) 영어 카드 「Thermal interface material selection for high-power AP」 이면
{{"keyword": "AP 방열 소재 선정", "question": "발열이 큰 AP 에 쓸 방열 소재는 어떤 기준으로 고르나?"}}

[카드]
제목: {title}
요약: {summary}
본문 앞부분:
{body}"""


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def _connect(env_file: Path) -> asyncpg.Connection:
    f = _load_env_file(env_file)

    def pick(key: str, default: str = "") -> str:
        return os.environ.get(key) or f.get(key) or default

    return await asyncpg.connect(
        host=pick("POSTGRES_HOST", "127.0.0.1"), port=int(pick("POSTGRES_PORT", "5432")),
        user=pick("POSTGRES_USER"), password=pick("POSTGRES_PASSWORD"), database=pick("POSTGRES_DB"),
        # 이 세션의 모든 트랜잭션이 읽기 전용이다 — 실수로라도 쓰면 DB 가 거절한다.
        server_settings={"default_transaction_read_only": "on", "statement_timeout": "60000"},
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _IMAGE_MARK.sub(" ", text or "")).strip()


def _norm(text: str) -> str:
    return _NORM_DROP.sub("", (text or "").lower())


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def title_overlap(query: str, title: str) -> tuple[float, bool]:
    """(질의 바이그램 중 제목에 있는 몫, 제목 베낌 여부). 공백·문장부호를 지우고 소문자로 맞춘 뒤 잰다."""
    q, t = _norm(query), _norm(title)
    bq = _bigrams(q)
    ratio = len(bq & _bigrams(t)) / len(bq) if bq else 0.0
    copy = bool(q) and (q in t or (len(t) >= 4 and t in q) or ratio >= 0.8)
    return round(ratio, 3), copy


def card_lang(text: str) -> str:
    """한글 음절이 글자(한글+로마자)의 30% 이상이면 ko, 아니면 en."""
    h, lat = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    return "ko" if h and h / (h + lat) >= 0.3 else "en"


async def _card_material(conn: asyncpg.Connection, rid: str) -> dict | None:
    rec = await conn.fetchrow(
        "select id, title, summary, data_type from records where id = $1 and deleted_at is null", rid)
    if rec is None:
        return None
    secs = await conn.fetch(
        "select left(content_text, $2) txt, length(content_text) n, embedding is not null emb "
        "from record_sections where record_id = $1 order by id", rid, BODY_CHARS * 2)
    body, total = [], 0
    for s in secs:
        if total >= BODY_CHARS:
            break
        t = _clean(s["txt"])
        if t:
            body.append(t)
            total += len(t)
    body_text = " ".join(body)[:BODY_CHARS]
    return {
        "record_id": rec["id"], "title": rec["title"] or "", "summary": _clean(rec["summary"])[:SUMMARY_CHARS],
        "data_type": rec["data_type"], "body": body_text,
        "n_sections": len(secs), "n_embedded": sum(1 for s in secs if s["emb"]),
        "body_chars": sum(int(s["n"] or 0) for s in secs),
    }


async def sample_cards(conn: asyncpg.Connection, per_domain: int, seed: int) -> list[dict]:
    rows = await conn.fetch(
        "select id, agents from records where deleted_at is null and cardinality(agents) > 0 order by id")
    by_agent: dict[str, list[str]] = {}
    for r in rows:
        for a in r["agents"] or []:
            by_agent.setdefault(a, []).append(r["id"])
    rng = random.Random(seed)
    chosen: set[str] = set()
    out: list[dict] = []
    for dom in sorted({a.split("-", 1)[0] for a in by_agent}):
        agents = sorted(a for a in by_agent if a.split("-", 1)[0] == dom)
        rng.shuffle(agents)
        pools = {a: rng.sample(by_agent[a], len(by_agent[a])) for a in agents}
        picked = 0
        while picked < per_domain and any(pools.values()):
            for a in agents:
                if picked >= per_domain:
                    break
                while pools[a]:
                    rid = pools[a].pop()
                    if rid in chosen:
                        continue
                    m = await _card_material(conn, rid)
                    if m is None or len(m["body"]) < MIN_BODY_CHARS:
                        continue
                    dup = await conn.fetchval(
                        "select count(*) from records where title = $1 and id <> $2 and deleted_at is null",
                        m["title"], rid)
                    chosen.add(rid)
                    out.append({
                        "record_id": rid, "agent_type": a, "domain": dom, "data_type": m["data_type"],
                        "card_lang": card_lang(" ".join((m["title"], m["summary"], m["body"]))),
                        "n_sections": m["n_sections"], "n_embedded": m["n_embedded"],
                        "body_chars": m["body_chars"], "dup_title": int(dup or 0),
                    })
                    picked += 1
                    break
        print(f"  표본 {dom}: {picked}장 (전문가 {len(agents)}명)", file=sys.stderr)
    return out


def _valid(kind: str, text: str) -> str:
    """빈 문자열이면 통과, 아니면 거절 사유(LLM 에게 되돌려 주는 말이기도 하다)."""
    if not isinstance(text, str) or not text.strip():
        return "비었다"
    if not _HANGUL.search(text):
        return "한국어 낱말이 없다 — 영어 낱말을 한국어로 옮겨 써라(약어 · 모델명만 원어로 둔다)"
    if _HAN.search(text):
        return "한자(중국어)가 섞였다 — 한국어로만 써라"
    if kind == "keyword":
        n = len(text.split())
        if not 2 <= n <= 4:
            return f"낱말이 {n}개다(2~4개여야 한다)"
        if "?" in text:
            return "물음표가 들어 있다"
    else:
        if not text.rstrip().endswith("?"):
            return "물음표로 끝나지 않는다"
        if not 8 <= len(text) <= 150:
            return f"{len(text)}자다(8~150자여야 한다)"
    return ""


def _llm_settings(args: argparse.Namespace) -> tuple[str, str, str]:
    base = (args.llm_base_url or os.environ.get("KOSEARCH_LLM_BASE_URL") or os.environ.get("VLLM_BASE_URL")
            or "http://127.0.0.1:8000/v1").rstrip("/")
    key = os.environ.get("KOSEARCH_LLM_API_KEY") or os.environ.get("VLLM_API_KEY") or "EMPTY"
    model = args.llm_model or os.environ.get("KOSEARCH_LLM_MODEL") or os.environ.get("VLLM_MODEL") or ""
    if not model:
        r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        r.raise_for_status()
        model = r.json()["data"][0]["id"]
    return base, model, key


async def _chat(client: httpx.AsyncClient, base: str, model: str, key: str, messages: list[dict],
                seed: int, temperature: float) -> str:
    r = await client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={
        "model": model, "temperature": temperature, "seed": seed, "max_tokens": 256,
        "response_format": {"type": "json_object"}, "messages": messages,
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def _parse(text: str) -> tuple[str, str]:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        out = {}
    vals = (out.get(k) if isinstance(out, dict) else None for k in ("keyword", "question"))
    kw, qs = (v.strip() if isinstance(v, str) else "" for v in vals)
    return kw, qs


async def generate(client: httpx.AsyncClient, base: str, model: str, key: str, card: dict,
                   seed: int) -> tuple[tuple[str, str, int] | None, str]:
    """((keyword, question, 시도 수) | None, 마지막 사유).

    규칙을 어긴 판은 사유를 붙여 다시 쓰게 한다(작은 모델은 되묻는 편이 시드만 바꾸는 것보다 잘 고친다).
    제목을 베낀 판도 다시 쓰게 하되, 끝까지 베끼면 마지막으로 규칙을 지킨 판을 남긴다(호출부가 title_copy 로 표시한다).
    """
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT.format(
        title=card["title"], summary=card["summary"] or "(없음)", body=card["body"])}]
    best, why = None, ""
    for attempt in range(ATTEMPTS):
        try:
            text = await _chat(client, base, model, key, messages, seed + attempt, 0.4 + 0.2 * attempt)
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            why = f"LLM 오류 {type(exc).__name__}"
            continue
        kw, qs = _parse(text)
        problems = [f"{k} 는 {v}" for k, v in (("keyword", _valid("keyword", kw)),
                                               ("question", _valid("question", qs))) if v]
        if not problems:
            best = (kw, qs, attempt + 1)
            problems = [f"{k} 가 제목을 거의 그대로 옮겼다" for k, t in (("keyword", kw), ("question", qs))
                        if title_overlap(t, card["title"])[1]]
            if not problems:
                break
        why = "; ".join(problems)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"다시 써라 — {why}. 규칙을 모두 지킨 JSON 하나만 내라."}]
    return best, why


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA, help="평가셋을 쓸 곳(기본 이 폴더의 data/)")
    ap.add_argument("--env-file", type=Path, default=ENV_FILE, help="POSTGRES_* 를 읽을 파일")
    ap.add_argument("--per-domain", type=int, default=15, help="도메인마다 고를 카드 수")
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 질의를 만들 카드 수 상한(0=전부)")
    ap.add_argument("--llm-base-url", default="")
    ap.add_argument("--llm-model", default="")
    args = ap.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    cards_path = args.data_dir / "cards.jsonl"
    queries_path = args.data_dir / "queries.jsonl"
    fail_path = args.data_dir / "gen_failures.jsonl"

    conn = await _connect(args.env_file)
    try:
        cards = _read_jsonl(cards_path)
        if not cards:
            print(f"표본을 뽑는다 — 도메인마다 {args.per_domain}장, 시드 {args.seed}", file=sys.stderr)
            cards = await sample_cards(conn, args.per_domain, args.seed)
            for c in cards:
                _append(cards_path, {**c, "seed": args.seed})
            print(f"표본 {len(cards)}장 → {cards_path}", file=sys.stderr)

        done = {q["record_id"] for q in _read_jsonl(queries_path)} | {f["record_id"] for f in _read_jsonl(fail_path)}
        todo = [(i, c) for i, c in enumerate(cards) if c["record_id"] not in done]
        if args.limit:
            todo = todo[: args.limit]
        base, model, key = _llm_settings(args)
        print(f"질의 생성 — 남은 카드 {len(todo)}장, 모델 {model}", file=sys.stderr)

        made = failed = 0
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
            for i, c in todo:
                m = await _card_material(conn, c["record_id"])
                if m is None:
                    _append(fail_path, {"record_id": c["record_id"], "reason": "카드가 사라졌다"})
                    failed += 1
                    continue
                best, why = await generate(client, base, model, key, m, seed=args.seed * 1000 + i * 10)
                if best is None:
                    _append(fail_path, {"record_id": c["record_id"], "reason": why})
                    failed += 1
                    continue
                kw, qs, attempts = best
                for style, text in (("keyword", kw), ("question", qs)):
                    ov, copy = title_overlap(text, m["title"])
                    _append(queries_path, {
                        "qid": f"{c['record_id']}#{style}", "record_id": c["record_id"], "style": style,
                        "query": text, "gold_title": m["title"], "agent_type": c["agent_type"],
                        "domain": c["domain"], "card_lang": c["card_lang"], "n_sections": c["n_sections"],
                        "n_embedded": c["n_embedded"], "dup_title": c["dup_title"],
                        "title_overlap": ov, "title_copy": copy, "gen_model": model, "attempts": attempts,
                    })
                made += 1
                if made % 20 == 0:
                    print(f"  {made}장 ({time.monotonic() - t0:.0f}초)", file=sys.stderr)
        print(f"끝 — 만든 카드 {made}장(질의 {made * 2}개), 실패 {failed}장 → {queries_path}", file=sys.stderr)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
