# Report — Team/Group Master Table + Dashboard CRUD

**Feature**: `team-group-mgmt`
**PDCA Cycle**: Plan → Design → Do → Check (Gap) → Act (Report)
**Date**: 2026-05-11
**Match Rate**: **96%** (gap-detector 분석)

## Executive Summary

| Field | Value |
|------|-------|
| Feature | 조직 team/group 마스터 테이블 + REST CRUD + 대시보드 관리 탭 |
| 시작 → 완료 | 2026-05-11 단일 세션 |
| Match Rate | 96% (의도 일치, 1건 설계 대비 개선 deviation) |
| Plan/Design 문서 | [01-plan/team-group-mgmt.md](../01-plan/team-group-mgmt.md), [02-design/team-group-mgmt.md](../02-design/team-group-mgmt.md) |
| 코드 변경 파일 | 13개 (신규 3, 편집 10) |
| 실제 등록 데이터 | 4 teams (HE, CCT, SP1, SP2), 17 groups |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | 조직 변경 시 [seed/teams.py](../../api_server/src/api/seed/teams.py) 직접 편집 + 서버 재배포 필요. 운영자(비개발자)는 변경 불가. |
| **Solution** | `org_teams`/`org_groups` 마스터 테이블 + REST CRUD + 대시보드 "조직 관리" 탭. meta/options 가 DB 조회로 전환되어 VSCode extension 은 코드 변경 없이 자동 수혜. |
| **Function/UX Effect** | 대시보드 → 5번째 탭 "조직 관리" → team/group 추가·수정·삭제. 삭제 시 records 참조 검사. ETag 로 conditional 304. |
| **Core Value** | 운영자 자율성 + 데이터 무결성 (Strict + Hard delete 가드) + VSCode 확장 무수정 호환 |

## 변경 산출물

### 신규 파일

| 파일 | LOC | 역할 |
|------|----|------|
| [alembic/versions/0012_org_master.py](../../api_server/alembic/versions/0012_org_master.py) | 167 | DDL + seed 이전 (6 teams / 14 groups) + orphan 경고 |
| [api/services/org_svc.py](../../api_server/src/api/services/org_svc.py) | 89 | `validate_team_group` + count helpers |
| [api/routes/org.py](../../api_server/src/api/routes/org.py) | 332 | 9개 endpoint CRUD (teams + groups) |

### 편집

| 파일 | 동작 |
|------|------|
| [api/db/models.py](../../api_server/src/api/db/models.py) | `OrgTeam`, `OrgGroup` ORM 추가 (composite PK on org_groups) |
| [api/routes/_schemas.py](../../api_server/src/api/routes/_schemas.py) | OrgTeamIn/Out/Patch + OrgGroupIn/Out/Patch |
| [api/routes/__init__.py](../../api_server/src/api/routes/__init__.py) | org import + register |
| [api/routes/meta.py](../../api_server/src/api/routes/meta.py) | options DB 조회 전환 + ETag + 304 |
| [api/routes/records.py](../../api_server/src/api/routes/records.py) | create_record 에 validate_team_group |
| [api/ingest/db_writer.py](../../api_server/src/api/ingest/db_writer.py) | 단일 chokepoint 에 validate_team_group (bundle + convert 자동 수혜) |
| [api/config.py](../../api_server/src/api/config.py) | `strict_team_validation: bool = True` |
| [api/seed/teams.py](../../api_server/src/api/seed/teams.py) | DEPRECATED 주석 |
| [static/dashboard/index.html](../../api_server/static/dashboard/index.html) | "조직 관리" 탭 + 모달 |
| [static/dashboard/dashboard.js](../../api_server/static/dashboard/dashboard.js) | loadOrg + Teams/Groups 렌더링 + CRUD 핸들러 |

## 확정 정책 (Plan 대비 그대로 유지)

- Hard delete + records/groups 참조 시 409 Conflict
- Strict team 검증 (`ALLOW_CUSTOM.team=false`, group lenient)
- records ↔ org_* 간 FK 없음 (service-layer 검증)
- composite PK `(team_code, code)` on org_groups
- 인증: `get_principal` (AUTH_REQUIRED=false 면 anonymous 허용, true 면 키 요구)

## 검증 결과

