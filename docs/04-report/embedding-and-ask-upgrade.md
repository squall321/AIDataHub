# Report — Embedding Upgrade + /api/ask DB Wiring (PDCA A + B)

**Date**: 2026-05-11
**Cycles**: [embedding-dim-upgrade](../01-plan/embedding-dim-upgrade.md), [ask-keywords-from-db](../01-plan/ask-keywords-from-db.md)
**Combined**: 두 사이클이 같은 운영 사상(코드 상수 → DB/환경 외부화)에 속해 함께 보고

## Executive Summary

| Field | Value |
|-------|-------|
| Feature A | 임베딩 dim 384→768 + 모델 e5_small→e5_base, `EMBEDDING_DIM` 환경변수화 |
| Feature B | `/api/ask` 의 `_AGENT_KEYWORDS` 코드 상수 → DB(`agents.common_tags`) 기반 동적 구성 + 5분 TTL 캐시 + `STANDARD_AGENTS` 자동 시드 제거 |
| 작업 규모 | A: alembic 0013 + 4 파일 / B: 2 파일 + 1 정책 변경 |
| 검증 | A: 의미 매칭 score 0.93→0.95 / B: 자연어 9종 모두 0건 → 1건 정확 매칭 |

### Value Delivered

| 관점 | A (embedding) | B (ask DB) |
|------|----|----|
| **Problem** | dim 384 고정, e5_small 한계 | 삭제된 agent가 코드 상수로 박혀 `/api/ask` 가 잘못된 filter 강제. agent CRUD가 자연어 매칭에 반영 안 됨. |
| **Solution** | `vector(768)` 컬럼 확장 + `EMBEDDING_DIM` 환경변수 + provider→dim 자동 매핑 (small/base/large) | `_AGENT_KEYWORDS` 제거 → `agents.common_tags + agent_type` 으로 DB 동적 구성. `STANDARD_AGENTS=[]` 로 자동 시드 부활 차단. agent 매겨지면 data_type filter skip 정책. |
| **Function/UX Effect** | 의미 score 분해능 ↑. 한국어 의역·오타 매칭 강건 | 자연어 쿼리가 등록된 agent 어휘에 즉시 반응. 운영자가 agent CRUD 만으로 5분 내 자동 합류. |
| **Core Value** | 임베딩 품질 / large 로의 확장 경로 표준화 | 운영자 자율성을 ask 파이프라인까지 확장 (team-group-mgmt 정신 일관 확장) |

## A 변경 (embedding-dim-upgrade)

| 파일 | 변경 |
|------|------|
| [alembic 0013](../../api_server/alembic/versions/0013_embedding_dim_768.py) | `record_sections.embedding vector(384)→vector(768)`, 임베딩 NULL reset |
| [services/embedding.py](../../api_server/src/api/services/embedding.py) | `EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))`, `e5_base`/`e5_large` 매핑 |
| [db/models.py](../../api_server/src/api/db/models.py) | `_Vector(_EMBEDDING_DIM)` |
| [.env.example](../../deploy/apptainer/.env.example) | 4 임베더 옵션 주석 + `EMBEDDING_DIM` |
| [.env](../../deploy/apptainer/.env) | `EMBEDDING_PROVIDER=e5_base`, `EMBEDDING_DIM=768` |
| [start_api.sh](../../deploy/apptainer/start_api.sh) | provider→dim 자동 매핑, sentence-transformers 자동 설치 |

### A 검증

```
컬럼 dim:  vector(768) ✓
backfill:  173 sections / 7~77초 / model=multilingual-e5-base-d768 ✓
의미 score: 0.93~0.95 (e5_small 대비 ↑)
의역 매칭: "구조해석 자동화 도구" / "고리매퍼" / "그리드 변환" 모두 0.93+ ✓
```

## B 변경 (ask-keywords-from-db)

| 파일 | 변경 |
|------|------|
| [services/discover_svc.py](../../api_server/src/api/services/discover_svc.py) | `_AGENT_KEYWORDS` 제거, `_get_agent_keywords(session)` 추가 (5분 TTL 모듈 캐시), `_interpret_keywords` / `interpret_query` / `_interpret_with_llm` 모두 `session` 받도록 변경. agent 매겨지면 data_type 매기지 않는 정책. |
| [seed/agents_data.py](../../api_server/src/api/seed/agents_data.py) | `STANDARD_AGENTS = []` (자동 시드 제거 — 운영자가 REST/대시보드로 관리). 5개 표준 agent 하드코딩 삭제. |

### B 검증

| 쿼리 | 전 | 후 |
|------|----|----|
| "메시 매핑" | 0건 | 1건 (agent=lsdyna-automation, score=keyword) |
| "LS-DYNA 메시 매핑하는 방법" | 0건 (data_type=SIM 강제 → 충돌) | 1건 |
| "리매퍼" / "고리매퍼" / "구조해석 자동화 도구" | 0건 | 1건 매칭 |
| 5개 옛 agent (DB→삭제→재기동 후 부활) | seed 가 자동 복원 | seed 빈 리스트 → 영구 정리 |

### Side-effect 발견 + 해결

API 재기동 시 `python -m api.seed -v` 가 매번 실행되어 사용자가 삭제한 5개 표준 agent를 멱등 upsert로 부활시키는 부작용 — `STANDARD_AGENTS=[]` 로 차단.
이전 사이클 (team-group-mgmt) 의 `seed/teams.py` 와 같은 패턴 — 코드 상수 시드는 운영자 자율 관리 정책과 충돌하므로 deprecate 일관 적용.

## 통합 Acceptance

- [x] `vector(768)` 컬럼 검증
- [x] e5_base 모델 로드 + 173 sections 백필
- [x] e5_small 대비 의미 score 향상 (대표 쿼리 7건)
- [x] `_AGENT_KEYWORDS` 코드에서 완전 제거
- [x] `STANDARD_AGENTS = []` 로 자동 시드 영구 비활성화
- [x] `/api/ask` 자연어 9종 모두 agent=lsdyna-automation 으로 정확히 좁혀짐
- [x] 운영자 agent CRUD 5분 내 자연어 매칭에 반영

## 후속

- LLM 기반 ask 해석 검증 (`OPENAI_API_KEY` 필요한 환경)
- agent 변경 직후 즉시 캐시 invalidate 훅 (현재는 TTL 자연 만료)
- `_DATA_TYPE_KEYWORDS` / `_CAPABILITY_KEYWORDS` 도 enum 변경 추적 도구화 (가치 낮음)
- record 추가 적재 후 의미 매칭 변별력 재측정 (record 1건 PoC 한계)

## 결론

두 사이클로 ① 의미 매칭 품질, ② 운영자 자율성 — 두 축이 모두 동시 향상. 모든 변경이 환경변수 또는 DB로 외부화되어 코드 수정 없이 운영 의도 변경 가능한 상태에 도달. team-group-mgmt 사이클이 시작한 "코드 상수 → 운영 가능 객체" 정신이 임베딩/검색 파이프라인까지 일관 적용됨.
