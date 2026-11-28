# 변환 결과의 그림을 VLM(OpenAI 호환)으로 판독해 캡션 · 본문 블록에 싣는 단계 — 박스 설정이 있을 때만(기본 꺼짐)
"""그림 판독(HWAXPortal docs/request-1125 IR-31).

변환기는 그림을 파일로 꺼낼 뿐 내용을 글로 옮기지 않아, 캡션이 넣은 파일 이름('Figure 1: t1_bar.png')이고 지식 DB 에 그림 내용이
한 글자도 들어가지 않았다. 이 단계는 꺼낸 그림마다 VLM 에 판독을 청해

- 캡션 — 파일 이름 · 기본 글('슬라이드 N 이미지')이면 판독 요약으로 바꾸고, 사람이 단 캡션이면 뒤에 요약을 붙인다(첨부 캡션도 같게).
- 본문 — 그 그림 블록 바로 뒤에 판독 전문을 글 블록(``[그림 판독 — Figure N] …``)으로 넣는다 — 절 본문(record_sections)이 되어
  검색이 그림 속 값을 찾는다.
- 그림 칸 ``reading`` · ``meta.figure_reading`` — 요약 · 모델 · 지시 판과 셈(읽음 · 실패 · 건너뜀 · 까닭).

지시문은 DocForge 의 그림 판독(df-vlm-3 FIGURE — 첫 줄 '종류:', 둘째 줄 '요약:')과 같은 말이다 — 같은 모델이면 두 길의 판독을 견줄 수
있다. 둘째 판독(차트 값 · 표 칸)은 하지 않는다(쪽마다 한 번 — 챗과 나눠 쓰는 서버다).

설정(박스 ``deploy/apptainer/.env`` — 바꾸면 API 재기동)
    AIDH_VLM_BASE_URL     OpenAI 호환 끝점(예 http://<서버>/v1). 비면 이 단계 전체가 꺼진다(기본).
    AIDH_VLM_MODEL        모델 이름. 비면 꺼진다.
    AIDH_VLM_API_KEY      키(없으면 'EMPTY'). 로그 · 결과에 싣지 않는다.
    AIDH_VLM_TIMEOUT_S    그림 하나의 시간 한도(기본 180).
    AIDH_VLM_MAX_CONCURRENCY  이 프로세스에서 동시에 보내는 판독 수(기본 2 — 변환이 여럿이어도 합쳐서).
    AIDH_VLM_MAX_FIGURES  문서 하나에서 판독할 그림 수 상한(기본 40 — 넘는 그림은 건너뛰고 셈에 적는다).
    AIDH_VLM_THINKING_OFF 생각 끄기를 보낼지(기본 1 — 챗 모델. 심의 모델 끝점이면 0).

실패는 그 그림만 판독 없이 둔다(변환을 죽이지 않는다). 그림을 못 받는 서버(no_vision)면 나머지를 건너뛴다. 키 · 주소 · 그림을 로그 ·
결과에 싣지 않는다 — 까닭은 종류(unreachable · timeout · http_5xx · http_4xx · no_vision · bad_body · format · cap)만.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROMPT_VERSION = "aidh-vlm-1"          # DocForge df-vlm-3 FIGURE 와 같은 말 — 바꾸면 판을 올린다
MAX_BYTES = 8 * 1024 * 1024
LONG_SIDE = 2048

SYSTEM = ("당신은 공학 그림 판독기입니다. 그림에 보이는 것만 글로 옮깁니다 — 지어내지 않습니다. 한국어로 씁니다(기술 용어의 영문 "
          "원어 병기는 괜찮다).")
_KINDS = ("그래프", "표", "지도", "CAD", "도면", "사진", "글", "기타")
FIGURE = ("첫 줄에는 '종류: 그래프' 처럼 그림 종류 하나만 적어라(" + " · ".join(_KINDS) + "). 둘째 줄에는 '요약: ' 다음에 무엇이 어떤 값인지 "
          "한 줄로 적어라(꼴 — '요약: 막대 4개 — B 가 82 로 가장 높다'). 그다음 이 그림을 판독하라 — ① 그림 종류(그래프 · 표 · 응력/변형 지도 · "
          "CAD 화면 · 도면 · 사진) ② 축 이름 · 단위 · 범위 ③ 범례 · 색 막대의 값 범위 ④ 읽을 수 있는 수치 전부(최댓값 · 최솟값과 그 위치, "
          "표의 칸) ⑤ 그림 안의 글자 그대로. 읽을 수 없는 것은 '판독 불가' 라고 적어라.")

_KIND_LINE = re.compile(r"^[#*\-\s]*종류\s*[:：]?\s*\**\s*([^\s*'\".,]+)")
_SUMMARY_LINE = re.compile(r"^[#*\-\s]*요약\s*[:：]\s*\**\s*(.+?)[\s*]*$")
_NONE = {"없음", "해당 없음", "해당없음", "없다", "none", "n/a", "na", "-"}
# 캡션 접두('Figure 3:' · '그림 3.') 뒤가 이 꼴이면 사람이 단 캡션이 아니다 — 판독 요약으로 바꾼다
_CAP_PREFIX = re.compile(r"^\s*((?:figure|fig\.?|그림)\s*\d+\s*[:.])\s*", re.IGNORECASE)
_PLAIN_CAP = re.compile(r"^(?:[\w\-. ()]+\.(?:png|jpe?g|gif|bmp|tiff?|emf|wmf|svg|webp)|슬라이드 \S+ 이미지|\(캡션 누락[^)]*\)|)$",
                        re.IGNORECASE)

_SEM_LOCK = threading.Lock()
_SEM: dict[int, threading.BoundedSemaphore] = {}


class ReadError(RuntimeError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class VlmSettings:
    base_url: str
    model: str
    key: str
    timeout_s: float
    max_concurrency: int
    max_figures: int
    thinking_off: bool


def _int(name: str, default: int, lo: int) -> int:
    try:
        return max(lo, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def settings() -> VlmSettings | None:
    """박스 설정 — 끝점이나 모델이 비면 None(이 단계가 꺼진다)."""
    base = (os.environ.get("AIDH_VLM_BASE_URL") or "").strip()
    model = (os.environ.get("AIDH_VLM_MODEL") or "").strip()
    if not base or not model:
        return None
    try:
        timeout = max(5.0, float(os.environ.get("AIDH_VLM_TIMEOUT_S", "") or 180))
    except ValueError:
        timeout = 180.0
    return VlmSettings(base_url=base.rstrip("/"), model=model, key=(os.environ.get("AIDH_VLM_API_KEY") or "").strip(),
                       timeout_s=timeout, max_concurrency=_int("AIDH_VLM_MAX_CONCURRENCY", 2, 1),
                       max_figures=_int("AIDH_VLM_MAX_FIGURES", 40, 0),
                       thinking_off=(os.environ.get("AIDH_VLM_THINKING_OFF", "1").strip() not in ("0", "false", "no")))


def _sem(n: int) -> threading.BoundedSemaphore:
    with _SEM_LOCK:
        if n not in _SEM:
            _SEM[n] = threading.BoundedSemaphore(n)
        return _SEM[n]


# ── 판독문 다루기(DocForge vision/prompts.py 와 같은 규칙) ─────────────────────
def figure_summary(text: str) -> str | None:
    """판독문 앞머리(내용 줄 다섯 안)의 '요약: …' 한 줄. 없으면 None."""
    for line in [ln for ln in (text or "").splitlines() if ln.strip()][:5]:
        m = _SUMMARY_LINE.match(line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def reading_lines(text: str) -> list[str]:
    """판독문 → 본문에 싣는 내용 줄 — '종류:' 줄 · '요약:' 줄(캡션이 된다) · 내용이 '없음' 인 줄을 뺀다. '판독 불가' 는 남긴다."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _KIND_LINE.match(line) or _SUMMARY_LINE.match(line):
            continue
        body = line.lstrip("-*•①②③④⑤ ").strip()
        cut = min((i for i in (body.find(":"), body.find("：")) if i >= 0), default=-1)
        content = (body[cut + 1:] if cut >= 0 else body).strip().strip(".。").strip().lower()
        if content in _NONE:
            continue
        out.append(line)
    return out


