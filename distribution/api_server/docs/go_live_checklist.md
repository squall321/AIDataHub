# 운영 진입 체크리스트 (Go-Live)

목적: 새 환경(개발 PC, 스테이징, 운영 서버)에 AI Data 허브를 처음 띄울 때 누락 없이 진입.
필수 항목과 선택 항목을 분리. 모두 사람 손으로 한 번씩 클릭해서 확인.

체크는 위에서 아래로 순차 실행한다.

---

## 0. 사전 요구사항

- [ ] 서버 OS: Windows 10/11 또는 Linux (Ubuntu 22.04+ 권장).
- [ ] Python 3.12+ 설치 (`py -3.12 --version` 또는 `python3.12 --version`).
- [ ] 디스크 여유 ≥ 20 GB (DB + 첨부 + 임베딩 캐시).
- [ ] (Docker 경로) Docker Desktop / Rancher / Podman 중 하나.

---

## 1. 데이터베이스

- [ ] **PostgreSQL 16+ 설치** — 직접 설치 또는 `docker compose up -d postgres`.
- [ ] (선택) **pgvector 확장 설치** — `CREATE EXTENSION IF NOT EXISTS vector;`
      (없어도 시맨틱 검색 외 모든 기능 동작).
- [ ] DB user / db / password 결정 → `api_server/.env` 에 `DATABASE_URL=...` 작성
      (참고: `api_server/.env.example`).

---

## 2. 마이그레이션

- [ ] **`alembic upgrade head` 실행** — `0001` ~ `0009` 모두 적용.

```powershell
$env:PYTHONPATH = "src"
alembic upgrade head
```

확인:

```powershell
alembic current
# 0009 (또는 그 이후) 가 보이면 OK
```

---

## 3. 표준 데이터 시드

- [ ] **`python -m api.seed`** 실행 — 표준 에이전트 5종 등록.
- [ ] 멱등이므로 재실행 안전. 사전 검증은 `--dry-run`.

확인 후:

```powershell
python -m api.seed --dry-run
# "no changes" 메시지면 시드 완료
```

표준 에이전트 5종: `iga-analyst`, `cae-reporter`, `material-reviewer`, `process-checker`, `code-assistant`.

---

## 4. 인증 부트스트랩

- [ ] **`BOOTSTRAP_API_KEY`** 환경변수 설정 (`.env` 또는 셸 export).
      관리자만 아는 강력한 임의 문자열로 설정 (1회용).
- [ ] 서버 기동 후 부트스트랩 키로 **첫 관리자 키 발급**:

```powershell
curl -X POST "http://localhost:8000/api/auth/keys" `
  -H "X-API-Key: $env:BOOTSTRAP_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{"name":"admin", "role":"admin"}'
```

응답에 한 번만 보이는 평문 키를 안전한 곳에 저장. 이후 부트스트랩 키는 비활성화 권장.

---

## 5. 기본 동작 검증

- [ ] **`GET /api/system/health`** — `200 OK` + `auth_required: true/false` + 버전 메타 확인.
- [ ] **`GET /api/discover`** — `200 OK` + `data_types_explained` + `agents` (5종) 응답.
- [ ] **표준 에이전트 5종 시드 확인** — `/api/discover.agents.length === 5`.

```powershell
curl "http://localhost:8000/api/discover" -H "X-API-Key: <admin>"
```

---

## 6. 첫 record 적재

- [ ] **`POST /api/convert/ingest`** 또는 VS Code 확장으로 **첫 record 적재**.
      샘플: `AI_data/examples/HE-CAE-2026-0000000001.json` 또는 `AI_data/iga_guide.docx`.

curl 예:

```powershell
curl -X POST "http://localhost:8000/api/convert/ingest" `
  -H "X-API-Key: <admin>" `
  -F "file=@AI_data/iga_guide.docx" `
  -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=1" `
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"
```

응답: `record_id` + `status: created`.

---

## 7. 검색·조회 검증

- [ ] **`GET /api/data?agent=iga-analyst`** — 1 건 이상 응답 확인 (relevance 스코어 포함).
- [ ] **`POST /api/ask`** — `{"query":"IGA 자료"}` 자연어 질의 → `interpreted_query` + `results` 확인.
- [ ] **`GET /api/records/{id}`** — 단일 레코드 상세 응답 확인.
- [ ] **`GET /api/records/{id}/sections`** — DOC 일 경우 섹션 목록 응답.

---

## 8. (선택) 시맨틱 검색

pgvector 가 설치된 경우만:

- [ ] **`python -m api.embed`** — 기존 record 의 미임베딩 섹션 백필.
- [ ] **`OPENAI_API_KEY`** 가 환경변수에 있으면 자동 사용. 없으면 결정론적 더미 임베더 (테스트 전용).
- [ ] **`GET /api/search?mode=semantic&q=...`** 응답 확인.

---

## 9. (선택) 자동 임베딩 트리거

- [ ] **`AUTO_EMBED_ON_INSERT=true`** 환경변수 설정 → INSERT 시 임베딩 백필 큐에 자동 등록.
- [ ] async job worker 가 같이 떠 있어야 한다 (`scripts/start_worker.{ps1,sh}` 또는 docker compose 의 `worker` 서비스).

운영 초기에는 off 권장 (수동 백필이 더 예측 가능). 데이터가 누적되면 on.

---

## 10. (선택) 사용자 배포

- [ ] **vsix 사내망 공유** — `vscode_extension/dist/ai-data-uploader-x.y.z.vsix` 를
      사내 공유 폴더 또는 사내 패키지 저장소에 게시.
- [ ] **사용자 가이드 공지** — [`user_guide_for_engineers.md`](user_guide_for_engineers.md) 링크.
- [ ] **사용자별 API key 발급 절차 명문화** — 누가 / 어떤 채널로 신청 / 만료 정책.
- [ ] **첫 사용자 1~2 명 파일럿** — 실제 자료 1 건 적재 → 검색 성공까지 동행.

---

## 11. 운영 보강 (옵션)

- [ ] **`AUTH_REQUIRED=true`** — 모든 엔드포인트 X-API-Key 의무화.
- [ ] **`/metrics`** Prometheus 스크레이프 등록.
- [ ] **JSON 로그 → Elastic/Loki** 적재.
- [ ] **백업 cron** — `pg_dump` + `ATTACHMENTS_DIR` rsync.
- [ ] **CORS 화이트리스트** — `EXTRA_ALLOWED_ORIGINS` 에 사내 웹툴 도메인 추가.
- [ ] **MAX_UPLOAD_MB** 조정 — 기본 50 MB. 큰 PPT/PDF 일상화면 100 MB 권장.

---

## 끝났음을 어떻게 아는가

다음 한 문장이 사실이면 운영 진입 완료:

> "VS Code 확장으로 적재한 record 가 Cline SR 의 자연어 질의 응답에 도메인 컨텍스트로 인용된다."

이 검증이 통과하기 전에는 어떤 체크박스가 다 채워졌더라도 운영 진입이 아니다.

---

## 참고 문서

- [`setup_guide.md`](setup_guide.md) — 더 자세한 설치 가이드.
- [`api_reference.md`](api_reference.md) — 모든 엔드포인트 reference.
- [`governance.md`](governance.md) — audit_log / soft delete / lineage 운영.
- [`AGENT_ONBOARDING.md`](AGENT_ONBOARDING.md) — AI 에이전트 통합.
- [`user_guide_for_engineers.md`](user_guide_for_engineers.md) — 사업부 사용자용.
- [`FAQ.md`](FAQ.md) — 자주 묻는 8 가지.
