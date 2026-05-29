"""LLM 친화 데이터 입력 키트 (zip) 생성.

목적:
    사용자가 자기 LLM(사내 Llama / Claude / ChatGPT) 에 "이 세트에 맞춰
    JSON 만들어줘" 하고 던질 수 있게, 한 zip 안에:
        - LLM 에 줄 시스템 프롬프트 (markdown)
        - 머신 리더블 JSON Schema
        - **의존성 없는 self-contained validate.py** (사용자 PC 에서 검증)
        - 완성 예시 3종
        - README (사람용)
    를 자동 조립해 내려보낸다.

    핵심: validate.py 안에 enum / 등록 agent / 등록 doc_type / agent expected
    가 **이 시점 데이터로 하드코딩**되어 박힌다. 사용자 PC 는 네트워크 없이
    검증 가능. 의존성도 Python 표준 라이브러리만.

호출 진입점:
    ``build_ingest_kit_zip(session, agent_type=None) -> bytes``
"""
from __future__ import annotations

import io
import json
import textwrap
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, DocType
from . import discover_svc, ingest_guide_svc


async def build_ingest_kit_zip(
    session: AsyncSession, *, agent_type: str | None = None
) -> tuple[bytes, str]:
    """Ingest Kit zip 빌드.

    Returns:
        ``(zip_bytes, filename)`` — filename 은 agent_type 반영.
    """
    guide = await ingest_guide_svc.build_guide(session, agent_type=agent_type)
    schema = discover_svc.build_json_schema()

    # 검증 스크립트에 박을 정보 수집
    enums = guide["enums"]
    doc_types = [d["code"] for d in guide["doc_types"]]
    # doc_type → mode 매핑 (validate.py 에서 mode 별 경고 차별화)
    doc_type_modes = {
        d["code"]: d.get("mode", "llm_context") for d in guide["doc_types"]
    }
    agents_all = [a["agent_type"] for a in guide["agents"]]

    expected: dict[str, Any] = {}
    if agent_type:
        # 단일 agent 가이드 → expected 채움
        target = next(
            (a for a in guide["agents"] if a["agent_type"] == agent_type),
            None,
        )
        if target is None:
            # agent_type 이 지정됐는데 등록이 없으면 전체 검증으로 폴백
            expected = {}
        else:
            expected = {
                "agent_type": target["agent_type"],
                "required_doc_type": target["required_doc_type"],
                "required_tags": list(target["required_tags"] or []),
                "excluded_tags": list(target["excluded_tags"] or []),
            }

    validate_py = _render_validate_py(
        enums=enums,
        doc_types=doc_types,
        doc_type_modes=doc_type_modes,
        agents_all=agents_all,
        expected=expected,
    )

    readme_md = _render_readme(agent_type=agent_type, expected=expected)

    # examples
    single_ex = next(
        (ex["value"] for ex in guide["examples"] if "단건 — 명시 id" in ex["title"]),
        {"id": "DOC-HE-CAE-2026-0000000001", "title": "예시", "content": {}},
    )
    auto_seq_ex = next(
        (ex["value"] for ex in guide["examples"] if "auto_seq" in ex["title"]),
        {"data_type": "DOC", "team": "HE", "group": "CAE", "year": 2026, "title": "예시", "content": {}},
    )
    batch_ex = next(
        (ex["value"] for ex in guide["examples"] if "배열" in ex["title"]),
        {"auto_seq": True, "records": [auto_seq_ex]},
    )

    # zip 조립 (메모리 내 — 1 MB 미만이므로 디스크 불필요)
    buf = io.BytesIO()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SYSTEM_PROMPT.md", guide["instructions"])
        z.writestr("SCHEMA.json", json.dumps(schema, ensure_ascii=False, indent=2))
        z.writestr("validate.py", validate_py)
        z.writestr(
            "examples/single.json",
            json.dumps(single_ex, ensure_ascii=False, indent=2),
        )
        z.writestr(
            "examples/auto_seq.json",
            json.dumps(auto_seq_ex, ensure_ascii=False, indent=2),
        )
        z.writestr(
            "examples/batch.json",
            json.dumps(batch_ex, ensure_ascii=False, indent=2),
        )
        z.writestr("README.md", readme_md)
        z.writestr(
            ".kit-meta.json",
            json.dumps(
                {
                    "generated_at": now,
                    "agent_type": agent_type,
                    "registered_agents": agents_all,
                    "registered_doc_types": doc_types,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    filename = (
        f"ingest-kit-{agent_type}.zip" if agent_type else "ingest-kit.zip"
    )
    return buf.getvalue(), filename


# ---------------------------------------------------------------------------
# validate.py 본문 — 사용자 PC 에서 단독 실행 가능
# ---------------------------------------------------------------------------
def _render_validate_py(
    *,
    enums: dict[str, list[str]],
    doc_types: list[str],
    doc_type_modes: dict[str, str],
    agents_all: list[str],
    expected: dict[str, Any],
) -> str:
    """``validate.py`` 본문 렌더 — 자기 완결적 검증 스크립트.

    표준 라이브러리만 사용. 어떤 Python 3.8+ 환경에서도 실행 가능.
    """
    enums_repr = repr(enums)
    doc_types_repr = repr(doc_types)
    doc_type_modes_repr = repr(doc_type_modes)
    agents_repr = repr(agents_all)
    expected_repr = repr(expected)

    body = textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """validate.py — Mobile eXperience AI Data Hub Ingest Kit 검증 스크립트.

        사용:
            python validate.py output.json [output2.json ...]
            cat output.json | python validate.py
            python validate.py --format=json output.json   # 머신 리더블 출력
            python validate.py --quiet output.json         # 에러만 출력

        반환 코드:
            0   모든 record 가 valid
            1   하나라도 error
            2   잘못된 입력 (파일 없음, JSON 파싱 실패 등)

        본 스크립트는 **표준 라이브러리만** 사용한다. 별도 설치 불필요.
        의존하지 마라: 네트워크, jsonschema, requests, etc.
        """
        from __future__ import annotations

        import json
        import re
        import sys
        from pathlib import Path

        # ------------------------------------------------------------ 하드코딩 자료
        # 생성 시점의 서버 상태로 박힌 데이터. 갱신하려면 zip 을 다시 받아라.
        ENUMS = __ENUMS__
        REGISTERED_DOC_TYPES = __DOC_TYPES__
        DOC_TYPE_MODES = __DOC_TYPE_MODES__  # code -> 'llm_context' | 'data_extract' | 'hybrid'
        REGISTERED_AGENTS = __AGENTS__
        EXPECTED = __EXPECTED__   # agent 별 expected_schema (없으면 빈 dict)

        # ------------------------------------------------------------ id 형식
        DATA_TYPES_RE = "|".join(sorted(ENUMS["data_type"], key=len, reverse=True))
        ID_PATTERN = re.compile(
            rf"^(?P<data_type>{DATA_TYPES_RE})"
            r"-(?P<team>[A-Z]{2,4})"
            r"-(?P<group>[A-Z]{2,5})"
            r"-(?P<year>20[2-9][0-9])"
            r"-(?P<seq>\\d{6,12})$"
        )

        # ------------------------------------------------------------ 검증 본문
        REQUIRED_FIELDS = ("title", "content")

        def validate_record(rec, idx, path):
            errors = []
            warnings = []
            if not isinstance(rec, dict):
                errors.append(f"{path}: must be a JSON object")
                return errors, warnings

            # 1. id 또는 auto_seq 키
            if rec.get("id"):
                if not ID_PATTERN.match(rec["id"]):
                    errors.append(
                        f"{path}.id: invalid format '{rec['id']}' "
                        "(expected {DATA_TYPE}-{TEAM}-{GROUP}-{YYYY}-{6~12digit})"
                    )
            else:
                # auto_seq 모드 — 4개 키 모두 있어야 서버가 채번 가능
                for k in ("data_type", "team", "group", "year"):
                    if not rec.get(k):
                        errors.append(
                            f"{path}.{k}: required (auto_seq mode — needs data_type/team/group/year all set)"
                        )

            # 2. 필수 필드
            for k in REQUIRED_FIELDS:
                if k not in rec or rec[k] in (None, ""):
                    errors.append(f"{path}.{k}: required")

            # 3. data_type enum
            dt = rec.get("data_type")
            if dt and dt not in ENUMS["data_type"]:
                errors.append(
                    f"{path}.data_type: must be one of {ENUMS['data_type']}, got '{dt}'"
                )

            # 4. classification / status / derivation / access_pattern enum
            for k in ("classification", "status", "derivation", "access_pattern"):
                v = rec.get(k)
                if v and v not in ENUMS.get(k, []):
                    errors.append(
                        f"{path}.{k}: must be one of {ENUMS.get(k)}, got '{v}'"
                    )

            # 5. doc_type — 등록 여부 (warn-only — 서버도 warn-only)
            dtc = rec.get("doc_type")
            if dtc and REGISTERED_DOC_TYPES and dtc not in REGISTERED_DOC_TYPES:
                warnings.append(
                    f"{path}.doc_type: '{dtc}' not registered "
                    f"(known: {sorted(REGISTERED_DOC_TYPES)[:10]}{'...' if len(REGISTERED_DOC_TYPES) > 10 else ''})"
                )

            # 6. agents — 등록 여부
            agents = rec.get("agents") or []
            if not isinstance(agents, list):
                errors.append(f"{path}.agents: must be list of strings")
            else:
                for at in agents:
                    if REGISTERED_AGENTS and at not in REGISTERED_AGENTS:
                        warnings.append(
                            f"{path}.agents: '{at}' not registered "
                            f"(known: {sorted(REGISTERED_AGENTS)[:10]}{'...' if len(REGISTERED_AGENTS) > 10 else ''})"
                        )

            # 7. tags — list of str
            tags = rec.get("tags")
            if tags is not None:
                if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                    errors.append(f"{path}.tags: must be list of strings")

            # 8. content 구조 — dict 권장, sections list 권장 (warn)
            ct = rec.get("content")
            if ct is not None and not isinstance(ct, dict):
                errors.append(f"{path}.content: must be a JSON object")
            elif isinstance(ct, dict) and "sections" in ct:
                if not isinstance(ct["sections"], list):
                    errors.append(f"{path}.content.sections: must be a list")
                else:
                    for si, sec in enumerate(ct["sections"]):
                        if not isinstance(sec, dict):
                            errors.append(f"{path}.content.sections[{si}]: must be object")
                            continue
                        if "section_id" not in sec:
                            warnings.append(f"{path}.content.sections[{si}]: missing 'section_id'")
                        if "title" not in sec:
                            warnings.append(f"{path}.content.sections[{si}]: missing 'title'")

            # 9. EXPECTED (agent 특화) — agent_type 이 키트 생성 때 지정된 경우만
            if EXPECTED.get("agent_type"):
                target = EXPECTED["agent_type"]
                if target not in (agents or []):
                    warnings.append(
                        f"{path}.agents: missing target agent '{target}' "
                        "(이 키트는 이 agent 용으로 빌드됨 — agents 배열에 추가 권장)"
                    )
                req_dt = EXPECTED.get("required_doc_type")
                if req_dt and dtc != req_dt:
                    errors.append(
                        f"{path}.doc_type: agent '{target}' expects '{req_dt}', got '{dtc}'"
                    )
                req_tags = set(EXPECTED.get("required_tags") or [])
                cur_tags = set(tags or [])
                missing_tags = req_tags - cur_tags
                if missing_tags:
                    errors.append(
                        f"{path}.tags: agent '{target}' requires {sorted(missing_tags)}"
                    )
                exc_tags = set(EXPECTED.get("excluded_tags") or [])
                bad_tags = cur_tags & exc_tags
                if bad_tags:
                    errors.append(
                        f"{path}.tags: agent '{target}' forbids {sorted(bad_tags)}"
                    )

            # 10. year 범위
            yr = rec.get("year")
            if yr is not None:
                try:
                    yri = int(yr)
                    if not (2020 <= yri <= 2099):
                        errors.append(f"{path}.year: must be in 2020..2099, got {yri}")
                except (TypeError, ValueError):
                    errors.append(f"{path}.year: must be integer, got {yr!r}")

            return errors, warnings


        def collect_records(data):
            """body 3가지 형식을 평탄화: dict / list / {records:[...]}"""
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                return list(data["records"])
            if isinstance(data, list):
                return list(data)
            if isinstance(data, dict):
                return [data]
            return []


        def main():
            args = [a for a in sys.argv[1:] if not a.startswith("--")]
            opts = [a for a in sys.argv[1:] if a.startswith("--")]
            fmt = "text"
            quiet = False
            for o in opts:
                if o.startswith("--format="):
                    fmt = o.split("=", 1)[1]
                elif o == "--quiet":
                    quiet = True
                elif o in ("--help", "-h"):
                    print(__doc__)
                    sys.exit(0)
                else:
                    print(f"unknown option: {o}", file=sys.stderr)
                    sys.exit(2)

            sources = []
            if args:
                for p in args:
                    try:
                        text = Path(p).read_text(encoding="utf-8")
                        sources.append((p, json.loads(text)))
                    except FileNotFoundError:
                        print(f"file not found: {p}", file=sys.stderr)
                        sys.exit(2)
                    except json.JSONDecodeError as exc:
                        print(f"{p}: JSON parse error: {exc}", file=sys.stderr)
                        sys.exit(2)
            else:
                if sys.stdin.isatty():
                    print("usage: python validate.py <file.json> [file2.json ...]", file=sys.stderr)
                    print("  or:  cat output.json | python validate.py", file=sys.stderr)
                    sys.exit(2)
                try:
                    sources.append(("<stdin>", json.loads(sys.stdin.read())))
                except json.JSONDecodeError as exc:
                    print(f"<stdin>: JSON parse error: {exc}", file=sys.stderr)
                    sys.exit(2)

            total_errors = []
            total_warnings = []
            total_records = 0
            for src, data in sources:
                recs = collect_records(data)
                if not recs:
                    total_errors.append(f"{src}: no records found (must be object, list, or {{records:[...]}})")
                    continue
                for i, rec in enumerate(recs):
                    path = f"{src}#records[{i}]" if len(sources) > 1 else f"records[{i}]"
                    e, w = validate_record(rec, i, path)
                    total_errors.extend(e)
                    total_warnings.extend(w)
                total_records += len(recs)

            ok = len(total_errors) == 0
            if fmt == "json":
                out = {
                    "records": total_records,
                    "errors": total_errors,
                    "warnings": total_warnings,
                    "valid": ok,
                    "expected": EXPECTED,
                }
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                if not quiet:
                    print(f"records:  {total_records}")
                    print(f"errors:   {len(total_errors)}")
                    print(f"warnings: {len(total_warnings)}")
                    if EXPECTED.get("agent_type"):
                        print(f"agent_type filter: {EXPECTED['agent_type']}")
                    print()
                if total_errors:
                    print("ERRORS:")
                    for e in total_errors:
                        print(f"  X {e}")
                if total_warnings and not quiet:
                    print("WARNINGS:")
                    for w in total_warnings:
                        print(f"  ! {w}")
                if ok and not quiet:
                    print("OK - all records valid.")

            sys.exit(0 if ok else 1)


        if __name__ == "__main__":
            main()
        '''
    )

    # 자료 치환 — repr 결과를 그대로 Python literal 로 삽입
    body = body.replace("__ENUMS__", enums_repr)
    body = body.replace("__DOC_TYPES__", doc_types_repr)
    body = body.replace("__DOC_TYPE_MODES__", doc_type_modes_repr)
    body = body.replace("__AGENTS__", agents_repr)
    body = body.replace("__EXPECTED__", expected_repr)
    return body


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
def _render_readme(*, agent_type: str | None, expected: dict[str, Any]) -> str:
    title_suffix = f" ({agent_type})" if agent_type else ""
    expected_note = ""
    if expected.get("agent_type"):
        req_dt = expected.get("required_doc_type")
        req_tags = expected.get("required_tags") or []
        exc_tags = expected.get("excluded_tags") or []
        bullets: list[str] = []
        if req_dt:
            bullets.append(f"- `doc_type` 은 반드시 `{req_dt}` 여야 함")
        if req_tags:
            bullets.append(f"- `tags` 에 반드시 포함: {', '.join(f'`{t}`' for t in req_tags)}")
        if exc_tags:
            bullets.append(f"- `tags` 에 절대 포함하면 안 됨: {', '.join(f'`{t}`' for t in exc_tags)}")
        if bullets:
            expected_note = "\n## 이 agent 의 expected schema\n\n" + "\n".join(bullets) + "\n"

    return textwrap.dedent(
        f"""\
        # Ingest Kit{title_suffix}

        Mobile eXperience AI Data Hub 에 데이터를 올리기 위한 자기완결적 키트.
        이 키트는 **너의 LLM** (사내 Llama / Claude / ChatGPT / 어느 것이든) 으로
        규격 JSON 을 생성하고, 너의 PC 에서 검증한 뒤, 통과한 것만 업로드하는
        흐름을 지원한다.

        ## 파일 구성

        - `SYSTEM_PROMPT.md` — LLM 에 그대로 시스템 프롬프트로 줄 가이드 (필수 필드, enum, 등록 agent, 등록 doc_type, 완성 예시 포함)
        - `SCHEMA.json`      — 머신 리더블 JSON Schema (draft-2020-12)
        - `validate.py`      — **표준 라이브러리만** 쓰는 검증 스크립트
        - `examples/`        — 단건 / auto_seq / 배열 완성 예시
        - `.kit-meta.json`   — 키트 생성 시점 메타데이터
        {expected_note}
        ## 권장 작업 순서

        ### 1) LLM 으로 JSON 만들기

        `SYSTEM_PROMPT.md` 의 내용을 LLM 의 시스템 프롬프트로 통째로 붙여 넣는다.
        그 후 사용자 메시지로 원본 데이터(보고서 본문/CSV/문서 내용)를 던지면,
        LLM 이 규격 JSON 을 응답한다.

        - 한 문서 → 단일 JSON 객체
        - 여러 문서 → `{{auto_seq: true, records: [...]}}` 또는 객체 배열

        응답을 `output.json` 같은 파일로 저장한다.

        ### 2) 검증

        ```
        python validate.py output.json
        ```

        출력 예:
        ```
        records:  3
        errors:   1
        warnings: 0

        ERRORS:
          X records[2].doc_type: agent 'cae-analyst' expects 'test_report', got 'memo'
        ```

        - 종료 코드 0 = OK, 1 = 에러 있음, 2 = 입력 문제.
        - `--format=json` 으로 머신 리더블 출력.
        - `--quiet` 로 에러만 출력.

        에러를 그대로 다시 LLM 에 던지면 LLM 이 수정 JSON 을 응답한다.

        ### 3) 업로드

        통과한 JSON 을 다음 중 한 가지로 업로드:

        **(a) VSCode Extension "Import JSON" 탭 → 파일 드롭**

        **(b) HTTP 직접 호출:**
        ```
        curl -X POST 'http://<host>/api/records/import?auto_seq=true' \\
          -H 'X-API-Key: <your_key>' \\
          -H 'Content-Type: application/json' \\
          --data @output.json
        ```

        ### dry-run

        실제 저장 전 검증만 원하면:
        ```
        curl -X POST 'http://<host>/api/records/import?auto_seq=true&dry_run=true' ...
        ```

        ## FAQ

        **Q. LLM 이 코드펜스 ` ```json ... ``` ` 를 붙여서 응답해요.**
        A. LLM 한테 "JSON 만 출력해. 코드펜스/주석/설명문 금지." 라고 한 번 더 강조하거나,
           응답에서 ` ``` ` 라인만 손으로 지우면 된다. validate.py 가 코드펜스를 같이
           삼키지는 않으므로 JSON 파싱 단계에서 거부된다.

        **Q. 등록된 agent / doc_type 목록이 바뀌면?**
        A. 이 키트는 생성 시점의 데이터로 박혀 있다. 변경이 있으면 키트를 다시 받아라
           (`GET /api/schema/ingest-kit.zip[?agent_type=...]`).

        **Q. id 를 직접 부여하고 싶지 않은데?**
        A. `data_type, team, group, year` 만 채우고 `auto_seq=true` 로 import 하면
           서버가 다음 seq 를 자동 부여한다 (가장 안전). 키트의 `examples/auto_seq.json`
           참고.

        **Q. validate.py 가 Python 안 깔린 환경에서도 되나?**
        A. Python 3.8+ 만 있으면 된다. 외부 패키지 install 불필요. macOS/Linux 는 보통 기본,
           Windows 도 `python` 설치 후 그대로 실행 가능.
        """
    )


__all__ = ["build_ingest_kit_zip"]
