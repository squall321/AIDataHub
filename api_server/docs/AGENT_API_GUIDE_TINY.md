# AGENT_API_GUIDE_TINY — Mobile eXperience AI Data Hub REST API

> TINY 모델 (1B~3B) 전용 압축 가이드. 4K 컨텍스트 안에 들어간다.
> 더 큰 모델은 `AGENT_API_GUIDE_SMALL.md` 보라.

Base URL: `http://localhost:8000`

---

## 1. 60초 시작 (cheatsheet)

```
인증 (auth):     헤더 (header) X-API-Key: <plaintext>
질의 (ask):      POST /api/ask   body={"query":"...","limit":5}
검색 (search):   GET  /api/search?mode=fts&q=<word>
레코드 (record): GET  /api/records/{id}
스키마 (schema): GET  /api/discover
첨부 (file):     GET  /api/records/{id}/attachments
헬스 (health):   GET  /api/system/health
```

읽어라 (read this):
- 모든 응답 (response) 은 JSON.
- ID 형식: `DOC-HE-CAE-2026-0000000001` (5 토큰, 하이픈 구분).
- 인증 실패 → 401. 없는 ID → 404.

DATA 한 줄 (one-line for DATA type):
```
카탈로그: GET /api/data?tags=...&domain=...&min_rows=10
행:       GET /api/data/{id}/rows?where=Region:Yield
컬럼:     GET /api/data/{id}/columns
통계:     GET /api/data/{id}/aggregate?op=max&column=Stress&group_by=Region
다축필터: GET /api/search/faceted?q=...&data_type=DATA&tags=...&min_quality=80
태그매칭: GET /api/search/by-tags?tags=IGA,NURBS&match=all
```
규칙: 평균/최대/최소 직접 계산 X — `aggregate` 한 번에. facets 응답 카운트가 다음 좁힘 후보.

의미 그룹 한 줄 (semantic groups one-line):
```
자동 그룹: POST /api/groups/auto body={"q":"...","n_groups":3}
이 record 같은 그룹: GET /api/records/{id}/cluster?mode=hybrid
여러 id 한방: POST /api/records/bulk body={"ids":[...]}
```
규칙: 비슷한 record 한 번에 받고 싶으면 `/api/groups/auto` 사용. 한 그룹 안의 record id 들을 `/api/records/bulk` 로 묶어 fetch.

---

## 2. 핵심 데이터 모델 (data model — 3 표만)

### 2.1 records (레코드, document/data 메타)

| 필드 (field) | 타입 (type) | 의미 (meaning) |
|---|---|---|
| `id` | str | 레코드 ID (예: `DOC-HE-CAE-2026-0000000001`) |
| `data_type` | str | DOC / DATA / SIM / CAD / LOG / FORM / OTHER |
| `title` | str | 제목 (title) |
| `summary` | str | 요약 (summary) |
| `tags` | str[] | 태그 배열 (tags) |
| `agents` | str[] | 사용 가능 에이전트 (agent types) |
| `content` | obj | 본문 (payload, data_type별 구조) |

### 2.2 record_sections (섹션, RAG chunk)

| 필드 | 타입 | 의미 |
|---|---|---|
| `record_id` | str | 부모 레코드 (parent id) |
| `section_id` | str | 섹션 식별자 (예: `S001`) |
| `title` | str | 섹션 제목 |
| `content_text` | str | 본문 평문 (text body) |
| `embedding` | vec | pgvector 임베딩 (있을 때) |

### 2.3 record_attachments (첨부, files)

| 필드 | 타입 | 의미 |
|---|---|---|
| `id` | str | `{record_id}-A001` 형식 |
| `kind` | str | figure / document / spreadsheet / data / cad / drawing / media / archive / other |
| `caption` | str | 캡션 (caption) |
| `file_path` | str | 정적 경로 (relative path) |
| `mime_type` | str | MIME 타입 |

첨부 바이너리 (binary) 는 `/attachments/{record_id}/A{nnn}.{ext}` 로 GET 하라.

---

## 3. 결정 트리 — 어떤 엔드포인트? (which endpoint?)

