# Mobile eXperience AI Data Hub — Windows 서버 셋업 가이드

다른 Windows 서버에 한 번에 셋업한다. PostgreSQL + pgvector + API 서버 + 마이그레이션 + 헬스체크까지 자동.

> **Linux 서버 셋업은 [`deploy/README_LINUX.md`](./deploy/README_LINUX.md) 참조.**
> Linux 는 `apt` / `dnf` 패키지로 PG + pgvector 자동 설치, 패키지 미제공 시 source build 자동 fallback.

## 한 줄 요약

zip 풀고 `deploy\SERVER_QUICK_SETUP.bat` 더블클릭 → PG 비번 1회 입력 → 끝.

> **pgvector binary 안내**: 배포 패키지의 `deploy/vendor/pgvector-pg18-windows-x64.zip` 은 PG 18 용 사전 빌드 binary (운영자가 미리 빌드해서 패키지에 포함). 별도 빌드 없이 그대로 install 자동화. PG 16/17 환경이면 운영자에게 해당 PG 버전용 vendor zip 별도 요청.

---

## 사전 요구 (1회만)

| 항목 | 필수 / 선택 | 비고 |
|---|---|---|
| Windows 10 / 11 / Server 2016+ (x64) | 필수 | |
| 관리자 권한 | 필수 | UAC 동의 가능한 계정 |
| 인터넷 연결 | 필수 (PG 신규 설치 시) | EDB 인스톨러 ~300MB 다운로드 |
| **Python 3.12** | **필수** | <https://www.python.org/downloads/release/python-3128/> 에서 설치. **"Add python.exe to PATH" 체크** |
| 비어 있는 포트 5432 / 8000 | 필수 | 사용 중이면 충돌 |
| 디스크 여유 | 권장 5GB+ | PG 데이터 + venv + figures |

> Python 3.12 만 사전 설치하면 그 외 모든 것은 SERVER_QUICK_SETUP.bat 가 자동 처리한다.

---

## 4단계 셋업

### 1. zip 풀기

