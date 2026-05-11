# Mobile eXperience AI Data Hub — 원터치 배포

다른 서버 (Linux 사내 서버 / 클라우드 VM / Windows + Docker Desktop) 에 한 번에 셋업한다.

PostgreSQL + pgvector + 의존성 + 마이그레이션 + 첫 기동까지 자동.

---

## 빠른 시작

### Linux / macOS (Docker)

```bash
git clone <this-repo> aidh
cd aidh/deploy
bash install.sh
```

완료 시 `http://<host>:8000` 에서 API 가 응답한다. `/docs` 로 Swagger UI 확인.

### Windows (Docker Desktop)

```cmd
cd aidh\deploy
install.bat
```

또는 PowerShell:

```powershell
cd aidh\deploy
.\install.ps1
```

---

## 무엇이 자동화되었나

| 단계 | install.sh / .bat / .ps1 | 비고 |
| --- | --- | --- |
| Docker / compose v2 검증 | yes | 없으면 실패 + 설치 링크 안내 |
| `.env` 생성 | yes | `.env.example` 복사 |
| PostgreSQL 16 + pgvector | yes | `pgvector/pgvector:pg16` 이미지 |
| API 이미지 빌드 | yes | `deploy/Dockerfile` |
| Alembic 마이그레이션 | yes | `entrypoint.sh` 안에서 `alembic upgrade head` |
| 표준 에이전트 시드 | yes | `python -m api.seed` (멱등) |
| uvicorn 기동 | yes | `0.0.0.0:8000` |
| 헬스체크 대기 | yes | `/api/system/health` 60초 폴링 |

---

## 운영 명령

`deploy/` 안에서 실행한다.

```bash
docker compose ps                  # 컨테이너 상태
docker compose logs -f api         # API 로그 follow
docker compose restart api         # API 재시작 (코드 변경 후)
docker compose down                # 종료 (데이터 보존)
docker compose down -v             # 종료 + 볼륨 (DB/첨부) 삭제
docker compose pull && docker compose up -d --build   # 재배포
```

---

## 환경 변수 (`deploy/.env`)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `aidh_change_me` | DB 비밀번호 (반드시 변경) |
| `POSTGRES_PORT` | `5432` | 호스트에 노출할 PG 포트 |
| `API_PORT` | `8000` | API 포트 |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |
| `LOG_FORMAT` | `json` | json / text |
| `AUTH_REQUIRED` | `false` | true 면 모든 요청에 X-API-Key 필수 |
| `BOOTSTRAP_API_KEY` | (빈값) | 키 발급용 마스터 키 |
| `EMBEDDING_PROVIDER` | `hash` | hash / openai |
| `OPENAI_API_KEY` | (빈값) | `EMBEDDING_PROVIDER=openai` 시 필수 |
| `BUILD_SHA` | `deploy` | `/api/system/health` 의 build 필드 |

---

## API key 발급 (AUTH_REQUIRED=true 시)

1. `.env` 에서 `AUTH_REQUIRED=true` + `BOOTSTRAP_API_KEY=<강한 랜덤>` 설정.
2. `docker compose up -d` 재기동.
3. 운영 키 발급:

   ```bash
   docker compose exec api python -c "
   import asyncio
   from api.database import SessionLocal
   from api.auth.keys import create_api_key
   async def go():
       async with SessionLocal() as s:
           row, plain = await create_api_key(s, name='ops')
           print('API_KEY=', plain)
   asyncio.run(go())
   "
   ```

4. 출력된 plaintext 를 안전한 곳에 보관. 이후 모든 요청에 `X-API-Key: <plaintext>` 헤더 첨부.
5. (권장) 운영 키 발급 후 `BOOTSTRAP_API_KEY` 를 다시 빈 문자열로 두고 재기동.

---

## Docker 없이 native 설치

PostgreSQL + pgvector 가 이미 설치된 환경.

### Linux / macOS

```bash
bash deploy/native_install.sh
# 첫 실행 시 .env 생성 후 종료 — DATABASE_URL 수정 후 재실행
```

### Windows

```cmd
deploy\native_install.bat
```

기존 `api_server\setup.bat` 를 위임 호출한다.

---

## 트러블슈팅

| 증상 | 원인 / 처치 |
| --- | --- |
| `port is already allocated` | `.env` 에서 `POSTGRES_PORT` / `API_PORT` 변경 |
| API 컨테이너가 즉시 종료 | `docker compose logs api` 로 alembic 또는 import 에러 확인 |
| `pgvector` 관련 에러 | 이미지 `pgvector/pgvector:pg16` 사용 중인지 확인 — 다른 PG 이미지 쓰면 마이그레이션 0004 실패 |
| 마이그레이션이 멈춤 | `docker compose exec postgres psql -U aidh -d aidh -c '\dx'` 로 vector 확장 확인 |
| `EMBEDDING_PROVIDER=openai` 인데 키 없음 | `.env` 에 `OPENAI_API_KEY=` 설정 + `docker compose restart api` |
| ARM 머신에서 빌드 느림 | base image 가 multi-arch 라 동작은 하지만 첫 빌드만 5–10분 소요 가능 |

---

## 한계

- **GPU**: 현 기본 임베더는 CPU (hash 또는 OpenAI API). 로컬 GPU 모델 쓰려면 별도 컨테이너 + driver 매핑 필요 (`nvidia-container-toolkit`).
- **Tesseract / Poppler**: PDF OCR (`/api/convert?ocr=true`) 사용 시 추가 설치 필요. 기본 이미지는 미포함.
- **Backup**: 자동 백업 없음. `docker compose exec postgres pg_dump -U aidh aidh > backup.sql` 수동.
- **HTTPS**: 컨테이너는 평문 HTTP. 운영 시 nginx / Caddy / cloud LB 앞단 두고 TLS 종단 권장.

---

## 파일 목록 (deploy/)

```text
deploy/
  docker-compose.yml      # postgres + api 정의
  Dockerfile              # api 이미지 빌드
  entrypoint.sh           # alembic + seed + uvicorn
  install.sh              # Linux/macOS 원터치
  install.bat             # Windows cmd 원터치 (Docker)
  install.ps1             # Windows PowerShell 원터치 (Docker)
  native_install.sh       # Linux/macOS native (PG 별도 설치)
  native_install.bat      # Windows native wrapper
  .env.example            # 환경변수 템플릿
  README.md               # 본 문서
```
