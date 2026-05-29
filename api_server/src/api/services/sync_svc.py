"""외부 데이터 소스 → AX Hub 정기 pull 동기화 서비스.

운영 흐름:
    1. ``sync_sources`` 행 1개 = 외부 시스템 1개. base_url/list_endpoint/매핑룰 보관.
    2. 외부 cron (또는 사용자) 이 ``POST /api/sync/sources/{id}/run`` 호출.
    3. 본 서비스가 list_endpoint 를 ``since`` (last_sync_at) + ``cursor`` 로
       페이지네이션해 가져옴.
    4. mapping_rules JSON 으로 외부 필드 → AX Hub record 필드 변환.
    5. ``records.import`` 내부 함수로 UPSERT (external_id_map 자동 매핑).
    6. 실패 row 는 ``sync_runs.dead_letter`` 에 저장 → 수동 재시도 가능.

상대측 부담 최소화:
    - cursor 부재: page_size 만큼 since 시간 윈도우로 폴백
    - updated_at 부재: full-list mode (옵션)
    - tombstone 부재: 주기적 full ID set 비교 (옵션)
    - rate limit 헤더 부재: ``max_rps`` 로 자체 throttle
    - pii_masked 부재: ``trust_pii_masked=False`` 면 classification=confidential

매핑 룰 JSON 스펙:
    {
      "id_field": "voc_id",                 # 외부 record 의 id 필드명
      "title_field": "title",
      "body_field": "body_text",
      "tags_fields": ["product_code", "category_path[*]", "channel"],
      "data_type": "DOC",
      "team": "MX",
      "group": "VOC",
      "doc_type": "voc_report",
      "agents": ["market-voc-analyst"],
      "classification": "internal",
      "language": "ko",
      "valid_from_field": "created_at",
      "summary_field": "title",
      "metadata_fields": {"product_name": "product_name"},
      "filter": {"pii_masked": true},        # 이 조건 안 맞으면 reject
      "transform": {
        "severity_to_quality_score": {       # severity → quality_score
          "critical": 100, "major": 75, "minor": 50, "info": 25
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ExternalIdMap, SyncRun, SyncSource
from .url_safety import validate_external_url

logger = logging.getLogger(__name__)


# ===========================================================================
# 매핑 룰 적용
# ===========================================================================
def _get_path(obj: Any, path: str) -> Any:
    """jq-like path 추출: 'a.b[*].c' / 'a.b' / 'a[0]'.

    지원:
        - dot path: a.b.c
        - array index: a[0]
        - array spread: a[*] → 리스트 평탄화
    """
    if not path:
        return obj
    parts: list[str | int | None] = []
    # 매우 단순 파서 — 토큰별로 분해
    buf = ""
    i = 0
    while i < len(path):
        c = path[i]
        if c == ".":
            if buf:
                parts.append(buf); buf = ""
        elif c == "[":
            if buf:
                parts.append(buf); buf = ""
            end = path.index("]", i)
            inner = path[i + 1: end]
            if inner == "*":
                parts.append(None)  # spread sentinel
            else:
                parts.append(int(inner))
            i = end
        else:
            buf += c
        i += 1
    if buf:
        parts.append(buf)

    def _walk(node: Any, remaining: list[str | int | None]) -> Any:
        if not remaining:
            return node
        if node is None:
            return None
        head, tail = remaining[0], remaining[1:]
        if head is None:  # spread
            if not isinstance(node, list):
                return None
            if not tail:
                return list(node)
            # spread 후에 더 가야 함 — 각 원소에 tail 을 적용해 list 로 모음.
            collected: list[Any] = []
            for item in node:
                v = _walk(item, tail)
                if v is None:
                    continue
                if isinstance(v, list):
                    collected.extend(v)
                else:
                    collected.append(v)
            return collected
        if isinstance(head, int):
            if not isinstance(node, list) or head >= len(node):
                return None
            return _walk(node[head], tail)
        if not isinstance(node, dict):
            return None
        return _walk(node.get(head), tail)

    return _walk(obj, parts)


_TEMPLATE_TOKEN_RE = re.compile(r"\{([a-zA-Z0-9_\[\]\*\.]+)(?:\|([a-zA-Z]+)(?::([^}]+))?)?\}")


def _render_template(tmpl: str, raw: dict[str, Any]) -> str:
    """간단 template 치환.

    토큰 형식: ``{path.to.field}`` 또는 ``{path|filter:arg}``.
    지원 필터:
        - truncate:N  — 앞 N 글자만, 초과시 '...' 추가
        - upper / lower
        - default:VAL — 값이 None/빈 문자열이면 VAL 로 치환

    예: ``"{content_original|truncate:80}"`` / ``"{product.code} VOC — {sentiment_label}"``
    """

    def _sub(m: re.Match) -> str:
        path = m.group(1)
        fname = (m.group(2) or "").lower()
        farg = m.group(3) or ""
        v = _get_path(raw, path)
        if v is None or v == "":
            if fname == "default":
                return farg
            return ""
        s = str(v) if not isinstance(v, list) else ", ".join(str(x) for x in v)
        if fname == "truncate":
            try:
                n = int(farg)
                if len(s) > n:
                    s = s[:n] + "..."
            except ValueError:
                pass
        elif fname == "upper":
            s = s.upper()
        elif fname == "lower":
            s = s.lower()
        return s

    return _TEMPLATE_TOKEN_RE.sub(_sub, tmpl)


def _resolve_field(
    raw: dict[str, Any],
    rules: dict[str, Any],
    *,
    field_key: str,
    template_key: str,
) -> str | None:
    """field 또는 template 둘 중 하나로 값 추출. template 가 우선."""
    if template_key in rules:
        rendered = _render_template(str(rules[template_key]), raw)
        return rendered if rendered else None
    if field_key in rules:
        v = _get_path(raw, rules[field_key])
        return str(v) if v not in (None, "") else None
    return None


def transform_record(
    raw: dict[str, Any], rules: dict[str, Any], *, trust_pii: bool
) -> dict[str, Any] | dict[str, str]:
    """외부 record dict → AX Hub record_in dict.

    매핑 룰 키 지원 (AIDATAHUB_CLIENT_SPEC.md §3 일치):
        id_field, title_field|title_template, body_field, summary_field|summary_template,
        tags_fields, tags_prefix, subject_keywords_fields,
        valid_from_field, year_field,
        data_type, team, group, doc_type, agents, classification, language,
        severity_field 또는 sentiment_field 와 transform 체인,
        transform.sentiment_to_severity, transform.severity_to_quality_score,
        filter (key→expected 매칭, 모두 통과해야 함),
        sections_field (passthrough — 본문을 단일 placeholder 대신 원본 sections 트리로 보존)

    반환:
        - 성공: AX Hub 가 받을 record dict (``_external_id`` 포함)
        - 실패: {"_reject_reason": "..."} — 호출자가 dead_letter 에 저장
    """
    # 1. filter 검증
    #
    # 지원 형태:
    #   "pii_masked": true                  → 직접 비교
    #   "require_processed_at": true        → processed_at 이 truthy 여야 함
    #   "skip_when_X": true                 → X 가 truthy 면 reject
    #   "sentiment_label__in": [...]        → 값이 리스트에 포함되어야 함
    #   "country_code__not_in": [...]       → 값이 리스트에 없어야 함
    fltr = rules.get("filter") or {}
    for k, expected in fltr.items():
        if k.startswith("require_") and expected is True:
            check_key = k[len("require_"):]
            actual = _get_path(raw, check_key)
            if not actual:
                return {"_reject_reason": f"filter failed: {check_key} is falsy"}
            continue
        if k.startswith("skip_when_") and expected is True:
            check_key = k[len("skip_when_"):]
            actual = _get_path(raw, check_key)
            if actual:
                return {"_reject_reason": f"filter failed: skipped because {check_key} is truthy"}
            continue
        if k.endswith("__in"):
            check_key = k[: -len("__in")]
            actual = _get_path(raw, check_key)
            if not isinstance(expected, list) or actual not in expected:
                return {"_reject_reason": f"filter failed: {check_key}={actual!r} not in {expected!r}"}
            continue
        if k.endswith("__not_in"):
            check_key = k[: -len("__not_in")]
            actual = _get_path(raw, check_key)
            if isinstance(expected, list) and actual in expected:
                return {"_reject_reason": f"filter failed: {check_key}={actual!r} in disallowed set"}
            continue
        actual = _get_path(raw, k)
        if actual != expected:
            return {"_reject_reason": f"filter failed: {k}={actual!r} != {expected!r}"}

    out: dict[str, Any] = {}

    # 2. external_id (필수)
    id_field = rules.get("id_field") or "id"
    eid = _get_path(raw, id_field)
    if eid is None or eid == "":
        return {"_reject_reason": f"missing external id field '{id_field}'"}
    out["_external_id"] = str(eid)

    # 3. title — field 또는 template
    title = _resolve_field(raw, rules, field_key="title_field", template_key="title_template")
    if not title:
        return {"_reject_reason": "missing title (no title_field/title_template resolved)"}
    out["title"] = title

    # 4. content — sections_field 가 있으면 원본 sections 트리 통째로 사용, 없으면 body_field 단일 section
    sections_field = rules.get("sections_field")
    if sections_field:
        sections_val = _get_path(raw, sections_field)
        if isinstance(sections_val, list) and sections_val:
            out["content"] = {"sections": sections_val}
        else:
            # sections_field 지정됐는데 비어있으면 body_field 폴백
            body = _get_path(raw, rules.get("body_field") or "body_text") or ""
            out["content"] = {"sections": [{
                "section_id": "1", "level": 1, "title": "본문", "content_text": str(body),
            }]}
    else:
        body = _get_path(raw, rules.get("body_field") or "body_text") or ""
        out["content"] = {"sections": [{
            "section_id": "1", "level": 1, "title": "본문", "content_text": str(body),
        }]}

    # 5. tags 누적 (tags_prefix 지원)
    tags_prefix = rules.get("tags_prefix") or {}
    tags: list[str] = []
    for tf in rules.get("tags_fields") or []:
        v = _get_path(raw, tf)
        # tags_prefix 의 키는 path expression. 매칭되면 prefix 추가.
        prefix = ""
        for pfx_key, pfx_val in tags_prefix.items():
            if pfx_key == tf:
                prefix = str(pfx_val)
                break
        if isinstance(v, list):
            for x in v:
                if x:
                    tags.append(f"{prefix}{x}")
        elif v:
            tags.append(f"{prefix}{v}")
    seen: set[str] = set()
    out["tags"] = [t for t in tags if not (t in seen or seen.add(t))][:30]

    # 6. 분류 키
    for k in ("data_type", "team", "group", "doc_type", "classification", "language"):
        if k in rules:
            out[k] = rules[k]
    if "data_type" not in out:
        out["data_type"] = "DOC"
    if "classification" not in out:
        out["classification"] = "internal"

    # 7. pii 보호
    if not trust_pii:
        out["classification"] = "confidential"

    # 8. agents
    if "agents" in rules:
        out["agents"] = list(rules["agents"])

    # 9. summary — field 또는 template
    summary = _resolve_field(raw, rules, field_key="summary_field", template_key="summary_template")
    if summary:
        out["summary"] = summary[:500]

    # 10. valid_from
    if "valid_from_field" in rules:
        vf = _get_path(raw, rules["valid_from_field"])
        if vf:
            try:
                out["valid_from"] = str(vf)[:10]
            except Exception:
                pass

    # 11. severity → quality_score (2단계 체인 지원)
    #   case A: severity_field + transform.severity_to_quality_score (직접)
    #   case B: sentiment_field + transform.sentiment_to_severity → transform.severity_to_quality_score (2단)
    tx = rules.get("transform") or {}
    sev_qs = tx.get("severity_to_quality_score") or {}
    severity_value: str | None = None
    if rules.get("severity_field"):
        sv = _get_path(raw, rules["severity_field"])
        if isinstance(sv, str):
            severity_value = sv
    elif rules.get("sentiment_field"):
        sentiment = _get_path(raw, rules["sentiment_field"])
        sentiment_map = tx.get("sentiment_to_severity") or {}
        if isinstance(sentiment, str) and sentiment in sentiment_map:
            severity_value = str(sentiment_map[sentiment])
    if severity_value and sev_qs and severity_value in sev_qs:
        try:
            out["quality_score"] = int(sev_qs[severity_value])
        except Exception:
            pass

    # 12. subject_keywords
    kw: list[str] = []
    for k in rules.get("subject_keywords_fields") or []:
        v = _get_path(raw, k)
        if isinstance(v, list):
            kw.extend(str(x) for x in v if x)
        elif v:
            kw.append(str(v))
    if kw:
        seen_kw: set[str] = set()
        out["subject_keywords"] = [k for k in kw if not (k in seen_kw or seen_kw.add(k))][:30]

    # 13. year
    yr_field = rules.get("year_field")
    if yr_field:
        v = _get_path(raw, yr_field)
        if v:
            try:
                out["year"] = int(str(v)[:4])
            except Exception:
                pass
    if "year" not in out:
        # UTC 기준 — 호스트 timezone 의존성 제거
        out["year"] = datetime.now(timezone.utc).year

    # 14. attachments (MXWP image refs 등) — record 안에 메타로 보존.
    #     실제 첨부 파일 적재는 별도 endpoint(/attachments) 에서 다루고,
    #     본 transform 단계에서는 record.content.attachments_meta 에 흔적 남김.
    att_field = rules.get("attachments_field")
    if att_field:
        atts = _get_path(raw, att_field)
        if isinstance(atts, list) and atts:
            url_template = rules.get("attachment_url_template")
            mode = rules.get("attachment_mode") or "url_ref"
            meta_atts: list[dict[str, Any]] = []
            for a in atts:
                if not isinstance(a, dict):
                    continue
                item = {"id": a.get("image_id") or a.get("id"), "kind": a.get("kind")}
                if url_template and isinstance(url_template, str):
                    item["url"] = _render_template(url_template, a)
                elif a.get("url"):
                    item["url"] = a["url"]
                meta_atts.append(item)
            if meta_atts:
                out.setdefault("content", {}).setdefault("attachments_meta", meta_atts)
                out.setdefault("content", {})["attachment_mode"] = mode

    return out


# ===========================================================================
# dead_letter PII / 크기 redaction
# ===========================================================================
_DEAD_LETTER_MAX_STRING = 200
_DEAD_LETTER_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd",
    "ssn", "credit_card", "card_number", "cvv",
    "phone", "phone_number", "tel",
    "email", "address", "addr",
    "auth_token", "api_key", "secret",
})


def _redact_for_dead_letter(obj: Any, depth: int = 0) -> Any:
    """저장 전 본문 큰 텍스트 잘라내기 + 명시적 sensitive key 마스킹."""
    if depth > 5:
        return "<max-depth>"
    if isinstance(obj, str):
        return obj if len(obj) <= _DEAD_LETTER_MAX_STRING else (obj[:_DEAD_LETTER_MAX_STRING] + "...<truncated>")
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in _DEAD_LETTER_SENSITIVE_KEYS):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_for_dead_letter(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact_for_dead_letter(x, depth + 1) for x in list(obj)[:20]]
    return obj


# ===========================================================================
# Throttle (자체 rate limit)
# ===========================================================================
class _Throttle:
    """간단 sleep 기반 throttle. max_rps=2 면 최소 0.5s 간격."""

    def __init__(self, max_rps: float):
        self.interval = 1.0 / max(0.1, max_rps)
        self._last = 0.0

    async def wait(self):
        now = time.monotonic()
        gap = now - self._last
        if gap < self.interval:
            await asyncio.sleep(self.interval - gap)
        self._last = time.monotonic()


# ===========================================================================
# 외부 API 호출
# ===========================================================================
async def _fetch_page(
    client: httpx.AsyncClient,
    src: SyncSource,
    *,
    since: str | None,
    cursor: str | None,
) -> dict[str, Any]:
    """list_endpoint 1페이지 호출.

    응답 정규화:
        {items: [...], next_cursor: str | None}
    """
    params: dict[str, Any] = {src.limit_param: src.page_size}
    # since_param 이 '_unsupported_' 접두로 시작하면 (예: MXWP) — 명시적
    # "서버가 받지 않음" 마커 — 보내지 마라.
    if since and not (src.since_param or "").startswith("_unsupported"):
        params[src.since_param] = since
    if cursor:
        params[src.cursor_param] = cursor

    # mapping_rules.list_filter — query string 으로 그대로 주입 (예: {"status":"published"})
    rules = src.mapping_rules or {}
    list_filter = rules.get("list_filter") or {}
    if isinstance(list_filter, dict):
        for k, v in list_filter.items():
            # 우리가 이미 점유한 표준 파라미터와 충돌 회피
            if k in (src.limit_param, src.cursor_param, src.since_param):
                continue
            params[k] = v

    headers: dict[str, str] = {}
    if src.api_key:
        headers[src.auth_header] = src.api_key

    # list_endpoint sanity — '/' 로 시작해야 하고 url-prefix 토큰 없어야 함
    le = src.list_endpoint or ""
    if not le.startswith("/"):
        raise httpx.RequestError(
            f"list_endpoint must start with '/' (got {le!r}) — bare host or absolute URL not allowed"
        )
    if "://" in le or le.startswith("@") or le.startswith("//"):
        raise httpx.RequestError(f"list_endpoint contains url-prefix tokens: {le!r}")

    # DNS rebinding 완화 — 매 fetch 직전 base_url 재검증 (defense-in-depth).
    # 완전한 IP-pinning 은 transport 레벨이지만, 이 단계에서도 두 번째 resolve
    # 결과가 private/metadata 로 바뀌면 일찍 차단된다.
    ok, reason = validate_external_url(src.base_url)
    if not ok:
        raise httpx.RequestError(f"base_url revalidate failed: {reason}")

    for attempt in range(src.retry_max + 1):
        try:
            resp = await client.request(
                src.list_method,
                src.base_url.rstrip("/") + src.list_endpoint,
                params=params,
                headers=headers,
                timeout=30.0,
            )
            # 429 / 503 — Retry-After 헤더 우선 처리
            if resp.status_code in (429, 503) and attempt < src.retry_max:
                ra_raw = resp.headers.get("Retry-After")
                wait = src.retry_backoff_sec * (2 ** attempt)
                if ra_raw:
                    try:
                        wait = max(wait, float(ra_raw))
                    except ValueError:
                        # HTTP-date 형식은 단순화 — 기본 backoff 유지
                        pass
                logger.info(
                    "rate limited (status=%s) — waiting %ss before retry %s/%s",
                    resp.status_code, wait, attempt + 1, src.retry_max,
                )
                await asyncio.sleep(min(wait, 60.0))
                continue
            resp.raise_for_status()
            body = resp.json()
            # 응답 정규화 — 4가지 형식 허용:
            #   1. {items: [...], next_cursor: ...}
            #   2. [item, item, ...]
            #   3. {data: [...], meta: {next_offset: N}} (MXWP envelope)
            #   4. {results|data: [...]} (offset 모드 — meta 없으면 종료)
            if isinstance(body, list):
                return {"items": body, "next_cursor": None}
            if isinstance(body, dict):
                items = body.get("items") or body.get("results") or body.get("data") or []
                # next_offset 우선 (offset 모드), 없으면 next_cursor (cursor 모드)
                meta = body.get("meta") or {}
                next_off = meta.get("next_offset")
                if next_off is not None:
                    return {"items": items, "next_cursor": str(next_off)}
                nc = body.get("next_cursor") or body.get("cursor") or None
                return {"items": items, "next_cursor": nc}
            return {"items": [], "next_cursor": None}
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            if attempt < src.retry_max:
                logger.info(
                    "fetch retry %s/%s after %s: %s",
                    attempt + 1, src.retry_max, type(exc).__name__, exc,
                )
                await asyncio.sleep(src.retry_backoff_sec * (2 ** attempt))
                continue
            raise

    raise RuntimeError("unreachable: retry loop exited without success or raise")


# ===========================================================================
# 메인: run_sync
# ===========================================================================
async def run_sync(
    session: AsyncSession,
    source_id: int,
    *,
    trigger: str = "manual",
    dry_run: bool = False,
    max_pages: int = 50,
) -> dict[str, Any]:
    """sync_source 1개를 실행. sync_runs 행을 생성하고 결과를 적재.

    Args:
        source_id: sync_sources.id
        trigger: 'manual' | 'cron' | 'webhook'
        dry_run: True 면 매핑 검증만, import 안 함
        max_pages: 1 회 run 의 페이지 한도 (안전장치)

    Returns:
        {run_id, status, fetched, imported, failed, ...}
    """
    src = (await session.execute(select(SyncSource).where(SyncSource.id == source_id))).scalar_one_or_none()
    if src is None:
        raise ValueError(f"sync_source not found: id={source_id}")
    if not src.enabled and trigger != "manual":
        raise ValueError(f"sync_source disabled: name={src.name}")

    # SSRF 방지 (defense-in-depth) — DB 에 저장된 URL 도 매 실행시 재검증
    ok, reason = validate_external_url(src.base_url)
    if not ok:
        raise ValueError(f"sync_source base_url unsafe: {reason}")

    # 동시 실행 차단 — 같은 source_id 의 run 이 이미 진행 중이면 거부.
    # 1) DB 의 sync_runs.status='running' 검사 (단순/이식성)
    # 2) PG 환경에서는 advisory lock 으로 race-free 강화
    running = (
        await session.execute(
            select(SyncRun.id).where(
                (SyncRun.source_id == source_id) & (SyncRun.status == "running")
            ).limit(1)
        )
    ).scalar_one_or_none()
    if running is not None:
        raise ValueError(
            f"sync_source busy: another run is in progress (run_id={running}). "
            "Wait for it to finish or mark it failed manually."
        )
    # advisory lock (best-effort — non-PG 에서는 무시)
    try:
        from sqlalchemy import text as _text

        dialect = session.bind.dialect.name if session.bind else ""
        if dialect == "postgresql":
            # pg_try_advisory_lock returns boolean — if False, another session holds it
            acquired = (
                await session.execute(
                    _text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                    {"k1": 0x41584834, "k2": source_id},  # 'AXH4' magic + source_id
                )
            ).scalar()
            if not acquired:
                raise ValueError(
                    f"sync_source busy: advisory lock held by another worker (source_id={source_id})"
                )
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover — best-effort lock
        logger.debug("advisory lock skipped: %s", exc)

    # SyncRun 행 생성
    run = SyncRun(
        source_id=src.id,
        trigger=trigger,
        cursor_before=src.cursor,
        status="running",
    )
    session.add(run)
    await session.flush()  # id 확보
    await session.commit()
    logger.info("sync_run started: source=%s run_id=%s trigger=%s", src.name, run.id, trigger)

    throttle = _Throttle(src.max_rps)
    rules = dict(src.mapping_rules or {})
    fetched = imported = updated_cnt = failed = 0
    dead_letter: list[dict[str, Any]] = []
    next_cursor = src.cursor
    since_iso = src.last_sync_at.isoformat() if src.last_sync_at else None
    error_str: str | None = None
    started_now = datetime.now(timezone.utc)
    drained = False  # True 면 모든 페이지 끝까지 다 가져옴 — cursor 리셋 가능

    try:
        async with httpx.AsyncClient() as client:
            for page in range(max_pages):
                await throttle.wait()
                page_data = await _fetch_page(
                    client, src, since=since_iso, cursor=next_cursor
                )
                items = page_data.get("items") or []
                nc_peek = page_data.get("next_cursor")
                fetched += len(items)
                if not items:
                    # Server-side filtered list 가 빈 페이지를 반환하더라도
                    # next_cursor 가 있으면 후속 페이지 시도. cursor 도 없으면
                    # 진짜 끝.
                    if nc_peek:
                        next_cursor = nc_peek
                        continue
                    break

                # 매핑 + import (페이지 단위)
                from ..routes.records import _import_one

                for raw in items:
                    if not isinstance(raw, dict):
                        failed += 1
                        dead_letter.append({"raw": str(raw)[:200], "error": "not a dict"})
                        continue
                    transformed = transform_record(
                        raw, rules, trust_pii=bool(src.trust_pii_masked)
                    )
                    if "_reject_reason" in transformed:
                        failed += 1
                        # PII 마스킹: 큰 텍스트/긴 필드 잘라내기 + sentitive key 명시 차단
                        dead_letter.append({
                            "raw": _redact_for_dead_letter(raw),
                            "error": transformed["_reject_reason"],
                        })
                        continue
                    if dry_run:
                        imported += 1
                        continue
                    outcome = await _import_one(
                        session,
                        raw=transformed,
                        auto_seq=True,
                        dry_run=False,
                        actor=f"sync:{src.name}",
                        request_id=None,
                        external_source=src.name,
                    )
                    if outcome.get("error"):
                        failed += 1
                        dead_letter.append({
                            "raw": _redact_for_dead_letter(raw),
                            "transformed": {
                                "_external_id": transformed.get("_external_id"),
                                "title": (transformed.get("title") or "")[:120],
                                "doc_type": transformed.get("doc_type"),
                            },
                            "error": outcome["error"],
                        })
                    else:
                        if outcome.get("action") == "updated":
                            updated_cnt += 1
                        else:
                            imported += 1

                # 다음 페이지 cursor
                nc = page_data.get("next_cursor")
                if not nc:
                    next_cursor = nc
                    drained = True
                    break
                next_cursor = nc
            else:
                # for-else: max_pages 도달 — drained False 유지
                logger.warning(
                    "sync_source %s hit max_pages=%s before draining — cursor NOT persisted "
                    "to avoid offset drift on next run. Increase max_pages or run again.",
                    src.name, max_pages,
                )

        # 성공 — sync_source 상태 갱신
        if not dry_run:
            # offset 모드 (cursor_param='offset') 는 mid-sync 업데이트로
            # 페이지 경계가 흔들려 drift 위험. drained 도 무관하게 매번 cursor
            # 리셋 → 다음 run 은 처음(offset=0)부터. (UPSERT 라 중복 비용만
            # 늘 뿐 데이터 손상 X.)
            # 진짜 cursor 모드 (cursor_param='cursor' 또는 'id-token') 는
            # drained 시에만 None (정상 종료), drained 아니면 stale cursor 보존
            # X — 다음 run 도 처음부터.
            offset_mode = (src.cursor_param or "").lower() == "offset"
            if offset_mode or not drained:
                src.cursor = None
            else:
                src.cursor = next_cursor
            src.last_sync_at = started_now
            src.last_status = "ok" if failed == 0 else "partial"
            src.last_fetched_count = fetched
            src.last_imported_count = imported + updated_cnt
            src.last_error = None

        run.status = "ok" if failed == 0 else "partial"

    except Exception as exc:
        error_str = f"{type(exc).__name__}: {exc}"
        logger.exception("sync_run failed: source=%s", src.name)
        run.status = "error"
        run.error = error_str
        if not dry_run:
            src.last_status = "error"
            src.last_error = error_str
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.fetched_count = fetched
        run.imported_count = imported
        run.updated_count = updated_cnt
        run.failed_count = failed
        run.cursor_after = next_cursor
        # dead_letter truncation 이 silent loss 가 되지 않도록 카운트와 함께
        # 별도 컬럼 같은 게 없으므로 첫 entry 에 'truncated' 마커를 박는다.
        kept = dead_letter[:200]
        if len(dead_letter) > 200:
            kept.insert(0, {
                "_truncated": True,
                "total_dead_letter": len(dead_letter),
                "kept": 200,
                "note": "dead_letter truncated. See server logs for full count.",
            })
            logger.warning(
                "sync_run %s dead_letter truncated: %s → 200",
                run.id, len(dead_letter),
            )
        run.dead_letter = kept

        # advisory lock 해제 (best-effort)
        try:
            from sqlalchemy import text as _text

            dialect = session.bind.dialect.name if session.bind else ""
            if dialect == "postgresql":
                await session.execute(
                    _text("SELECT pg_advisory_unlock(:k1, :k2)"),
                    {"k1": 0x41584834, "k2": source_id},
                )
        except Exception:
            pass

        await session.commit()

    return {
        "run_id": run.id,
        "source_name": src.name,
        "status": run.status,
        "fetched": fetched,
        "imported": imported,
        "updated": updated_cnt,
        "failed": failed,
        "cursor_before": run.cursor_before,
        "cursor_after": run.cursor_after,
        "dry_run": dry_run,
        "error": error_str,
        "dead_letter_count": len(dead_letter),
    }


__all__ = ["run_sync", "transform_record"]
