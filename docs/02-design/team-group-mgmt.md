# Design — Team/Group Master Table + Dashboard CRUD

**Feature**: `team-group-mgmt`
**Phase**: Design (PDCA)
**Date**: 2026-05-11
**See**: [Plan](../01-plan/team-group-mgmt.md)

## 1. DB Schema (alembic 0012)

```sql
-- 1) Master tables
CREATE TABLE org_teams (
  code        VARCHAR(10)  PRIMARY KEY,           -- 2~4자 대문자 ASCII (id_format 규약)
  name        TEXT         NOT NULL,
  description TEXT         NOT NULL DEFAULT '',
  is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE org_groups (
  team_code   VARCHAR(10)  NOT NULL REFERENCES org_teams(code) ON DELETE RESTRICT,
  code        VARCHAR(20)  NOT NULL,              -- 2~5자 대문자 ASCII (group은 약간 더 길게 허용)
  name        TEXT         NOT NULL,
  description TEXT         NOT NULL DEFAULT '',
  is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (team_code, code)
);
CREATE INDEX idx_org_groups_team ON org_groups (team_code);
```

**FK 정책**: `org_groups → org_teams` 만 RESTRICT (마스터 안에서의 무결성). `records.team/group → org_*` FK는 **걸지 않는다** (rationale: records 0010 마이그레이션 후 기존 데이터 호환성 + group lenient 정책).

**데이터 이전** (alembic data_upgrade 단계):

1. `seed/teams.py`의 `TEAMS` 6개를 `org_teams`에 INSERT
2. `seed/teams.py`의 `GROUPS` 14개를 `org_groups`에 INSERT
   (HE:3 + EV:3 + PT:2 + DA:2 + MX:2 + VD:2)
3. `SELECT DISTINCT team, group FROM records` 결과 중 마스터에 없는 값은 **로그만 출력** (자동 추가 X — Strict 정책의 일관성)
4. 향후 records orphan 검증은 별도 점검 스크립트 (out-of-scope)

## 2. API Spec — `/api/org`

| Method | Path | 인증 | 동작 | 응답 |
|--------|------|------|------|------|
| GET | `/api/org/teams` | 익명 | team 목록 (활성/전체 옵션) | `[{"code","name","description","is_active","group_count","record_count"}]` |
| GET | `/api/org/teams/{code}` | 익명 | 단일 team | 객체 또는 404 |
| POST | `/api/org/teams` | API key | team 생성 | 201 + 객체, 409 (중복) |
| PATCH | `/api/org/teams/{code}` | API key | name/description/is_active | 200 + 객체 |
| DELETE | `/api/org/teams/{code}` | API key | 삭제. records 또는 org_groups 참조 시 409 | 204 |
| GET | `/api/org/groups` | 익명 | `?team=HE` 필터 가능 | 동일 패턴 |
| GET | `/api/org/groups/{team}/{code}` | 익명 | 단일 group | |
| POST | `/api/org/groups` | API key | `{"team_code","code","name"}` | 201, 409, 400 (team 미존재) |
| PATCH | `/api/org/groups/{team}/{code}` | API key | name/description/is_active | 200 |
| DELETE | `/api/org/groups/{team}/{code}` | API key | records 참조 시 409 | 204 |

**검증 규칙**:
- `code`: `^[A-Z][A-Z0-9]{1,9}$` (team), `^[A-Z][A-Z0-9]{1,19}$` (group)
- `name`: 1~80자
- PATCH 시 `code` 변경 금지 (rename은 별도 사이클)

**참조 카운트 (group_count, record_count)**:
- group_count = `SELECT COUNT(*) FROM org_groups WHERE team_code = ?`
- record_count = `SELECT COUNT(*) FROM records WHERE team = ? [AND group = ?]`

## 3. `/api/meta/options` 변경

**전**:
```python
from ..seed.teams import GROUPS, TEAMS
...
"teams": list(TEAMS),
"groups": {k: list(v) for k, v in GROUPS.items()},
```

