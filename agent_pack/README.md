# Mobile eXperience AI Data Hub — Agent Direct-Connect Pack

> 다른 AI 에이전트가 본 시스템에 **즉시 직결** 하기 위한 자체 완결형 가이드 모음.
> 이 폴더만 있으면 추가 코드 없이 검색·발견·적재 가능.

## 0. 30초 접속 (Read me first)

이 시스템의 API 서버 주소는 다음으로 **하드코딩**되어 있다:

```text
http://110.15.177.125:8000
```

> URL 바꾸려면 한 줄 명령으로 전파: `python update_url.py http://new-server:8000` (또는 PowerShell `.\update_url.ps1`). [`CONFIG.md`](./CONFIG.md) 참조. 런타임 임시 override 는 `AIDH_API_URL` 환경변수.

헬스체크 (시작 직후 한 번):

```bash
curl http://110.15.177.125:8000/api/system/health
# → {"status":"ok","version":"...","auth_required":false,...}
```

응답 200 + `status="ok"` 이면 직결 성공. 인증 필요 (`auth_required=true`) 면 [`CONFIG.md`](./CONFIG.md) `X-API-Key` 절.

---

## 1. 본 폴더에 무엇이 있나

| 파일 | 용도 |
|---|---|
| [`README.md`](./README.md) | 본 문서 — 진입점 |
| [`CONFIG.md`](./CONFIG.md) | API 서버 URL · API 키 · 환경변수 |
| [`api_reference_compact.md`](./api_reference_compact.md) | 56 endpoint 한 페이지 요약 |
| [`schema_reference.md`](./schema_reference.md) | JSON 스키마 (메타·섹션·표·첨부) 핵심만 |
| [`patterns.md`](./patterns.md) | 자주 쓰는 호출 패턴 (검색·그룹·ingest) |
| [`examples/`](./examples/) | 동작하는 코드 예제 (.py / .sh / .ts) — 6 files |
| [`.api_url`](./.api_url) | 캐노니컬 API URL (sync 마커 — `update_url.py` 가 사용) |
| [`update_url.py`](./update_url.py) | URL 일괄 갱신 (Python, cross-platform) |
| [`update_url.ps1`](./update_url.ps1) | URL 일괄 갱신 (PowerShell) |

본 폴더는 **자체 완결**. 더 깊이 들어가야 할 경우 다음을 참조 (선택):

