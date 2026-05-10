# JSON Schema (압축 참조용)

> 풀 명세는 [`../json_schema_rules.md`](../json_schema_rules.md). 본 문서는 agent 가 1번에 흡수할 수 있는 핵심만.

`BASE = http://110.15.177.125:8000`

---

## Record 최상위 (RecordOut — `/api/records/{id}` 응답)

```jsonc
{
  // 식별 (id 에서 team/group/year/seq 모두 도출)
  "id": "DOC-HE-CAE-2026-000001",
  "data_type": "DOC",       // DOC | DATA | SIM | CAD | LOG | FORM | OTHER
  "team": "HE",
  "group": "CAE",
  "year": 2026,
  "seq": 1,
  "schema_version": "1.0",

  // RAG 1차 필터
  "title": "KooRemapper Manual",
  "summary": "...",          // 자동 추출 (Word/MD/HTML), 또는 override
  "tags": ["IGA", "NURBS"],  // 자동 추출 (Word RAKE) + override
  "agents": ["iga-analyst"], // agent_type 식별자 (등록되어야 함)

  // 출처/저자
  "source_file": "iga_guide.docx",
  "author": "...",
  "department": "...",
  "project": "KooRemapper",
  "version": "1.0",

  // 분류/생애주기 (Migration 0006)
  "classification": "internal",  // public | internal | confidential | restricted
  "status": "approved",          // draft | review | approved | deprecated
  "domain": "CAE",
  "subject_keywords": ["등기하해석"],
  "source_system": "manual_authoring",
  "language": "ko",
  "parent_record_id": null,
  "derivation": "original",      // original | extracted | aggregated | translated
  "quality_score": 85,           // 0~100
  "valid_from": "2026-05-01",
  "valid_until": null,

  // Agent discovery (Migration 0007 — 자동 채움)
  "agent_hints": "이 record 는 ... 입니다. 주요 토픽: ...",
  "related_record_ids": [],
  "query_examples": ["...", "..."],
  "access_pattern": "occasional",  // frequent | occasional | rare

  // DB 컴퓨티드
  "content_hash": "...",
  "has_attachments": true,
  "attachment_count": 12,
  "created_at": "2026-05-08T...",
  "updated_at": "2026-05-08T...",

  // 본문 (JSONB)
  "content": {
    "sections": [...],
    "tables": [...],
    "attachments": [...],
    "sources": [...],
    "toc": [...]
  }
}
```

---

## sections 트리 (record_sections 테이블 1행 = 1 노드)

```jsonc
{
  "id": "1.2",
  "level": 2,                  // 1~6 (Word 1~3, MD 1~6, PPT 1~2, Excel 1)
  "title": "...",
  "blocks": [
    { "type": "paragraph", "text": "..." },
    { "type": "code", "lang": "yaml", "text": "..." },
    { "type": "list_item", "level": 1, "ordered": false, "text": "..." },
    { "type": "table_ref", "id": "T001" },
    { "type": "figure_ref", "id": "F001" },
    { "type": "quote", "text": "..." },
    { "type": "heading_inline", "level": 4, "text": "..." }
  ],
  "figure_refs": ["F001"],
  "table_refs": ["T001"],
  "children": []
}
```

---

## tables[]

```jsonc
{
  "id": "T001",
  "caption": "응력-변형률 곡선 (SS400)",
  "headers": ["strain", "stress"],
  "rows": [[0.001, 250], [0.002, 480]],
  "units": { "strain": "%", "stress": "MPa" },  // 객체 형식
  "context": {                                   // Excel 만 (옵션)
    "method": "KS B 0814",
    "condition": "23±2℃",
    "equipment": "UTM-500",
    "operator": "박지수",
    "date": "2026-04-15"
  },
  "column_descriptions": { "strain": "변형률", "stress": "응력" }
}
```

---

## attachments[]

