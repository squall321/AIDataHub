"""LLM 친화 데이터 입력 가이드 생성.

목적:
    외부 LLM (Claude / ChatGPT / 사내 Llama) 이 시스템 프롬프트로 그대로 붙여
    "원본 데이터 → 규격 JSON" 변환을 수행할 수 있도록 한 번에 필요한 모든
    정보를 합쳐서 제공한다.

응답 구성:
    1. 필수/권장/선택 필드 표
    2. id 포맷 + 자동 채번 (auto_seq) 모드 설명
    3. data_type / classification / status / derivation enum
    4. 등록된 agent 목록 + 각 agent 의 expected schema
    5. doc_type 목록
    6. 완성 JSON 예시 (단건 + 배열)
    7. POST /api/records/import 호출 예시

응답 포맷:
    GET /api/schema/ingest-guide → markdown (LLM 시스템 프롬프트로 직접 사용)
    GET /api/schema/ingest-guide?format=json → {instructions, examples, enums}
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, DocType
from ..schemas.id_format import DATA_TYPES, SEQ_PAD_WIDTH


CLASSIFICATION_VALUES = ("public", "internal", "confidential", "secret")
STATUS_VALUES = ("draft", "published", "archived")
DERIVATION_VALUES = ("original", "derived", "aggregated")
ACCESS_PATTERN_VALUES = ("frequent", "occasional", "rare")


async def build_guide(
    session: AsyncSession, *, agent_type: str | None = None
) -> dict[str, Any]:
    """LLM 가이드 페이로드(JSON) 빌드."""
    # 등록 agent 목록 (agent_type 지정 시 단일 agent 만 포함).
    if agent_type:
        rows = (
            await session.execute(
                select(Agent).where(Agent.agent_type == agent_type)
            )
        ).scalars().all()
    else:
        rows = (await session.execute(select(Agent))).scalars().all()

    agents_info: list[dict[str, Any]] = []
    for a in rows:
        agents_info.append(
            {
                "agent_type": a.agent_type,
                "name": a.name,
                "description": a.description or "",
                "data_types": list(a.data_types or []),
                "common_tags": list(a.common_tags or []),
                "required_doc_type": a.required_doc_type,
                "required_tags": list(a.required_tags or []),
                "excluded_tags": list(a.excluded_tags or []),
                "sample_queries": list(a.sample_queries or [])[:5],
            }
        )

    # doc_type 목록
    dt_rows = (
        await session.execute(select(DocType).order_by(DocType.code))
    ).scalars().all()
    doc_types = [
        {
            "code": d.code,
            "name": d.name,
            "data_type": getattr(d, "data_type", None),
            "mode": getattr(d, "mode", "llm_context"),
            "description": d.description or "",
        }
        for d in dt_rows
    ]

    enums = {
        "data_type": list(DATA_TYPES),
        "classification": list(CLASSIFICATION_VALUES),
        "status": list(STATUS_VALUES),
        "derivation": list(DERIVATION_VALUES),
        "access_pattern": list(ACCESS_PATTERN_VALUES),
    }

    examples = _example_records(agents_info)

    return {
        "instructions": _build_markdown(
            agents_info=agents_info,
            doc_types=doc_types,
            enums=enums,
            examples=examples,
            agent_type=agent_type,
        ),
        "enums": enums,
        "agents": agents_info,
        "doc_types": doc_types,
        "examples": examples,
        "id_format": {
            "pattern": "{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:010d}",
            "seq_pad_width": SEQ_PAD_WIDTH,
            "auto_seq_supported": True,
            "example": "DOC-HE-CAE-2026-0000000001",
        },
    }


def _example_records(agents_info: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM 학습용 완성 예시 — 단건 + 배열 + auto_seq 변형 포함."""
    first_agent = agents_info[0]["agent_type"] if agents_info else "cae-analyst"
    return [
        {
            "title": "단건 — 명시 id (LLM 이 id 를 만들 때)",
            "value": {
                "id": "DOC-HE-CAE-2026-0000000001",
                "title": "낙하시험 보고서 — X 모델 V3",
                "summary": "1.2m 자유낙하, 6개 자세, 변형률·가속도 분석",
                "doc_type": "test_report",
                "tags": ["drop-test", "phone", "v3"],
                "agents": [first_agent],
                "author": "홍길동",
                "department": "HE/CAE",
                "language": "ko",
                "classification": "internal",
                "content": {
                    "sections": [
                        {
                            "section_id": "1",
                            "level": 1,
                            "title": "개요",
                            "content_text": "본 보고서는 X 모델 V3 의 낙하시험 결과를 정리한다.",
                        },
                        {
                            "section_id": "2",
                            "level": 1,
                            "title": "결과",
                            "content_text": "최대 변형률 0.0042, 최대 가속도 8500g.",
                        },
                    ]
                },
            },
        },
        {
            "title": "단건 — auto_seq 모드 (id 생략, 서버가 채번)",
            "value": {
                "data_type": "DOC",
                "team": "HE",
                "group": "CAE",
                "year": 2026,
                "title": "낙하시험 보고서 — Y 모델",
                "content": {
                    "sections": [
                        {
                            "section_id": "1",
                            "level": 1,
                            "title": "본문",
                            "content_text": "...",
                        }
                    ]
                },
            },
        },
        {
            "title": "배열 — 다건 일괄 (POST /api/records/import body)",
            "value": {
                "auto_seq": True,
                "dry_run": False,
                "records": [
                    {
                        "data_type": "DOC",
                        "team": "HE",
                        "group": "CAE",
                        "year": 2026,
                        "title": "보고서 1",
                        "content": {"sections": []},
                    },
                    {
                        "data_type": "DOC",
                        "team": "HE",
                        "group": "CAE",
                        "year": 2026,
                        "title": "보고서 2",
                        "content": {"sections": []},
                    },
                ],
            },
        },
    ]