**후**:
```python
team_rows = (await session.execute(
    select(OrgTeam).where(OrgTeam.is_active).order_by(OrgTeam.code)
)).scalars().all()
group_rows = (await session.execute(
    select(OrgGroup).where(OrgGroup.is_active).order_by(OrgGroup.team_code, OrgGroup.code)
)).scalars().all()

teams = [t.code for t in team_rows]
groups: dict[str, list[str]] = {}
for g in group_rows:
    groups.setdefault(g.team_code, []).append(g.code)

# 응답 키는 동일 → VSCode extension 호환
"teams": teams,
"groups": groups,
```

**ETag**:
- payload SHA256 의 앞 16자 → `ETag: "abc123..."` 헤더
- 클라이언트가 `If-None-Match` 보내면 304 응답
- 변경 직후 응답은 새 ETag → 캐시 자동 무효화

`Cache-Control: public, max-age=300` 유지 + ETag → 클라이언트는 5분 후 conditional GET → 변경 없으면 304 (밴드폭 절약)

## 4. Ingest Strict 검증

**삽입 위치**: [services/org_svc.py](../../api_server/src/api/services/org_svc.py) (신규)에 `validate_team_group(session, team, group)` 추가:

```python
async def validate_team_group(session, team: str, group: str) -> None:
    """team 마스터 존재 + (team, group) 마스터 일치를 검증.
    
    실패 시 422 Unprocessable Entity 발생.
    
    설계 노트:
        - team 은 Strict (ALLOW_CUSTOM.team = false)
        - group 은 lenient (ALLOW_CUSTOM.group = true) → group 미존재는 경고만, 차단 X
    """
    t = await session.get(OrgTeam, team)
    if t is None or not t.is_active:
        raise HTTPException(status_code=422,
            detail=f"team '{team}' is not registered or inactive")
    # group 은 lenient — 로그만
    g = await session.get(OrgGroup, (team, group))
    if g is None:
        log.info("ingest: unknown group '%s/%s' (lenient allowed)", team, group)
```

**호출 지점** (구현 시 단일 chokepoint 로 통합):
- [routes/records.py](../../api_server/src/api/routes/records.py) `create_record`: `session.add(rec)` 직전 (직접 POST 경로)
- [ingest/db_writer.py](../../api_server/src/api/ingest/db_writer.py) `_resolve_target_for_record` 진입 직후
  — bundle / convert / 모든 외부 ingest 가 결국 이 함수로 모이므로 한 곳에서 검증 (DRY).
  Plan/Design 초안에서는 bundle.py / convert.py 각각 편집을 명시했으나
  실제로는 단일 진입점에서 검증하는 게 더 안전.

운영 toggle: 환경변수 `STRICT_TEAM_VALIDATION` (default true). false면 검증 skip → 마이그레이션 기간 대비.

## 5. Dashboard UI 와이어프레임

새 탭 "**조직**" (탭 번호 08 정도, 기존 "그룹·분류"는 의미 그룹이라 별도):

```
┌─ Tabs: Records | Search | Analytics | ... | 그룹·분류 | 조직 ──┐
│                                                              │
│  ┌─ Teams ──────────────────┐  ┌─ Groups (team=HE) ─────┐  │
│  │ [+ 신규 Team]            │  │ [+ 신규 Group]          │  │
│  │ ──────────────────────── │  │ ─────────────────────── │  │
│  │ HE  HE 팀         8 그룹 │  │ CAE   Computer-Aided... │  │
│  │ EV  EV 팀         3 그룹 │  │ Test  Test             │  │
│  │ PT  ...                  │  │ Design Design          │  │
│  │ ...                      │  │                        │  │
│  └──────────────────────────┘  └────────────────────────┘  │
│                                                              │
│  선택된 team 클릭 → 오른쪽 패널에 해당 group 목록 로드        │
│  각 행에 [수정] [삭제] 버튼                                   │
└──────────────────────────────────────────────────────────────┘
```

모달:
- 신규/수정 모달: code (신규시만 입력 가능), name, description, is_active 체크박스
- 삭제 모달: 확인 + records 참조 카운트 표시. 0이 아니면 "삭제 불가"