```
사용자 입력 (user input) 받았다 →

  1. 자연어 문장 (natural sentence, "찾아줘", "보여줘") ?
       YES → POST /api/ask   body={"query":"<문장>","limit":5}
       STOP.

  2. 정확한 ID (예: DOC-HE-CAE-2026-0000000001) 알고 있다 ?
       YES → GET /api/records/{id}
       STOP.

  3. 키워드 1~2개 (단어, "IGA", "battery") ?
       YES → GET /api/search?mode=fts&q=<word>
       STOP.

  4. 태그 알고 있다 (tag 정확히) ?
       YES → GET /api/search?mode=tag&tags=<t1>&tags=<t2>
       STOP.

  5. 의미 유사 검색 (의미만 비슷) 필요 ?
       YES → GET /api/search?mode=semantic&q=<word>
       503 응답 오면 → mode=fts 로 폴백 (fallback) 하라.
       STOP.

  6. 모르겠다 ?
       → GET /api/discover  로 카탈로그 (catalog) 먼저 보라.
```

규칙 (rule): 의심스러우면 `POST /api/ask` 부터 호출하라. 항상 동작한다.

---

## 4. 5가지 워크플로우 (workflows — cheatsheet)

### 워크플로우 1: 자연어 질문 → 답변 (NL → answer)

```
1. POST /api/ask   body={"query":"<user_text>","limit":5}
2. 응답.results[].id   ← record id 추출하라
3. 각 id 마다 → GET /api/records/{id}
4. 응답.follow_up_queries   ← 다음 질의 후보 표시하라
```

### 워크플로우 2: 키워드 검색 → 레코드 (keyword → records)

```
1. GET /api/search?mode=fts&q=<word>&limit=20
2. 응답.items[].record_id   ← id 추출하라
3. GET /api/records/{record_id}   ← 상세 조회하라
```

### 워크플로우 3: 태그 필터 → 레코드 (tag filter → records)

```
1. GET /api/discover   ← 보유 태그 분포 보라
2. GET /api/search?mode=tag&tags=<t1>&tags=<t2>&limit=20
3. 응답.items[].record_id   ← id 추출하라
```

### 워크플로우 4: 첨부 그림 다운로드 (download figure)

```
1. GET /api/records/{id}/attachments?kind=figure
2. 응답[i].id   ← 예: DOC-HE-CAE-2026-0000000001-A001
3. GET /attachments/{record_id}/A001.png   ← 정적 (static) 다운로드
```

### 워크플로우 5: 관련 레코드 찾기 (related records)

```
1. GET /api/records/{id}                              ← base.tags 추출하라
2. GET /api/search?mode=tag&tags=<base.tags[0]>       ← 태그 공유
3. GET /api/search?mode=semantic&q=<base.title>       ← 의미 유사
4. GET /api/records/{id}/lineage                      ← 조상/자손 (parents/children)
```

---

## 5. 응답 스키마 핵심 키 (response keys — 5개만)

레코드 (record) 응답:

```json
{"id":"DOC-HE-CAE-2026-0000000001","data_type":"DOC","title":"...","summary":"...","tags":["iga"],"content":{...}}
```

검색 (search) 응답:

```json
{"mode":"fts","q":"IGA","items":[{"record_id":"...","title":"...","snippet":"...","score":0.83}],"total":7,"limit":20,"offset":0}
```

자주 쓰는 키 (frequent keys):
- `id` 또는 `record_id` → 다음 호출의 path param 으로 쓰라.
- `items[]` → 검색 결과 배열 (search results array).
- `total` → 전체 건수 (total count).
- `score` → 유사도 (similarity), 0..1, 1=동일. semantic 모드에만 있음.
- `content_text` → 섹션 본문 (section body text).

`POST /api/ask` 응답 (response):

```json
{"interpreted_query":{"source":"llm","filters":{}},"results":[{...record}],"total_matched":7,"follow_up_queries":["..."]}
```

`results[]` 안 객체 (object) 는 record 와 같다. `results[].id` 를 추출하라.

---

## 6. 에러 처리 (error handling — 3 케이스만)

표준 응답 형식 (standard error shape):

```json
{"error":{"code":"...","message":"...","request_id":"..."}}
```

| HTTP | 의미 (meaning) | 즉시 행동 (action) |
|---|---|---|
| 401 | 인증 실패 (auth fail) | 헤더 `X-API-Key` 추가하라 |
| 404 | 없는 ID (not found) | id 다시 확인 → `GET /api/records?q=...` 로 재검색 |
| 422 | 검증 실패 (validation) | `details.errors[].loc` 와 `msg` 읽어서 필드 수정 |

