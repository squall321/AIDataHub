# Plan — `/api/ask` Keyword Map from DB Agents

**Feature**: `ask-keywords-from-db`
**Date**: 2026-05-11
**Predecessor**: [embedding-dim-upgrade](./embedding-dim-upgrade.md)

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | `_AGENT_KEYWORDS` 코드 상수를 `agents.common_tags` DB 조회 기반으로 동적 구성 |
| 동기 | team-group-mgmt 사이클과 동일 원칙 — 운영자가 agent CRUD 만으로 자연어 매칭 즉시 갱신 |
| 작업 규모 | discover_svc.py 단일 파일 (사전 제거 + async signature) + 캐시 |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | [discover_svc.py:813](../../api_server/src/api/services/discover_svc.py#L813) `_AGENT_KEYWORDS` 가 삭제된 5개 agent를 하드코딩 → /api/ask 가 미등록 agent로 filter 매겨서 0건 |
| **Solution** | `_get_agent_keywords(session)` 가 `agents.common_tags + agent_type` 을 DB에서 빌드. 5분 TTL in-memory 캐시. agent CRUD 즉시 반영 (캐시 invalidate은 TTL로 처리, 강한 정합 필요 없음) |
| **Function/UX Effect** | `/api/ask` 가 등록된 agent 어휘에만 반응. 운영자가 agent를 추가하면 5분 안에 자연어 매칭에 자동 합류 |
| **Core Value** | 메타데이터 운영자 자율성 일관 확장 (앞 사이클들과 동일 정신) |

## 결정 (확정)

1. **소스**: `agent.common_tags` + `agent.agent_type` 두 셋의 union, lowercase 정규화
2. **캐시**: module-level dict, TTL 300s (5분). agent 변경 시 다음 GET 까지 stale 허용
3. **signature**: `_interpret_keywords(query, session)` async, `interpret_query(query, session)` async
4. **LLM prompt** ([discover_svc.py:966](../../api_server/src/api/services/discover_svc.py#L966)): `_AGENT_KEYWORDS.keys()` → DB agent_type 목록 동적 주입
5. **타 사전** (`_DATA_TYPE_KEYWORDS`, `_CAPABILITY_KEYWORDS`): 그대로 (data_type/capability는 고정 enum)
6. **테스트** (`test_ask_route.py::test_interpret_keywords_unit`): 별도 사이클에서 정비 (현재 PoC는 통과 강제 안 함)

## Acceptance

- [ ] `POST /api/ask {"q":"LS-DYNA 자동화 도구"}` → KooRemapper_Manual 1건 매칭
- [ ] `POST /api/ask {"q":"메시 매핑"}` → KooRemapper_Manual 매칭
- [ ] 새 agent `foo-bar` 추가 후 5분 내 자연어 "foo bar" 매칭에 합류
- [ ] interpreted_query에 `agent: lsdyna-automation` 추정 표시

## Out-of-scope

- LLM 기반 해석 디버그 (OPENAI_API_KEY 없는 환경이라 모든 경로가 keyword fallback)
- agent 변경 시 즉시 캐시 무효화 (POST/PATCH/DELETE 훅) — TTL로 충분
- `_DATA_TYPE_KEYWORDS` / `_CAPABILITY_KEYWORDS` 동적화 (enum 고정, 가치 적음)
