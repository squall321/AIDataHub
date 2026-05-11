# 자주 쓰는 호출 패턴

> 본 시스템에서 agent 가 가장 빈번하게 사용하는 검색·발견·분류 패턴 모음.
> 모든 예제는 `BASE = http://110.15.177.125:8000` 가정.

---

## 1. "이 시스템에 무엇이 있나?" — 1회 발견 시퀀스

```bash
BASE="http://110.15.177.125:8000"

# 1단계: 카탈로그 — 전체 분포
curl -s "$BASE/api/discover" | jq

# 2단계: tag 분포 (어느 토픽이 풍부한지)
curl -s "$BASE/api/taxonomy/tags?limit=30" | jq

# 3단계: data_type 분포
curl -s "$BASE/api/taxonomy/data-types" | jq

# 4단계: agent 등록 목록
curl -s "$BASE/api/taxonomy/agents" | jq
```

이 4번 호출로 시스템 윤곽 파악 완료.

---

## 2. 자연어 → 답 — `/api/ask`

```bash
curl -s -X POST "$BASE/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"q":"KooRemapper 의 IGA 옵션 사용법 알려줘"}' | jq
```

응답 예시:

```jsonc
{
  "interpreted_query": {
    "mode": "semantic",
    "tags": ["KooRemapper", "IGA"],
    "data_types": ["DOC"]
  },
  "results": [...],
  "summary": "KooRemapper 는 ..."
}
```

---

## 3. 시맨틱 검색 — 한↔영 cross-lingual

```bash
# 영어로 질의 → 한글 record 매칭
curl -s "$BASE/api/search?mode=semantic&q=stress%20strain%20curve&limit=5" | jq '.items'
# → SS400 인장시험 결과 (한글 record) 리턴

# 한글로 질의 → 영문 record 매칭
curl -s "$BASE/api/search?mode=semantic&q=%EC%9D%91%EB%A0%A5%20%EB%B3%80%ED%98%95%EB%A5%A0&limit=5" | jq '.items'
```

`e5_small` 임베딩이 한↔영 동일 의미를 약 0.92~0.96 유사도로 매칭.

---

## 4. FTS 검색 — 정확한 키워드 매칭

```bash
curl -s "$BASE/api/search?mode=fts&q=KooRemapper&limit=10" | jq '.items'
```

PG `to_tsvector('simple', ...)` 기반. 토큰 단위 매칭이 필요할 때.

---

## 5. tag 검색 — AND/OR

```bash
# AND (기본): 모든 tag 매칭
curl -s "$BASE/api/search?mode=tag&tags=IGA&tags=NURBS&limit=5" | jq '.items'

# OR: any 모드 (별도 endpoint)
curl -s "$BASE/api/search/by-tags?tags=IGA,NURBS&match=any&limit=5" | jq
```

---

## 6. faceted 검색 — facet 카운트로 다음 좁힘 안내

```bash
curl -s "$BASE/api/search/faceted?q=%EC%B2%B4%ED%81%AC%EB%A6%AC%EC%8A%A4%ED%8A%B8&mode=semantic&limit=20" | jq
```

응답:

```jsonc
{
  "items": [...20개],
  "facets": {
    "data_type": {"DOC": 18, "REPORT": 2},
    "tags": {"checklist": 20, "group:CAE": 7, "company-wide": 5},
    "domain": {"CAE": 7, "ops": 5, "safety": 3}
  }
}
```

→ "CAE 그룹만 보고 싶으면 `tag=group:CAE` 추가" 식으로 좁힐 수 있음을 안내.

---

## 7. 다축 필터 (data_type + classification + agent)

```bash
# DOC + 인증된 + 특정 agent 가 소비하는 record
curl -s "$BASE/api/search/faceted?data_type=DOC&classification=approved&agent=iga-analyst&limit=20" | jq '.items'

# 카탈로그 라우트로 (filter 만, 검색 없이)
curl -s "$BASE/api/records?data_type=DOC&tag=IGA&agent=iga-analyst&limit=20" | jq '.items'
```

---

## 8. 자동 그룹화 — semantic clustering