def _fit(blob: bytes) -> tuple[bytes, str]:
    """PNG · JPEG 로 — 8MiB · 긴 변 2048px 를 넘으면 줄인다. PIL 이 못 여는 그림(EMF · WMF …)은 ReadError('format')."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as im:
            fmt = im.format
            if fmt in ("PNG", "JPEG") and len(blob) <= MAX_BYTES and max(im.size) <= LONG_SIDE:
                return blob, "image/png" if fmt == "PNG" else "image/jpeg"
            im.seek(0)
            im.thumbnail((LONG_SIDE, LONG_SIDE))
            buf = io.BytesIO()
            if fmt == "JPEG":
                im.convert("RGB").save(buf, format="JPEG", quality=90)
                return buf.getvalue(), "image/jpeg"
            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.save(buf, format="PNG")
            return buf.getvalue(), "image/png"
    except Exception as exc:  # noqa: BLE001 — 열 수 없는 형식 · 깨진 그림
        raise ReadError("format") from exc


def read_one(client: Any, cfg: VlmSettings, blob: bytes) -> str:
    """그림 하나 → 판독문. 실패는 ReadError(종류)."""
    import httpx

    data, mime = _fit(blob)
    body: dict[str, Any] = {
        "model": cfg.model, "temperature": 0, "stream": False,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": [
                         {"type": "text", "text": FIGURE},
                         {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}]}],
    }
    if cfg.thinking_off:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    with _sem(cfg.max_concurrency):
        try:
            r = client.post(f"{cfg.base_url}/chat/completions", json=body, timeout=cfg.timeout_s,
                            headers={"Authorization": f"Bearer {cfg.key or 'EMPTY'}"})
        except httpx.TimeoutException as exc:
            raise ReadError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ReadError("unreachable") from exc
    if r.status_code >= 500:
        raise ReadError("http_5xx")
    if r.status_code >= 400:
        msg = r.text.lower()
        raise ReadError("no_vision" if any(w in msg for w in ("multimodal", "image", "vision")) else "http_4xx")
    try:
        text = r.json()["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ReadError("bad_body") from exc
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.strip()
    if not text:
        raise ReadError("bad_body")
    return text


# ── 변환 결과에 싣기 ──────────────────────────────────────────────────────
def _walk(sections: list[Any]):
    for sec in sections or []:
        if isinstance(sec, dict):
            yield sec
            yield from _walk(sec.get("children") or [])


def _caption(old: str, number: Any, summary: str) -> str:
    m = _CAP_PREFIX.match(old or "")
    rest = (old or "")[m.end():].strip() if m else (old or "").strip()
    head = m.group(1) if m else f"Figure {number}:"
    if _PLAIN_CAP.match(rest):
        return f"{head} {summary}"
    return f"{head} {rest} — {summary}"


def apply(payload: dict[str, Any], output_dir: Path, *, cfg: VlmSettings | None = None, client: Any = None) -> dict[str, Any]:
    """``payload`` (DOC 변환 결과)의 그림을 판독해 제자리에서 고친다. 설정이 없거나 그림이 없으면 그대로 돌려준다."""
    cfg = cfg or settings()
    figures = [f for f in (payload.get("figures") or []) if isinstance(f, dict)]
    if cfg is None or not figures:
        return payload
    stats: dict[str, Any] = {"model": cfg.model, "prompt_version": PROMPT_VERSION, "figures": len(figures), "read": 0,
                             "failed": 0, "skipped": 0, "reasons": {}}

    def note(kind: str, key: str) -> None:
        stats[key] += 1
        stats["reasons"][kind] = stats["reasons"].get(kind, 0) + 1

    # 읽을 그림 — 파일이 있는 것, 같은 그림은 한 번만(sha256)
    jobs: dict[str, bytes] = {}
    sha_of: dict[str, str] = {}
    for i, fig in enumerate(figures):
        path = fig.get("image_path")
        blob = b""
        if isinstance(path, str) and path:
            try:
                blob = (Path(output_dir) / path).read_bytes()
            except OSError:
                blob = b""
        if not blob:
            note("no_file", "skipped")
            continue
        sha = hashlib.sha256(blob).hexdigest()
        if sha not in jobs and len(jobs) >= cfg.max_figures:
            note("cap", "skipped")
            continue
        jobs.setdefault(sha, blob)
        sha_of[str(fig.get("id"))] = sha

    results: dict[str, str | ReadError] = {}
    if jobs:
        import httpx

        stop = threading.Event()

        def run(item: tuple[str, bytes]) -> tuple[str, str | ReadError]:
            sha, blob = item
            if stop.is_set():
                return sha, ReadError("no_vision")
            try:
                return sha, read_one(http, cfg, blob)
            except ReadError as exc:
                if exc.kind == "no_vision":
                    stop.set()
                return sha, exc

        own = client is None
        http = client or httpx.Client()
        try:
            with ThreadPoolExecutor(max_workers=cfg.max_concurrency) as pool:
                results = dict(pool.map(run, list(jobs.items())))
        finally:
            if own:
                http.close()

    by_sec = {str(s.get("id")): s for s in _walk(payload.get("sections") or [])}
    atts = [a for a in (payload.get("attachments") or []) if isinstance(a, dict)]
    for fig in figures:
        sha = sha_of.get(str(fig.get("id")))
        if sha is None:
            continue
        got = results.get(sha)
        if isinstance(got, ReadError) or got is None:
            note(got.kind if isinstance(got, ReadError) else "missing", "failed")
            continue
        lines = reading_lines(got)
        summary = figure_summary(got) or (lines[0][:80] if lines else "")
        if not summary and not lines:
            note("empty", "failed")
            continue
        stats["read"] += 1
        label = f"Figure {fig.get('number')}"
        if summary:
            fig["caption"] = _caption(str(fig.get("caption") or ""), fig.get("number"), summary)
            for a in atts:
                if (a.get("extra") or {}).get("figure_ref") == fig.get("id"):
                    a["caption"] = fig["caption"]
        fig["reading"] = {"summary": summary, "model": cfg.model, "prompt_version": PROMPT_VERSION}
        text = f"[그림 판독 — {label}] " + "\n".join(lines or [summary])
        sec = by_sec.get(str(fig.get("section_ref")))
        if sec is not None:
            blocks = sec.setdefault("blocks", [])
            at = next((i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "figure"
                       and b.get("ref") == fig.get("id")), None)
            block = {"type": "paragraph", "text": text}
            if at is None:
                blocks.append(block)
            else:
                blocks.insert(at + 1, block)
    if not isinstance(payload.get("meta"), dict):
        payload["meta"] = {}
    payload["meta"]["figure_reading"] = stats
    if stats["failed"]:
        log.warning("그림 판독 — %d/%d 개 실패(%s)", stats["failed"], stats["figures"], stats["reasons"])
    return payload
