# Plan — Embedding Dimension Upgrade (384 → 768 / e5_small → e5_base)

**Feature**: `embedding-dim-upgrade`
**Date**: 2026-05-11
**Predecessor**: [team-group-mgmt](./team-group-mgmt.md)

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | 임베딩 차원 384→768 확장, 기본 모델 e5_small→e5_base, dim 코드 의존성 환경변수화 |
| 동기 | 한국어 도메인 record 적재량 증가 대비 의미 매칭 품질 향상 |
| 작업 규모 | alembic 0013 + 3 파일 편집 + backfill |
| 위험 | 기존 임베딩 모두 NULL 리셋 후 재계산 (1회성) |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | 현재 `vector(384)` + e5_small. 한국어 도메인 변별력은 충분하지만 적재 record 가 늘면 미세 의미 차이 구분이 약해짐. |
| **Solution** | pgvector 컬럼을 `vector(768)` 로 확장 + e5_base (`intfloat/multilingual-e5-base`) 로 default 모델 전환. `EMBEDDING_DIM` 환경변수로 코드 dim 의존부 외부화. |
| **Function/UX Effect** | 의미 매칭 score 분해능 ↑. `/api/search?mode=semantic` top-k 변별 향상. |
| **Core Value** | 운영 단계에서 record 수 확장에 따른 의미 검색 품질 보전 + 향후 large 로의 마이그레이션 절차 표준화. |

## 결정 (확정)

1. **dim**: 768 (e5_base 출력 그대로)
2. **모델**: `intfloat/multilingual-e5-base`
3. **기존 임베딩**: NULL 리셋 후 백필 (1 record / 173 sections — ~10초)
4. **dim 외부화**: `EMBEDDING_DIM` 환경변수 (default 384, e5_base 시 768)
5. **자동 매핑**: `start_api.sh` 가 `EMBEDDING_PROVIDER=e5_base` 면 자동으로 `EMBEDDING_DIM=768` 설정
6. **롤백**: alembic downgrade → vector(384) 로 복원 + 임베딩 재계산 (small 또는 hash)

## 영향 파일

| 파일 | 변경 |
|------|------|
| `api_server/alembic/versions/0013_embedding_dim_768.py` | NEW — ALTER TYPE + 임베딩 NULL reset |
| `api_server/src/api/services/embedding.py` | `EMBEDDING_DIM` env-driven + `e5_base` 모델 매핑 추가 |
| `api_server/src/api/db/models.py` | `_Vector(384)` → `_Vector(EMBEDDING_DIM)` |
| `deploy/apptainer/.env.example` | `EMBEDDING_DIM=768` 추가 + 옵션 주석 |
| `deploy/apptainer/.env` | `EMBEDDING_PROVIDER=e5_base` + `EMBEDDING_DIM=768` |
| `deploy/apptainer/start_api.sh` | provider→dim 자동 매핑 (`e5_small=384`, `e5_base=768`) |

## Acceptance

- [ ] `alembic upgrade head` 멱등 적용
- [ ] `SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='record_sections'::regclass AND attname='embedding'` → `vector(768)`
- [ ] POST `/api/jobs/embed` 빈 body → 173 sections re-embedded (model=`...multilingual-e5-base-d768`)
- [ ] `/api/search?mode=semantic&q=메시를 한층씩 매핑` → 매칭 + score 0.85~0.95 범위