`distribution.zip` 을 임의의 경로에 압축 해제. **경로에 공백/한글이 없는 위치 권장** (예: `C:\aidh\`).

압축 해제 후 구조:

```text
distribution\
  deploy\
    SERVER_QUICK_SETUP.bat   ← 더블클릭 대상
    install_postgres_windows.ps1
    install_pgvector_windows.ps1
    write_env.ps1
    vendor\pgvector-pg18-windows-x64.zip
    ...
  api_server\
    setup.bat
    run.bat
    src\, alembic\, requirements.txt ...
  client_setup\
  SERVER_SETUP_GUIDE.md      ← 본 문서
  CLIENT_SETUP_GUIDE.md
```

### 2. SERVER_QUICK_SETUP.bat 더블클릭

`distribution\deploy\SERVER_QUICK_SETUP.bat` 더블클릭.

순서대로:

1. **UAC 창** → "예" 클릭 (관리자 권한 부여)
2. **postgres 비밀번호 입력** (한 번만, 화면에 안 보임)
   - 신규 PG 설치 시: 새 비번 정함 (강한 비번 권장)
   - 기존 PG 17/16 사용 시: 기존 postgres 슈퍼유저 비번 입력

> 비밀번호에 따옴표 `"` 는 사용 금지. 그 외 특수문자는 OK.

### 3. 자동 진행 (10~15분)

콘솔에 진행 상황 표시:

```text
[1/6] PostgreSQL 18 자동 설치 (5~10분) ...
[2/6] pgvector 설치 ...
[3/6] api_server\.env 생성 ...
[4/6] Python venv 생성 ...
[5/6] api_server\setup.bat 실행 (의존성 + 마이그레이션 + 시드) ...
[6/6] API 서버 백그라운드 기동 ...
```

각 단계가 자동으로 진행된다. 마지막에 "Mobile eXperience AI Data Hub API" 라는 최소화된 콘솔 창이 작업 표시줄에 보이면 성공.

### 4. 검증

웹 브라우저에서:

| URL | 기대 응답 |
|---|---|
| <http://localhost:8000/api/system/health> | `{"status":"ok", ...}` |
| <http://localhost:8000/docs> | Swagger UI |
| <http://localhost:8000/api/discover> | JSON 카탈로그 |

원격 서버라면 `localhost` 를 서버 IP / 호스트명으로 변경.

---

## 무엇이 자동화되었나

| # | 단계 | 도구 |
|---|---|---|
| 0 | UAC elevation | SERVER_QUICK_SETUP.bat 자가 elevation |
| 1 | Python 3.12 검증 | `py -3.12 --version` |
| 2 | PostgreSQL 설치 검증 (18 → 17 → 16 순서로 발견) | 폴백 자동 |
| 3 | PG 18 다운로드 + silent install | install_postgres_windows.ps1 |
| 4 | PG 서비스 자동 시작 + Path 등록 | install_postgres_windows.ps1 |
| 5 | pgvector 파일 복사 + CREATE EXTENSION 검증 | install_pgvector_windows.ps1 |
| 6 | api_server\.env 생성 (DATABASE_URL 포함, 비번 URL-encoded) | write_env.ps1 |
| 7 | venv 생성 + pip install -r requirements.txt | api_server\setup.bat |
| 8 | DB 'ai_data' 생성 + alembic upgrade head + 시드 | api_server\setup.bat |
| 9 | uvicorn 백그라운드 기동 | api_server\run.bat |
| 10 | /api/system/health 60초 폴링 | curl |

---

## 트러블슈팅

| 증상 | 원인 | 대응 |
|---|---|---|
| UAC 창에서 "아니오" 클릭 | 권한 부여 거부 | 관리자 PowerShell 에서 `& '경로\SERVER_QUICK_SETUP.bat'` 재실행 |
| `[ERROR] py 런처 없음` | Python 3.12 미설치 | <https://python.org> 에서 3.12 설치 (PATH 체크 필수) |
| `[ERROR] 포트 5432 이미 사용 중` | 다른 PG 가 떠 있음 | services.msc 에서 기존 `postgresql-x64-*` 정리 후 재실행 |
| PG 인스톨러 다운로드 실패 | 방화벽/프록시 | `https://get.enterprisedb.com/postgresql/postgresql-18.0-1-windows-x64.exe` 수동 다운로드 → `%TEMP%\postgresql-18-installer.exe` 저장 후 재실행 |
| `[WARN] pgvector 검증 실패` | 기존 PG 17/16 + ABI 불일치 | 배포 패키지의 vendor zip 은 PG 18 사전 빌드 binary. 16/17 환경이면 운영자에게 PG 16/17 용 vendor zip 별도 요청. ILIKE 폴백으로 서비스는 동작 |
| `alembic upgrade head` 실패 | pgvector 미적용 + 0004 마이그레이션 | pgvector 재설치 후 `cd api_server && .venv\Scripts\activate && alembic upgrade head` |
| 8000 포트 응답 없음 | run.bat 콘솔이 즉시 종료됨 | 작업표시줄의 "Mobile eXperience AI Data Hub API" 창에서 에러 확인. `cd api_server && run.bat` 직접 실행해 stdout 확인 |
| 한글 깨짐 | 콘솔 코드페이지 | SERVER_QUICK_SETUP.bat 가 `chcp 65001` 자동 적용. CMD 폰트가 "굴림체" 면 안 보임 — "맑은 고딕" 권장 |

---

## 운영 명령

| 작업 | 명령 |
|---|---|
| 서버 시작 | `cd api_server && run.bat` |
| 서버 종료 | "Mobile eXperience AI Data Hub API" 콘솔 창 닫기 또는 `taskkill /F /IM python.exe` |
| 로그 확인 | run.bat 콘솔 창의 stdout (또는 LOG_FORMAT=json 시 jq 로 필터) |
| .env 수정 | `notepad api_server\.env` → 저장 → 서버 재시작 |
| DB 직접 접속 | `psql -U postgres -d ai_data` |
| pg 데이터 백업 | `pg_dump -U postgres -d ai_data > backup.sql` |

---

## 다음 단계 — 클라이언트 연결

이 서버에 다른 PC (개발자 워크스테이션 등) 가 접속하려면:

1. **인증 활성화 (운영 필수)**:
   - `api_server\.env` 에서 `AUTH_REQUIRED=true` + `BOOTSTRAP_API_KEY=<강한_랜덤>` 설정
   - `cd api_server && run.bat` 재시작
2. **API Key 발급**:
   ```powershell
   cd api_server
   .venv\Scripts\activate
   $env:PYTHONPATH = "src"
   python -m api.cli issue-key --name ops
   ```
   출력된 plaintext 키를 클라이언트에게 전달.
3. **방화벽 오픈**: 8000 포트 인바운드 허용 (`netsh advfirewall firewall add rule name="Mobile eXperience AI Data Hub" dir=in action=allow protocol=TCP localport=8000`).
4. **클라이언트 셋업**: 클라이언트 PC 에서 `client_setup\setup.bat` 실행 — 자세한 절차는 **CLIENT_SETUP_GUIDE.md** 참조.

---

## 한계

- **GPU 임베딩 미지원**: 기본 임베더는 hash 또는 OpenAI API. 로컬 GPU 모델은 별도 컨테이너 필요.
- **자동 백업 없음**: pg_dump 수동 또는 작업 스케줄러로 등록.
- **HTTPS 미적용**: 평문 HTTP. 운영 시 nginx / IIS / Caddy 앞단에 TLS 종단.
- **PG 16/17 + pgvector**: vendor zip 은 PG 18 빌드 — 16/17 환경에서는 pgvector 재빌드 필요할 수 있음. ILIKE 폴백으로 서비스 자체는 동작.
- **OCR 미포함**: PDF OCR (`/api/convert?ocr=true`) 사용 시 Tesseract / Poppler 별도 설치.

---

## 파일 목록 (deploy/)

```text
deploy\
  SERVER_QUICK_SETUP.bat            # 원터치 진입점 (더블클릭)
  install_postgres_windows.ps1      # PG silent install
  install_pgvector_windows.ps1      # pgvector binary 설치
  write_env.ps1                     # api_server\.env 생성 헬퍼
  vendor\
    pgvector-pg18-windows-x64.zip   # PG 18 용 pgvector 빌드
  install.bat / install.ps1         # (Docker 기반 대안)
  native_install.bat                # (수동 native 설치 wrapper)
  README.md                         # Docker 배포 문서
```