기타 (etc):
- 503 + semantic search → `mode=fts` 로 폴백 (fallback) 하라.
- 409 (conflict) → 이미 존재. PATCH 하라 또는 다른 seq 쓰라.
- 429 (rate limit) → 잠시 대기 후 재시도 (retry).

모든 응답 헤더에 `X-Request-ID` 있다. 디버깅 (debug) 용으로 보고하라.

---

## 7. 검색 모드 4종 (search modes — 압축)

```
mode=fts       :  GET /api/search?mode=fts&q=<word>
                  전문 검색 (full-text), title+summary+section, 한국어 OK, 빠름.
                  기본 추천 (default choice).

mode=tag       :  GET /api/search?mode=tag&tags=<t1>&tags=<t2>
                  태그 AND (AND filter), 정확 매칭, 태그 알 때만.

mode=semantic  :  GET /api/search?mode=semantic&q=<word>
                  벡터 유사도 (vector cosine), 동의어/재구성 강함.
                  embedder 없으면 503. 폴백 → mode=fts.

keyword (legacy) :  GET /api/records?q=<word>
                    단순 ILIKE, recall 낮음. 마지막 폴백.
```

규칙 (rules):
- 자연어 질문 → `POST /api/ask` 가 위 4 모드를 알아서 고른다. 우선 ask 부터 시도하라.
- 결과 부족 (results 비어있다) → semantic → fts → tag 순으로 시도하라.

---

## 8. 자주 쓰는 endpoint 빠른 참조 (quick refs)

```
GET  /                         서비스 핑 (ping)
GET  /api/system/health        헬스 + 메타
GET  /api/discover             전체 카탈로그 (start here)
GET  /api/schema               JSON Schema (필드 메타)
GET  /api/hints                자연어 힌트 (NL hints)

POST /api/ask                  자연어 → 결과 (NL → results)
GET  /api/search               tag/fts/semantic
GET  /api/records              목록 (list, q/data_type/year/tag 필터)
GET  /api/records/{id}         단건 (single)
GET  /api/records/{id}/attachments    첨부 목록
GET  /api/records/{id}/lineage        조상/자손
GET  /api/agents               에이전트 목록
GET  /api/data?agent=<x>       agent-scoped 검색

GET  /api/taxonomy/tags                    태그 + 빈도 + 분포
GET  /api/taxonomy/tags/resolve?q=<word>   비공식 → 정식 태그 매핑
GET  /api/taxonomy/data-types              data_type 분포 + 추천
GET  /api/taxonomy/domains                 domain 분포
GET  /api/taxonomy/agents                  agent 분포 통계
GET  /api/taxonomy/classification          enum 의미 + 분포
GET  /api/taxonomy/status                  enum 의미 + 분포
```

쓰기 (write) — 필요시만 (only if needed):

```
POST  /api/records             body=RecordIn (직접 INSERT)
PATCH /api/records/{id}        body={"summary"?,"tags"?,"agents"?,...}
POST  /api/convert/ingest      multipart 파일 → JSON → DB (seq=0 로 자동 할당)
```

---

## 9. ID 형식 (ID format)

```
{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ}
예: DOC-HE-CAE-2026-0000000001
```

- `DATA_TYPE` : `DOC` / `DATA` / `SIM` / `CAD` / `LOG` / `FORM` / `OTHER`
- `TEAM` : 2~4 대문자 (예: `HE`, `EV`, `PT`, `DA`, `MX`, `VD`)
- `GROUP` : 2~5 대문자 (예: `CAE`, `Test`)
- `YEAR` : 4자리 (예: `2026`)
- `SEQ` : 6자리 zero-pad (예: `000001`)

첨부 ID (attachment id):

```
{record_id}-A{nnn}
예: DOC-HE-CAE-2026-0000000001-A001
```

규칙 (rule): ID 받으면 그대로 path param 으로 쓰라. 인용부호 (quote) 추가 X.

---

## 10. 핵심 enum (closed enums)