```jsonc
{
  "id": "A001",
  "kind": "figure",       // 10 kinds (api/schemas/attachment.py:29-51):
                          //   figure | document | spreadsheet | media |
                          //   archive | cad | chart | drawing | data | other
                          // 확장자 자동 매핑: _KIND_BY_EXT 표 참조.
                          // PPT 차트 placeholder 는 kind="chart".
  "mime": "image/png",
  "caption": "Figure 1: NURBS 기저함수",
  "file_path": "DOC-HE-CAE-2026-000001/A001.png",  // POSIX 상대 경로
  "size_bytes": 12345
}
```

다운로드: `GET http://110.15.177.125:8000/attachments/{file_path}`

---

## sources[]

```jsonc
{
  "id": "S001",
  "label": "input.k",
  "kind": "lsdyna_input",
  "role": "source",       // source | result | reference
  "uri": "smb://...",
  "file_format": "k",
  "size_bytes": 12345
}
```

---

## ID 포맷

```text
{DATA_TYPE}-{DIV}-{GROUP}-{YYYY}-{6digits}

DATA_TYPE: DOC | DATA | SIM | CAD | LOG | FORM | OTHER
DIV / GROUP: 대문자 2~5자 (회사 표준)
YYYY: 4자리 연도
6digits: 0-padded sequence

예: DOC-HE-CAE-2026-000001
    DATA-HE-MFG-2026-000034
    SIM-HE-CAE-2025-000007
```

`id` 만 있으면 `team/group/year/seq` 모두 파싱 가능. agent 가 별도 필드로 전달할 필요 없음.

---

## 검색 응답 (`/api/search?mode=...`)

### `mode=semantic` 또는 `mode=fts`

```jsonc
{
  "mode": "semantic",
  "q": "KooRemapper",
  "items": [
    {
      "record_id": "DOC-HE-CAE-2026-000001",
      "title": "KooRemapper Manual",
      "data_type": "DOC",
      "section_id": "16.3",          // semantic 만 (어느 섹션이 매칭됐는지)
      "section_title": "...",
      "snippet": "…응력 변형률…",
      "score": 0.964,                 // semantic: cosine, fts: ts_rank
      "tags": ["IGA","NURBS"]
    }
  ],
  "total": 7,
  "limit": 20,
  "offset": 0
}
```

### `mode=tag`

```jsonc
{
  "mode": "tag",
  "tags": ["IGA"],
  "items": [
    /* RecordOut 형태 — id, title, summary, tags, ... 전체 필드 */
  ],
  "total": 5
}
```

---

## /api/groups/auto 응답

```jsonc
{
  "query": "체크리스트",
  "total_records": 42,
  "groups": [
    {
      "label": "전사 표준 체크리스트",
      "size": 18,
      "common_domain": "ops",
      "common_tags": ["company-wide"],
      "records": [
        {"id": "DOC-...", "title": "...", "score": 0.94}
      ]
    }
  ]
}
```

---

## meta enum 한방 요약

| 키 | 허용값 |
|---|---|
| `data_type` | DOC / DATA / SIM / CAD / LOG / FORM / OTHER |
| `classification` | public / internal / confidential / restricted |
| `status` | draft / review / approved / deprecated |
| `derivation` | original / extracted / aggregated / translated |
| `access_pattern` | frequent / occasional / rare |

---

## 자주 쓰는 own-extras (변환기별 특수 메타)

JSONB `content` 안에 있을 수 있음:

| 키 | 어디서 | 내용 |
|---|---|---|
| `meta.pdf` | PDF 출신 record | `{page_count, heading_strategy, creator, producer, creation_date, ocr_pages}` |
| `meta.head_meta_extra` | HTML 출신 | 표준 매핑 안 된 `<meta name=...>` 모두 |
| `meta.front_matter_extra` | MD 출신 | 표준 매핑 안 된 frontmatter |
| `tables[].context` | Excel `_META` 로 기술된 시험 컨텍스트 | method/condition/equipment/operator/date |
| `tables[].column_descriptions` | Excel `_GLOSSARY` | 컬럼별 설명 |