| 시나리오 | 결과 |
|---------|------|
| alembic 0012 적용 | 성공 (6 teams / 14 groups 시드 이전) |
| `/api/meta/options` DB 출처 | 정상 (teams/groups 응답 키 동일) |
| ETag conditional GET | 304 응답 OK |
| POST `/api/org/teams` 신규 (CCT/SP1/SP2) | 201 × 3 |
| POST `/api/org/groups` 신규 (17건) | 201 × 17 |
| DELETE 시드 정리 (5 teams + 13 groups) | 204 × 18 |
| Strict ingest 미등록 team 'ZZ' → 422 | OK (`"team 'ZZ' is not registered"`) |
| DELETE 참조 있는 team → 409 | OK (`"CCT has 8 groups — delete blocked"`) |
| 빈 group 삭제 → 204 | OK |

## Gap 분석 (자세히는 gap-detector 출력 참조)

| 항목 | 상태 |
|------|------|
| 설계 항목 대비 구현 커버리지 | 22/23 (96%) |
| Deviation 1: ingest 검증을 bundle.py + convert.py 가 아닌 db_writer.py 단일 chokepoint 로 통합 | 의도적 개선 (DRY) — Plan/Design 문서에 사후 반영 완료 |
| Deviation 2: 시드 수 16 → 14 | 문서 표기 정정 (실제 seed list 가 14개) |
| Added: get_principal dependency 명시 | 프로젝트 컨벤션 일치 |

## 운영자 사용법

### 대시보드 UI

http://110.15.177.120:8001/dashboard/ → 5번 탭 "조직 관리"
- 좌: Teams (`+ 신규 Team` / 행 클릭 → 우측 Groups 로드 / 수정·삭제)
- 우: 선택된 team 의 Groups (`+ 신규 Group` / 수정·삭제)
- records 참조 있는 항목은 삭제 거부 (alert)

### CLI

```bash
# 목록
curl http://127.0.0.1:8001/api/org/teams
curl http://127.0.0.1:8001/api/org/groups?team=CCT

# 추가
curl -X POST http://127.0.0.1:8001/api/org/teams \
     -H "Content-Type: application/json" \
     -d '{"code":"NEW","name":"New Team"}'

# 수정
curl -X PATCH http://127.0.0.1:8001/api/org/teams/NEW \
     -H "Content-Type: application/json" \
     -d '{"name":"Updated"}'

# 삭제 (records/groups 참조 없을 때만)
curl -X DELETE http://127.0.0.1:8001/api/org/teams/NEW
```

## 최종 조직 상태 (검증 시점)

| Team | Groups |
|------|--------|
| **CCT** 부품 전문 팀 | ANT, AUD, BAT, BIO, CAM, DISP, PWR, RF (8) |
| **HE** Hardware Engineering 팀 | CAE, DV, HWI (3) |
| **SP1** 스마트폰 개발 1팀 | G1, G2, G3, G4 (4) |
| **SP2** 스마트폰 개발 2팀 | G5, G6, G7 (3) |

## 후속 과제 (Open / Out-of-scope)

| 항목 | 사유 / 다음 사이클 |
|------|---------|
| code rename (PATCH 로 code 변경) | 현재는 금지 — records 의 String 컬럼이 String 매칭이라 cascade 위험. 별도 마이그레이션 사이클로 처리 |
| RBAC 세분화 (운영자/뷰어 분리) | 현재는 API key 단일 권한. 운영 단계에서 |
| ETag 외 `Cache-Control: no-cache` 강제 | 변경 즉시 반영은 ETag 로 충분. dashboard 가 mutation 직후 직접 다시 GET → ETag 비교 → 자동 무효화 |
| 대시보드 "그룹·분류" 탭 (의미 그룹) ↔ "조직 관리" 탭 (조직) 명칭 혼동 | 의미 그룹 탭 라벨을 "의미 그룹"으로 변경하면 명확. 다음 사이클 후보 |

## 결론

PDCA 사이클 1회로 Plan → Design → Do → Check → Act 완주. Match Rate 96% (≥ 90% 임계), 실제 사용자 조직 데이터(3 teams + 17 groups) 등록 및 기존 시드 정리 완료. 운영자가 코드 수정 없이 대시보드 또는 REST API 로 조직을 관리할 수 있는 상태에 도달.