```
data_type      : DOC, DATA, SIM, CAD, LOG, FORM, OTHER
classification : public < internal (default) < confidential < restricted
status         : draft (default) → review → approved → deprecated
language       : ko (default), en, ja, zh, mixed
search.mode    : fts, tag, semantic
attachment.kind: figure, document, spreadsheet, media, archive, cad, drawing, data, other
```

값 (value) 모를 때 → `GET /api/meta/options` 호출하라. 모든 셀렉트박스 (dropdown) 옵션 반환 (returns).

---

## 11. 페이지네이션 (pagination)

```
limit  : 기본 (default) 20, 최대 (max) 100
offset : 0부터 시작
응답   : {"items":[...],"total":N,"limit":L,"offset":O}
```

마지막 페이지 (last page) 판단 (check):
- `offset + len(items) >= total` 이면 마지막이다. 더 호출하지 마라.

`/api/ask` 는 `limit` 만 받고 offset 없다 (default 5, max 50).

---

## 12. 한 화면 요약 카드 (one-screen card)

```
LOGIN     →  헤더 X-API-Key: <plaintext>
START     →  GET  /api/discover                     [start here]
ASK       →  POST /api/ask  body={"query":"...","limit":5}
GET       →  GET  /api/records/DOC-HE-CAE-2026-0000000001
SEARCH    →  GET  /api/search?mode=fts&q=<word>
TAG       →  GET  /api/search?mode=tag&tags=<t1>
SEMANTIC  →  GET  /api/search?mode=semantic&q=<word>
LIST      →  GET  /api/records?data_type=DOC&year=2026&limit=20
FILES     →  GET  /api/records/{id}/attachments
DOWNLOAD  →  GET  /attachments/{record_id}/A001.png
RELATED   →  GET  /api/records/{id}/lineage
META      →  GET  /api/meta/options                 (enum 값 모를 때)
HEALTH    →  GET  /api/system/health
ERROR     →  body.error.code + X-Request-ID
```

규칙 (rules) 3가지만 외우라 (memorize):
1. 자연어 (natural language) → `POST /api/ask`.
2. ID 알면 (know the id) → `GET /api/records/{id}`.
3. 모르겠으면 (when unsure) → `GET /api/discover` 부터.

---

## 13. 짧은 예시 (short examples — copy-paste 용)

### 13.1 자연어 질의 (NL ask) — 가장 흔함 (most common)

요청 (request):

```
POST /api/ask
Headers: X-API-Key: <key>
Body: {"query":"IGA 결과 보여줘","limit":3}
```

응답 (response, shape only):

```json
{"interpreted_query":{"source":"llm","filters":{"data_type":"DOC"}},"results":[{"id":"DOC-HE-CAE-2026-0000000001","title":"IGA tensile test report","summary":"..."}],"total_matched":3,"follow_up_queries":["IGA 시편 사진","IGA 시험 절차"]}
```

추출 (extract): `results[].id` → 다음 단계 `GET /api/records/{id}`.

### 13.2 키워드 검색 (FTS) — 단어 1개

요청:

```
GET /api/search?mode=fts&q=battery&limit=5
Headers: X-API-Key: <key>
```

응답 (shape):

```json
{"mode":"fts","q":"battery","items":[{"record_id":"DOC-EV-Battery-2026-0000000007","title":"...","snippet":"..."}],"total":1,"limit":5,"offset":0}
```

추출: `items[].record_id` → `GET /api/records/{record_id}`.

### 13.3 레코드 단건 (single record)

요청:

```
GET /api/records/DOC-HE-CAE-2026-0000000001
Headers: X-API-Key: <key>
```

응답: 위 §5 의 record 형태.

---

## 14. 흔한 실수 (common mistakes — 피하라)

1. `POST /api/ask` 는 body 가 `{"query":"..."}` 이다. **`q` 아니다.** 검색 (search) 의 query string 만 `q` 다.
2. ID 에 인용부호 (quote, `"`) 붙이지 마라. URL path 에 그대로 넣어라.
3. `tags` 는 배열 (array) 이다. URL 에서는 `?tags=a&tags=b` 로 반복 (repeat) 하라. 콤마 X.
4. semantic 503 받으면 다시 시도 (retry) 마라. 즉시 `mode=fts` 로 폴백 (fallback) 하라.
5. `X-API-Key` 헤더 (header) 빠지면 401. 모든 호출 (every call) 에 넣어라.

끝 (end of guide).