```bash
curl -s -X POST "$BASE/api/groups/auto" \
  -H "Content-Type: application/json" \
  -d '{"q":"체크리스트","n_groups":3,"top_k":50}' | jq
```

응답:

```jsonc
{
  "query": "체크리스트",
  "groups": [
    {"label": "전사 표준 체크리스트", "size": 18, "common_tags": ["company-wide"]},
    {"label": "CAE 그룹 체크리스트", "size": 7, "common_domain": "CAE"},
    {"label": "안전 체크리스트", "size": 5, "common_tags": ["safety"]}
  ]
}
```

---

## 9. 단일 record 의 시맨틱 이웃

```bash
curl -s "$BASE/api/records/DOC-HE-CAE-2026-0000000001/cluster?top_k=10" | jq
```

해당 record 와 가장 가까운 10개 record (cosine 거리 순).

---

## 10. 그룹 단위 발췌 (작성 표준)

명세 권장 작성 표준 (META_FORMAT_AUDIT.md 참조):

| 메타 필드 | 형식 | 의미 |
|---|---|---|
| `tags` | `["group:<코드>"]` | 그룹 식별 (예: `group:CAE`, `group:ops`) |
| `tags` | `["scope:<범위>"]` | 적용 범위 (예: `scope:company-wide`, `scope:group`) |
| `tags` | `["checklist"]`, `["procedure"]`, ... | 문서 종류 |
| `classification` | `internal`/`restricted-<group>` | 권한 등급 |

발췌 호출:

```bash
# CAE 그룹의 체크리스트만
curl -s "$BASE/api/records?tag=group:CAE&tag=checklist" | jq '.items[].id'

# 전사 공통 체크리스트만
curl -s "$BASE/api/records?tag=scope:company-wide&tag=checklist" | jq '.items[].id'

# CAE 그룹의 + approved 상태인 + iga-analyst 가 보는
curl -s "$BASE/api/search/faceted?tag=group:CAE&status=approved&agent=iga-analyst" | jq
```

---

## 11. 단일 record 본문 + 첨부

```bash
# Record 본문 (sections 트리 + tables + attachments)
curl -s "$BASE/api/records/DOC-HE-CAE-2026-0000000001" | jq '.content.sections[0]'

# 첨부 이미지 직접 다운로드
curl -O "$BASE/attachments/DOC-HE-CAE-2026-0000000001/A001.png"

# 그림 (figures 별칭)
curl -O "$BASE/figures/DOC-HE-CAE-2026-0000000001/F001.png"
```

---

## 12. Excel 데이터 행/집계

```bash
# 표 행 (페이지네이션)
curl -s "$BASE/api/data/DATA-HE-CAE-2026-0000000034/rows?limit=100" | jq

# 컬럼 메타 (단위·설명)
curl -s "$BASE/api/data/DATA-HE-CAE-2026-0000000034/columns" | jq

# 집계 (예: stress 평균)
curl -s "$BASE/api/data/DATA-HE-CAE-2026-0000000034/aggregate?func=avg&col=stress" | jq
```

---

## 13. 새 문서 적재 (ingest) — 3가지 경로

### A. CLI 변환기 직접 사용 (서버 거치지 않음, 가장 빠름)

```bash
cd api_server
.venv/Scripts/python.exe -m converter input.docx \
  --team HE --group CAE --year 2026 --seq 7 \
  --agents iga-analyst,doc-curator \
  --tags KooRemapper,IGA --output-dir output
```

출력: `output/DOC-HE-CAE-2026-0000000007.json` + `output/DOC-HE-CAE-2026-0000000007/F*.png` + ...

### B. HTTP 원본 업로드 → 서버 변환 + 적재

```bash
curl -X POST "$BASE/api/convert/ingest" \
  -F "file=@input.docx" \
  -F "team=HE" \
  -F "group=CAE" \
  -F "year=2026" \
  -F "seq=7" \
  -F "agents=iga-analyst,doc-curator" \
  -F "tags=KooRemapper,IGA"
```

서버가 변환기를 직접 실행. 옵션: `?ocr=true` (PDF), `?detect_multi_tables=true` (Excel).

### C. 사전 변환된 ZIP 번들 업로드 (A + B 의 하이브리드)