def _build_markdown(
    *,
    agents_info: list[dict[str, Any]],
    doc_types: list[dict[str, Any]],
    enums: dict[str, list[str]],
    examples: list[dict[str, Any]],
    agent_type: str | None,
) -> str:
    """LLM 시스템 프롬프트로 그대로 사용 가능한 마크다운 생성."""
    lines: list[str] = []
    lines.append("# Mobile eXperience AI Data Hub — Ingest Guide for LLM")
    lines.append("")
    lines.append("이 문서는 너(LLM)가 사용자의 원본 데이터(보고서/CSV/문서)를")
    lines.append("우리 데이터 허브에 입력 가능한 **규격 JSON** 으로 변환하기 위한 가이드다.")
    lines.append("이 가이드를 시스템 프롬프트로 받았다면, 사용자의 다음 입력을 가지고")
    lines.append("**오직 JSON 한 개만** (또는 객체 배열) 출력하라. 설명/마크다운 금지.")
    lines.append("")

    # ── 필드 룰
    lines.append("## 필수 필드 (없으면 400)")
    lines.append("")
    lines.append("| 필드 | 타입 | 비고 |")
    lines.append("|---|---|---|")
    lines.append("| `title` | string | 사람이 읽는 제목 |")
    lines.append("| `content` | object | 본문 — `{sections: [...]}` 권장 |")
    lines.append("| `id` **또는** `(data_type, team, group, year)` | — | id 생성 또는 auto_seq 모드 |")
    lines.append("")
    lines.append("**id 직접 부여 시** 형식: `{DATA_TYPE}-{TEAM}-{GROUP}-{YYYY}-{SEQ:010d}`")
    lines.append("예: `DOC-HE-CAE-2026-0000000001`")
    lines.append("")
    lines.append("**id 생략 시 (auto_seq)**: `data_type`, `team`, `group`, `year` 만 주면")
    lines.append("서버가 해당 (data_type, team, group, year) 의 다음 seq 를 부여한다.")
    lines.append("이 방식이 가장 안전하다 (id 충돌 방지).")
    lines.append("")

    lines.append("## 강력 권장 필드 (검색 품질 직결)")
    lines.append("")
    lines.append("| 필드 | 타입 | 의미 |")
    lines.append("|---|---|---|")
    lines.append("| `summary` | string | 1~3 줄 요약. 시맨틱 검색에 영향. |")
    lines.append("| `tags` | string[] | 영문 소문자 kebab-case 권장. `[\"drop-test\", \"phone\"]` |")
    lines.append("| `agents` | string[] | 사용할 agent_type 목록 (아래 등록 agent 표 참조) |")
    lines.append("| `doc_type` | string | 등록된 doc_type code (아래 표 참조) |")
    lines.append("| `author` | string | |")
    lines.append("| `department` | string | 예: `HE/CAE` |")
    lines.append("| `language` | string | `ko` / `en` / `ja` … (기본 `ko`) |")
    lines.append("")

    lines.append("## 선택 필드")
    lines.append("")
    lines.append("`project`, `version` (default `1.0`), `classification` (default `internal`),")
    lines.append("`status` (default `draft`), `domain`, `subject_keywords`, `source_system`,")
    lines.append("`parent_record_id`, `quality_score` (0~100), `valid_from`, `valid_until`,")
    lines.append("`agent_hints`, `related_record_ids`, `query_examples`, `access_pattern`,")
    lines.append("`source_file`, `derivation`.")
    lines.append("")
    lines.append("`content_hash`, `depth`, `capabilities`, `created_at`, `updated_at`, `has_attachments`,")
    lines.append("`attachment_count` 는 서버가 자동 계산하므로 **출력하지 마라**.")
    lines.append("")

    # ── enums
    lines.append("## Enum 값 (이 외 값을 쓰면 warn 또는 에러)")
    lines.append("")
    for k, vals in enums.items():
        lines.append(f"- `{k}`: {', '.join(f'`{v}`' for v in vals)}")
    lines.append("")

    # ── content 구조
    lines.append("## `content` 권장 구조")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(
        {
            "sections": [
                {
                    "section_id": "1",
                    "level": 1,
                    "title": "개요",
                    "content_text": "본문 텍스트...",
                    "figure_refs": ["F001"],
                    "table_refs": ["T001"],
                },
                {
                    "section_id": "1.1",
                    "level": 2,
                    "title": "배경",
                    "content_text": "...",
                },
            ]
        },
        ensure_ascii=False,
        indent=2,
    ))
    lines.append("```")
    lines.append("")
    lines.append("- `section_id`: 계층 점표기 (`1`, `1.1`, `1.2.3`)")
    lines.append("- `level`: 1=장, 2=절, 3=항")
    lines.append("- `content_text`: 본문 평문 (마크다운/HTML 가능, 검색은 평문화 후 수행)")
    lines.append("")

    # ── agent 목록
    if agent_type:
        lines.append(f"## 등록 Agent — `{agent_type}` (특정 agent 용 가이드)")
    else:
        lines.append("## 등록 Agent 목록 (`agents` 필드에 넣을 값)")
    lines.append("")
    if not agents_info:
        lines.append("_(등록된 agent 없음)_")
    else:
        for a in agents_info:
            lines.append(f"### `{a['agent_type']}` — {a['name']}")
            if a["description"]:
                lines.append(f"{a['description']}")
            if a["data_types"]:
                lines.append(f"- 대상 `data_type`: {', '.join(f'`{d}`' for d in a['data_types'])}")
            if a["required_doc_type"]:
                lines.append(f"- **요구 `doc_type`**: `{a['required_doc_type']}` (이 agent 를 쓰려면 record 의 doc_type 이 이 값이어야 함)")
            if a["required_tags"]:
                lines.append(f"- **요구 tag**: {', '.join(f'`{t}`' for t in a['required_tags'])}")
            if a["excluded_tags"]:
                lines.append(f"- 제외 tag: {', '.join(f'`{t}`' for t in a['excluded_tags'])}")
            if a["common_tags"]:
                lines.append(f"- 자주 쓰이는 tag: {', '.join(f'`{t}`' for t in a['common_tags'][:8])}")
            if a["sample_queries"]:
                lines.append(f"- 샘플 쿼리: {'; '.join(a['sample_queries'][:3])}")
            lines.append("")

    # ── doc_types
    lines.append("## 등록 doc_type 목록 (`doc_type` 필드에 넣을 값)")
    lines.append("")
    lines.append("`mode` 컬럼 의미:")
    lines.append("- `llm_context`: 텍스트 자료 — embedding 생성, semantic 검색 우선")
    lines.append("- `data_extract`: 수치 자료 — embedding skip, 메타·tag 검색 위주")
    lines.append("- `hybrid`: 둘 다 가치 — 양쪽 검색에 모두 노출")
    lines.append("")
    if not doc_types:
        lines.append("_(등록된 doc_type 없음 — 비워둬도 됨)_")
    else:
        lines.append("| code | mode | name | 설명 |")
        lines.append("|---|---|---|---|")
        for d in doc_types:
            desc = (d["description"] or "").replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(f"| `{d['code']}` | `{d.get('mode','llm_context')}` | {d['name']} | {desc} |")
    lines.append("")

    # ── 완성 예시
    lines.append("## 완성 JSON 예시")
    lines.append("")
    for ex in examples:
        lines.append(f"### {ex['title']}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(ex["value"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    # ── import 호출
    lines.append("## 업로드 방법")
    lines.append("")
    lines.append("생성한 JSON 을 파일로 저장하면, 사용자는 두 가지로 입력할 수 있다:")
    lines.append("")
    lines.append("**1) VSCode Extension 'Import JSON' 버튼 → 파일 드래그**")
    lines.append("")
    lines.append("**2) HTTP 직접 호출:**")
    lines.append("")
    lines.append("```bash")
    lines.append("# 단건 (auto_seq)")
    lines.append("curl -X POST http://<host>/api/records/import \\")
    lines.append("  -H 'X-API-Key: <key>' -H 'Content-Type: application/json' \\")
    lines.append("  -d '{\"auto_seq\": true, \"records\": [<JSON>]}'")
    lines.append("")
    lines.append("# 다건 (배열만 body 로 보내는 단순 형태도 허용)")
    lines.append("curl -X POST http://<host>/api/records/import?auto_seq=true \\")
    lines.append("  -H 'X-API-Key: <key>' -H 'Content-Type: application/json' \\")
    lines.append("  -d '[<JSON1>, <JSON2>, ...]'")
    lines.append("")
    lines.append("# dry-run (검증만, 저장 X)")
    lines.append("curl -X POST 'http://<host>/api/records/import?dry_run=true' ...")
    lines.append("```")
    lines.append("")

    # ── 최종 출력 형식 강제
    lines.append("## 출력 형식 (LLM 에게)")
    lines.append("")
    lines.append("- 사용자가 단일 문서를 주면: **하나의 JSON object** 만 출력.")
    lines.append("- 사용자가 여러 문서를 주면: `{auto_seq: true, records: [...]}` 또는 객체 배열.")
    lines.append("- JSON 이외의 텍스트/설명/주석/마크다운 코드펜스 출력 금지.")
    lines.append("- 모르는 필드는 비우거나 생략 (추측해서 채우지 마라 — `quality_score` 같은 정량값).")
    lines.append("- `tags` 는 영문 kebab-case, 5개 이내 권장.")
    lines.append("")

    return "\n".join(lines)


__all__ = ["build_guide"]
