# Plan — Team/Group Master Table + Dashboard CRUD

**Feature**: `team-group-mgmt`
**Phase**: Plan (PDCA)
**Date**: 2026-05-11
**Author**: koopark (with Claude Opus 4.7)

## Executive Summary

| Field | Value |
|------|-------|
| Feature | 조직 team/group 마스터 테이블 + CRUD API + 대시보드 관리 탭 |
| 기간 | 2026-05-11 (1일 추정, Plan/Design/Do 단일 세션) |
| 산출물 | alembic 0012, 라우터 2개, 시드 변환, 대시보드 새 탭, extension 캐시 무효화 |
| 영향 | meta/options 스키마 변경, seed/teams.py deprecate, records 자연키는 그대로 |

### Value Delivered (4-perspective)

| 관점 | 내용 |
|------|------|
| **Problem** | 조직 변경(팀 신설/병합/명칭변경) 발생 시 [api_server/src/api/seed/teams.py](../../api_server/src/api/seed/teams.py)를 직접 편집하고 서버를 재배포해야 함. 운영자(비개발자)는 변경 불가. |
| **Solution** | `org_teams`/`org_groups` 마스터 테이블 + REST CRUD + 대시보드 "조직 관리" 탭. meta/options가 DB 조회로 전환되어 VSCode extension은 코드 변경 없이 자동 반영. |
| **Function/UX Effect** | 대시보드 → "조직 관리" 탭 → team 추가/수정/삭제, group은 team에 종속해 트리 형태로 관리. 삭제 시 records 참조 검사. |
| **Core Value** | 운영자 자율성 + 데이터 무결성 (Strict 입력 정책 + Hard delete 안전 가드) |

## Decisions (확정)

1. **삭제 정책**: Hard delete, records 참조 시 409 Conflict
2. **team 입력 정책**: Strict — 마스터에 없는 team은 ingest 거부 (`ALLOW_CUSTOM.team = false` 유지)
3. **group 입력 정책**: 그대로 lenient 유지 (`ALLOW_CUSTOM.group = true`) — 향후 정책 변경 시 동일 패턴
4. **DB FK**: records 테이블에는 FK를 걸지 **않는다**. 이유: 기존 records 0010 마이그레이션 호환성 + lenient group 정책 유지를 위해 서비스 레이어 검증으로만 처리
5. **인증**: CRUD는 `AUTH_REQUIRED=true` 환경에서 API key 필수. read-only `/api/meta/options`는 현행 그대로 익명 허용
6. **seed**: 기존 [seed/teams.py](../../api_server/src/api/seed/teams.py) 값은 alembic `data_upgrade` 단계에서 마스터 테이블로 1회 이전. 파일은 fallback / legacy import 호환 목적으로 유지하되 deprecate 주석

## Scope

### In-scope

- DB: `org_teams` (code PK, name, description, created_at, updated_at), `org_groups` (code PK 또는 (team_code, code) composite, team_code FK to org_teams, name, description, created_at, updated_at)
- alembic 0012 — 테이블 생성 + 기존 seed 값 이전 + records의 distinct team/group 검증 (orphan 발견 시 경고만, 차단하지 않음)
- 라우터: `/api/org/teams` (GET list / POST / GET /{code} / PATCH /{code} / DELETE /{code}) + `/api/org/groups` 동일 패턴 (단 GET list는 team_code 필터)
- 변경: [routes/meta.py](../../api_server/src/api/routes/meta.py) `options` 가 `seed/teams.py` 대신 DB 조회로 전환
- 변경: ingest 경로 (records POST / convert / bundle) 에서 team 마스터 존재 검증 추가 (Strict)
- 대시보드: 새 탭 "**조직 관리**" — team 목록 표 + 추가/수정/삭제 모달, 선택된 team에 종속된 group 목록
- 캐시 무효화: meta/options 응답에 `ETag` 추가 → VSCode extension이 304 받으면 캐시 유지, 변경 감지 시 즉시 재요청 (확장 측 변경은 별도 PR)