- [`/api/discover`](http://110.15.177.125:8000/api/discover) — 카탈로그 (런타임에서 직접 가져옴)
- [`/api/docs/agent-guide?size=small`](http://110.15.177.125:8000/api/docs/agent-guide?size=small) — 모델 사이즈별 markdown 가이드
- [`/dashboard`](http://110.15.177.125:8000/dashboard/) — 사람용 대시보드 (5탭)
- [`/docs`](http://110.15.177.125:8000/docs) — Swagger UI

---

## 2. 한 줄짜리 호출 (curl)

```bash
# 검색 (semantic — 한국어/영어 cross-lingual)
curl "http://110.15.177.125:8000/api/search?mode=semantic&q=KooRemapper&limit=5"

# 검색 (FTS — 정확 매칭)
curl "http://110.15.177.125:8000/api/search?mode=fts&q=stress&limit=10"

# 검색 (tag — 정확 매칭)
curl "http://110.15.177.125:8000/api/search?mode=tag&tags=IGA&tags=NURBS&limit=5"

# 단일 record 본문
curl http://110.15.177.125:8000/api/records/DOC-HE-CAE-2026-0000000001

# 자동 그룹 (semantic 클러스터)
curl -X POST http://110.15.177.125:8000/api/groups/auto \
  -H "Content-Type: application/json" \
  -d '{"q":"체크리스트","n_groups":3,"top_k":50}'

# 카탈로그 (필터 가능)
curl "http://110.15.177.125:8000/api/records?data_type=DOC&tag=IGA&limit=20"
```

---

## 3. 한 줄짜리 호출 (Python)

```python
import urllib.request, json
BASE = "http://110.15.177.125:8000"

# 검색
with urllib.request.urlopen(f"{BASE}/api/search?mode=semantic&q=KooRemapper&limit=3") as r:
    data = json.load(r)
for it in data["items"]:
    print(it["record_id"], "-", it["title"])
```

전체 예제는 [`examples/python_client.py`](./examples/python_client.py).

---

## 4. 가장 중요한 5개 엔드포인트

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/discover` | 카탈로그 — 무엇이 있는지 1회 호출로 파악 |
| `GET` | `/api/search?mode={semantic\|fts\|tag}&q=...` | 검색 — 가장 자주 호출 |
| `GET` | `/api/records/{id}` | 단일 record 본문 + 첨부 |
| `POST` | `/api/groups/auto` | 의미 클러스터 — 카테고리화 |
| `GET` | `/api/records?{filter}` | 카탈로그 필터링 (data_type/tag/agent/classification) |

자세한 56 endpoint 표는 [`api_reference_compact.md`](./api_reference_compact.md).

---

## 5. 답변 데이터 구조 한눈에

검색 응답 (`/api/search`):

```jsonc
{
  "mode": "semantic",
  "items": [
    {
      "record_id": "DOC-HE-CAE-2026-0000000001",
      "title": "KooRemapper Manual",
      "snippet": "…응력(stress) 변형률(strain)…",
      "score": 0.964,
      "section_id": "16.3",
      "tags": ["KooRemapper","IGA","NURBS"]
    }
  ],
  "total": 7
}
```

단일 record 응답 (`/api/records/{id}`):

```jsonc
{
  "id": "DOC-HE-CAE-2026-0000000001",
  "data_type": "DOC",
  "title": "KooRemapper Manual",
  "summary": "...",
  "tags": [...],
  "agents": [...],
  "classification": "internal",
  "domain": "CAE",
  "language": "ko",
  "content": {
    "sections": [...],
    "tables": [...],
    "attachments": [...],
    "sources": [...]
  }
}
```

전체 스키마는 [`schema_reference.md`](./schema_reference.md).

---

## 6. 메타 필드 활용 (그룹·분류 슬라이스)

작은 모델이 효율적으로 검색을 좁히려면 다음 메타 필드를 쿼리 파라미터로 넘긴다:

| 메타 필드 | 쿼리 파라미터 | 예시 |
|---|---|---|
| `data_type` | `?data_type=DOC` | `DOC` / `DATA` / `SIM` / `CAD` / `LOG` / `FORM` / `OTHER` |
| `tags` | `?tag=X&tag=Y` (반복) | `?tag=group:CAE&tag=checklist` |
| `agents` | `?agent=...` | `?agent=iga-analyst` |
| `classification` | (클라이언트 후필터) | `confidential` / `internal` / `public` / `restricted` |
| `domain` | (클라이언트 후필터) | `CAE` / `safety` / `procurement` |
| `status` | (클라이언트 후필터) | `draft` / `review` / `approved` / `deprecated` |

**그룹 단위 발췌 패턴** (예: CAE 팀 체크리스트만):

```bash
curl "http://110.15.177.125:8000/api/records?data_type=DOC&tag=group:CAE&tag=checklist"
```

자세한 패턴 모음은 [`patterns.md`](./patterns.md).

---

## 7. 트러블슈팅

| 증상 | 원인 / 대응 |
|---|---|
| `Connection refused` | 서버가 안 떠있음. 운영자에게 확인. |
| `404` on `/api/...` | URL prefix 확인 — `/api/` 누락 흔함. |
| `401` / `403` | `X-API-Key` 헤더 필요. [`CONFIG.md`](./CONFIG.md) 참조. |
| `422 VALIDATION_ERROR` | 요청 본문 스키마 위반. 응답 body 의 `details.errors[]` 확인. |
| `500 INTERNAL_ERROR` | 서버 측 버그. 운영자에게 request_id 전달. |
| 한글 검색 결과 0건 | `mode=semantic` 시도 (한↔영 cross-lingual 가능, e5_small 임베딩) |

---

## 8. 변경 이력

- **2026-05-10** — Pack v1.0 초기 생성. URL 하드코딩, 56 endpoint 요약, 6 examples.
- 향후 변경은 [`CONVERSION_RULES_INDEX.md`](../CONVERSION_RULES_INDEX.md) §6 changelog 와 동기화.

---

*본 폴더는 [d:/Personal/AI_data/](../) 의 부분집합 중 "agent 직결용 자체 완결 패키지" 이다. 풀 시스템 문서는 [`../CONVERSION_RULES_INDEX.md`](../CONVERSION_RULES_INDEX.md) 부터 시작.*