A 경로로 변환해두고 (또는 작성자가 손으로 만든 JSON), `output/` 폴더를 zip 압축해 업로드:

```bash
# 변환기 출력 폴더 통째로 zip
cd output
zip -r DOC-HE-CAE-2026-0000000007.zip \
  DOC-HE-CAE-2026-0000000007.json \
  DOC-HE-CAE-2026-0000000007/

# 업로드
curl -X POST "$BASE/api/ingest/bundle" \
  -F "file=@DOC-HE-CAE-2026-0000000007.zip"
```

응답:

```jsonc
{
  "id": "DOC-HE-CAE-2026-0000000007",
  "data_type": "DOC",
  "title": "...",
  "figures_copied": 12,
  "attachments_copied": 3,
  "warnings": {
    "missing_resources": [],   // JSON 이 참조하나 zip 에 없는 파일
    "extra_resources": []      // zip 에 있으나 JSON 이 참조 안 한 파일
  }
}
```

서버가 zip 해제 → `normalize` → DB 적재 → `figures_dir/{doc_id}/` 와 `attachments_dir/{doc_id}/` 에 자원 자동 배치. 정적 마운트 `/figures/{doc_id}/F001.png`, `/attachments/{doc_id}/A001.csv` 로 즉시 서빙.

Python 헬퍼: [`examples/bundle_upload.py`](./examples/bundle_upload.py) — JSON 경로만 주면 자동으로 zip 만들어 업로드.

```bash
python examples/bundle_upload.py output/DOC-HE-CAE-2026-0000000007.json
```

### 언제 어느 경로?

| 상황 | 권장 |
|---|---|
| 새 .docx 를 처음 변환 + 적재 | B (HTTP 업로드) |
| 이미 CLI 로 변환해 둔 폴더 | C (zip 번들) |
| 대량 일괄 적재 (네트워크 부담) | A (서버 머신에서 CLI) |
| 변환 결과 검수 → 사후 적재 | C (zip 번들 — 검수된 JSON 그대로) |
| 작성자가 직접 작성한 JSON (변환기 안 돌림) | C (zip 번들) |

PDF 의 OCR 적용:

```bash
curl -X POST "$BASE/api/convert/ingest?ocr=true&ocr_lang=eng+kor" \
  -F "file=@scan.pdf" \
  -F "team=HE" -F "group=CAE" -F "year=2026" -F "seq=8"
```

Excel 다중 표:

```bash
curl -X POST "$BASE/api/convert/ingest?detect_multi_tables=true" \
  -F "file=@multi.xlsx" \
  -F "team=HE" -F "group=MFG" -F "year=2026" -F "seq=12"
```

---

## 14. 메타 패치 (record 보정)

agent 가 적재 후 메타를 보강할 때 (예: `agent_hints` 추가):

```bash
curl -X PATCH "$BASE/api/records/DOC-HE-CAE-2026-0000000001" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_hints": "이 매뉴얼은 IGA 변환의 standard reference. 예제는 16장 이후.",
    "query_examples": ["IGA 변환 옵션", "NURBS 설정"]
  }'
```

---

## 15. 백그라운드 잡 (대량 임베딩)

```bash
# 임베더 변경 후 전체 record 재임베딩
curl -X POST "$BASE/api/jobs" \
  -H "Content-Type: application/json" \
  -d '{"kind":"embed-backfill","model":"e5_small"}'

# 잡 상태 확인
curl -s "$BASE/api/jobs/<job_id>" | jq
```

---

## 16. 작은 모델 진입 시퀀스 (권장 순서)

```text
0. /api/system/health                         # 살아있는지 확인
1. /api/discover                              # 카탈로그 흡수 (1회)
2. /api/docs/agent-guide?size=small           # 모델 사이즈에 맞는 가이드
3. (필요할 때) /api/search?mode=semantic&q=  # 검색 시작
4. /api/search/faceted                        # 좁히기 가이드 (facet)
5. /api/records/{id}                          # 본문 흡수
6. /api/groups/auto                           # 카테고리 묶음 (옵션)
```

각 단계 응답이 다음 호출의 입력을 설계해 준다 — agent 는 단계마다 재고만 한다.