## 6. 변경 파일 매니페스트

| 파일 | 동작 |
|------|------|
| `api_server/alembic/versions/0012_org_master.py` | NEW — DDL + data_upgrade |
| `api_server/src/api/db/models.py` | EDIT — `OrgTeam`, `OrgGroup` 모델 추가 |
| `api_server/src/api/routes/org.py` | NEW — CRUD 라우터 |
| `api_server/src/api/services/org_svc.py` | NEW — `validate_team_group` |
| `api_server/src/api/routes/__init__.py` | EDIT — `org` import + include_router |
| `api_server/src/api/routes/meta.py` | EDIT — DB 조회 + ETag |
| `api_server/src/api/routes/records.py` | EDIT — create_record에 validate 호출 |
| `api_server/src/api/ingest/db_writer.py` | EDIT — record 생성 직전 validate (bundle + convert 단일 chokepoint) |
| `api_server/src/api/config.py` | EDIT — `strict_team_validation: bool = True` 추가 |
| `api_server/src/api/seed/teams.py` | EDIT — deprecate 주석 + 기존 TEAMS/GROUPS 상수는 fallback으로 유지 |
| `api_server/static/dashboard/index.html` | EDIT — 새 탭 마크업 |
| `api_server/static/dashboard/dashboard.js` | EDIT — fetchOrg(), renderTeams(), CRUD 핸들러 |
| `docs/01-plan/team-group-mgmt.md` | 이미 생성 |
| `docs/02-design/team-group-mgmt.md` | 이 문서 |

## 7. 에러 코드 매트릭스

| 시나리오 | HTTP | 응답 |
|---------|------|------|
| team 중복 생성 | 409 | `{"detail": "team 'HE' already exists"}` |
| team 미존재 group 생성 | 422 | `{"detail": "team 'XX' not found"}` |
| code 형식 위반 | 422 | pydantic validation error |
| records 참조 team 삭제 | 409 | `{"detail": "team 'HE' has 42 records — delete blocked"}` |
| Strict ingest 미등록 team | 422 | `{"detail": "team 'XX' is not registered"}` |
| 단순 미존재 조회 | 404 | `{"detail": "team 'XX' not found"}` |

## 8. 검증 시나리오 (Do 단계에서 수행)

```bash
# 1. alembic 적용
bash deploy/apptainer/start_api.sh
# 2. options 정상
curl -s http://127.0.0.1:8001/api/meta/options | jq '.teams, .groups'
# 3. 새 team 추가
curl -X POST http://127.0.0.1:8001/api/org/teams \
     -H "Content-Type: application/json" \
     -d '{"code":"TEST","name":"테스트 팀"}'
# 4. options 즉시 반영
curl -s http://127.0.0.1:8001/api/meta/options | jq '.teams'
# 5. group 추가
curl -X POST http://127.0.0.1:8001/api/org/groups \
     -d '{"team_code":"TEST","code":"SUB","name":"서브"}'
# 6. records 참조 없는 team 삭제 → 204
curl -X DELETE http://127.0.0.1:8001/api/org/teams/TEST
# 7. records가 있는 team(HE) 삭제 시도 → 409
curl -X DELETE http://127.0.0.1:8001/api/org/teams/HE
# 8. Strict ingest 미등록 team → 422
curl -X POST http://127.0.0.1:8001/api/records \
     -d '{"id":"DOC-ZZ-XX-2026-0000000001","data_type":"DOC","team":"ZZ","group":"XX",...}'
# 9. ETag 동작
curl -I http://127.0.0.1:8001/api/meta/options    # ETag 받음
curl -I http://127.0.0.1:8001/api/meta/options -H 'If-None-Match: "..."' # 304
```

## 9. 회귀 방지

- 기존 `/api/meta/options` 응답 키 (`teams`, `groups`, `agents`, ...) 모두 유지
- VSCode extension 동작 변경 없음 (자동 수혜)
- 기존 records 데이터 변경 없음