### Out-of-scope

- VSCode extension 코드 수정 (`/api/meta/options` 응답 스키마는 호환성 유지 → 자동 수혜)
- records의 team/group rename cascade (마스터에서 team 이름만 바꿔도 records의 String 컬럼은 그대로. 별도 데이터 마이그레이션 API는 다음 사이클)
- 권한 세분화 (운영자/뷰어 RBAC) — 현재는 API key 단일 권한

## Implementation Phases

| Phase | 작업 | 검증 |
|------|------|------|
| 1 | alembic 0012 작성 + 시드 데이터 이전 | `alembic upgrade head` → `SELECT * FROM org_teams` 값 확인 |
| 2 | `db/models.py`에 `OrgTeam`, `OrgGroup` ORM 모델 추가 | python -c import 검증 |
| 3 | `routes/org.py` 라우터 (teams + groups) + service layer | `curl /api/org/teams` 응답 |
| 4 | `routes/meta.py` options를 DB 조회로 전환 | `/api/meta/options` 응답이 DB 값 반영 |
| 5 | ingest 검증 — `services/record_ingest.py` 등에서 team 마스터 존재 체크 | 미존재 team으로 POST → 422 |
| 6 | 대시보드 새 탭 (HTML + JS) | 브라우저에서 추가/수정/삭제 동작 |
| 7 | meta/options 응답에 ETag 추가 | `curl -I` 로 헤더 확인 |
| 8 | 회귀 테스트 + seed/teams.py deprecation 주석 | 기존 endpoint 응답 동일성 |

## Risks & Mitigations

| 리스크 | 대응 |
|------|------|
| 기존 records가 마스터에 없는 team/group 값을 갖고 있을 가능성 | 0012 마이그레이션에서 `SELECT DISTINCT team, group FROM records` 결과를 마스터에 자동 시드 + 로그 출력 |
| Strict 정책 도입 후 기존 ingest 워크플로 깨짐 | 단계 5 배포 전에 dry-run 모드 옵션 (`STRICT_TEAM_VALIDATION=false`) → 운영 후 true 전환 |
| VSCode extension이 옛 응답 스키마 가정 | 응답 키 `teams`/`groups` 그대로 유지 (값 출처만 DB로 전환). extension PR 불필요 |
| meta/options 캐시로 인한 변경 반영 지연 | ETag 추가 + 대시보드에서 변경 시 `Cache-Control: no-cache` 강제 재요청 |
| 동시 편집 | optimistic concurrency 없이 단순 last-write-wins. 운영자 수가 적어 허용 |

## Acceptance Criteria

- [ ] `bash deploy/apptainer/start_api.sh` → `alembic upgrade head` 자동 적용, 기존 6개 team / 14개 group 시드값이 `org_teams`/`org_groups`에 적재됨
- [ ] `curl http://127.0.0.1:8001/api/meta/options | jq .teams` 가 기존과 동일한 값
- [ ] `curl -X POST http://127.0.0.1:8001/api/org/teams -d '{"code":"NEW","name":"New Team"}'` 후 `/api/meta/options` 즉시 반영
- [ ] records가 1개라도 있는 team을 DELETE → 409 Conflict
- [ ] 마스터에 없는 team으로 record ingest → 422 Unprocessable Entity (Strict)
- [ ] 대시보드 "조직 관리" 탭에서 추가/수정/삭제 모두 동작
- [ ] gap-detector Match Rate ≥ 90%

## Open Questions

- `org_groups` PK를 `code` 단일로 할지, `(team_code, code)` composite로 할지? → **결정: composite** — 다른 팀에서 동일 group 코드 사용 가능 (예: HE/CAE, DA/CAE 같이)
- 대시보드 UI의 디자인 컨벤션 — 기존 records/agents 탭과 동일한 vanilla JS 패턴 유지

## Next

`docs/02-design/team-group-mgmt.md` 작성 (API 스펙 + DB schema + UI 와이어프레임) → Do (구현) → Gap analysis
