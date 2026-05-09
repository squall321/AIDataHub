# Windows 환경 — Docker 없이 셋업 (PG + pgvector 자동)

Windows 에서 Docker 를 사용하지 않고 PostgreSQL + pgvector 를 설치한 뒤
`api_server/setup.bat` 으로 마이그레이션 + 시드까지 끝내는 흐름.

## 시나리오별 명령

| 상황 | 명령 |
|------|------|
| PG 미설치 | `install_postgres_windows.ps1` (자동 다운로드 + silent install) |
| PG 설치됐지만 pgvector 없음 | `install_pgvector_windows.ps1` |
| PG + pgvector 다 됐음 | `cd ..\api_server && setup.bat` |

## 단계별 절차

1. Windows 시작 메뉴 → "PowerShell" 검색 → **"관리자 권한으로 실행"**
2. 실행 정책 임시 허용:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   cd D:\Personal\AI_data\deploy
   ```
3. (PG 미설치 시) PostgreSQL 18 자동 설치:
   ```powershell
   .\install_postgres_windows.ps1
   # 비밀번호 직접 지정:
   .\install_postgres_windows.ps1 -SuperPassword "MyStrongPwd!"
   ```
   - EnterpriseDB installer 자동 다운로드 (~300MB, 5분 내외)
   - 기본 superuser 비밀번호: `postgres` (운영 시 반드시 변경)
   - 기본 포트: 5432
   - 기본 설치 경로: `C:\Program Files\PostgreSQL\18`
4. pgvector 설치:
   ```powershell
   .\install_pgvector_windows.ps1
   # PG 버전 명시 / 비밀번호 명시 / binary URL 지정 옵션:
   .\install_pgvector_windows.ps1 -PgVersion 18 -PostgresPassword "MyStrongPwd!"
   .\install_pgvector_windows.ps1 -BinaryUrl "https://example.com/pgvector-pg18.zip"
   ```
   - PG 18/17/16 자동 감지 (가장 높은 버전 우선, `-PgVersion` 으로 지정 가능)
   - `vector.dll` → `lib/`, `vector.control` + `vector--*.sql` → `share/extension/` 자동 복사
   - 서비스 재시작 후 `CREATE EXTENSION vector` 검증
5. api_server 셋업:
   ```powershell
   cd ..\api_server
   .\setup.bat
   ```
   - `.env` 자동 생성 + DATABASE_URL 안내
   - `ai_data` DB 생성 + 마이그레이션 + 표준 에이전트 시드

## pgvector binary 확보 전략

`pgvector` 공식 저장소는 **source-only** 라 Windows 에서는 Visual Studio Build Tools + `nmake`
가 필요하다. 일반 사용자에게는 진입 장벽이 높아 스크립트는 **세 단계 폴백**으로 동작한다:

1. **로컬 zip 우선** — 같은 폴더에 `pgvector-pg{버전}-windows-x64.zip` 이 있으면 그걸 사용.
   가장 신뢰성 높은 경로 (사용자가 직접 다운로드 받아 검증 가능).
2. **`-BinaryUrl` 인자** — 사용자가 신뢰하는 community fork URL 을 직접 넘긴다.
3. **모두 실패 시 명확한 안내** — source 빌드 명령(`nmake /F Makefile.win install`) 까지 출력.

zip 내부 구조는 다음 둘 다 자동으로 처리한다:
```
# 형태 A (디렉터리 보존)
lib/vector.dll
share/extension/vector.control
share/extension/vector--0.x.x.sql

# 형태 B (평탄)
vector.dll
vector.control
vector--0.x.x.sql
```

## 트러블슈팅

| 증상 | 원인 / 해결 |
|------|--------|
| `관리자 권한이 필요하다` | PowerShell 을 "관리자 권한으로 실행"으로 다시 열어라. |
| `installer 다운로드 실패` | EDB URL 의 패치 버전 변동. https://www.postgresql.org/download/windows/ 에서 최신 URL 확인 후 `-InstallerUrl` 로 지정. |
| `pgvector binary 를 확보하지 못했다` | 같은 폴더에 `pgvector-pg18-windows-x64.zip` 을 두거나 `-BinaryUrl` 사용. |
| `CREATE EXTENSION 실패` | (1) postgres 비밀번호 불일치 → `-PostgresPassword` 지정 (2) 서비스 재시작 안 됨 → `services.msc` 에서 `postgresql-x64-18` 재시작 (3) PG 버전과 ABI 비호환 binary. |
| `포트 5432 충돌` | 다른 PG 가 점유 중. `install_postgres_windows.ps1 -Port 5433`. |
| `setup.bat` 가 pgvector 미설치를 보고 | 설치 누락 또는 서비스 재시작 필요. `install_pgvector_windows.ps1` 재실행. |

## 보안 주의

- `install_postgres_windows.ps1 -SuperPassword "postgres"` (기본값) 은 **개발용**.
  운영 환경에서는 강한 비밀번호 + `pg_hba.conf` 의 `localhost` 외 차단 + 방화벽 규칙 필수.
- 다운로드한 binary 는 항상 신뢰하는 source 인지 확인하라. 가능하면 공식 source 빌드.
