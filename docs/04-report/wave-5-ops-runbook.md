# Wave-5 운영 검증 Runbook (target 서버)

작성일: 2026-05-24
대상: v0.4.4 (P1.8 완료). wave-1~5 + extension 0.16.0 + wave-4 동적 도구 + wave-6 P1 federation hook 까지 통합.
선행: 메모리 규칙 — **dev PC 가 아닌 target 서버 (110.15.177.120 또는 10.252.39.181)에서 사용자 직접 실행**.
보조 자동화: `deploy/apptainer/verify-wave-5.sh` + `deploy/apptainer/seed-stress-strain.sh`.

---

## 0. 사전 준비 (1회)

| 항목 | 확인 명령 | 합격 기준 |
|---|---|---|
| repo 최신 | `git pull && git log --oneline -5` | `e94f4bf` 또는 그 이후 |
| venv | `ls api_server/.venv/bin/python` | 존재 |
| Python 3.12 | `api_server/.venv/bin/python --version` | 3.12.x |
| Apptainer | `apptainer --version` | 1.3 이상 (또는 `.tools/apptainer/...`) |
| PostgreSQL | `apptainer instance list \| grep postgres` | running |
| GPU (옵션) | `nvidia-smi` | Driver + GPU 표시 |
| MCP_BASE_URL env | `cat deploy/apptainer/.env \| grep MCP_BASE_URL` | `http://<host>:8001` 권장 (URL 절대화) |

문제 시: `bash deploy/apptainer/setup.sh` (venv) / `bash deploy/apptainer/start_postgres.sh` (DB).

---

## 1. DB Migration — alembic upgrade head

```bash
cd <repo>/api_server
DATABASE_URL="postgresql+asyncpg://aidh:<pw>@localhost:5432/aidh" \
  .venv/bin/alembic current
# 현재 head 확인 (예: 0020 또는 그 이전이면 다음 단계 필요)

DATABASE_URL="...same..." .venv/bin/alembic upgrade head
# 0021 + 0023 까지 자동 적용 (0022 는 의도적 skip — chain 은 0020→0021→0023)
```

**검증 SQL**:
```sql
\d+ mcp_uploads             -- 9 columns (name PK, current_sha, manifest, ...)
\d+ mcp_uploads_history     -- 11 columns (id PK, sha, version, smoke_result, ...)
\d+ mcp_upstreams           -- 14 columns (alias PK, transport, url, ...)
\d+ mcp_proxy_calls         -- 11 columns (id PK, ts, upstream_alias, latency_ms, ...)
\d+ record_sections         -- section_path / parent_section_id / chunk_index 컬럼 있음 (0019, 0020)
\di idx_sections_embedding_hnsw   -- HNSW 인덱스 존재 (0018)
```

이상 시: `alembic downgrade <revision>` 후 한 단계씩 재시도. 로그는 `logs/alembic.log`.

---

## 2. API 재기동 + Health

```bash
cd <repo>/deploy/apptainer
bash restart.sh                                  # API + (필요 시) postgres
# tail 8줄에 "Application startup complete" 와 "EMBEDDING_DIM consistency check OK" 두 라인 보여야 함
```

**Health 검증**:
```bash
HOST="http://<server>:8001"
curl -s $HOST/health                              # {"status":"ok"}
curl -s $HOST/api/system/health                   # 200 + db.connected=true
curl -s $HOST/api/discover | head -30             # catalog 응답
```

**MCP tool 일람** (built-in 12 + 동적 스크립트 2):
```bash
curl -s -X POST $HOST/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 -c "import sys, json; d=json.load(sys.stdin); print(len(d['result']['tools']))"
# 기대: 14
```

**신규 라우터**:
```bash
curl -s $HOST/api/mcp_tools/         # 빈 list (wave-5 도구 0개 — 다음 단계에서 추가)
curl -s $HOST/api/mcp/upstreams      # 빈 list (wave-6 upstream 0개)
curl -s $HOST/api/metrics/mcp        # JSONL tail 집계 (Prometheus 와 별개)
```

---

## 3. Wave-4 회귀 — echo_args / fetch_rerank_model

기존 운영 도구가 wave-5/6 추가 후에도 살아있는지.

```bash
# echo_args 호출 (MCP 직접)
curl -s -X POST $HOST/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"echo_args","arguments":{"message":"hello","repeat":2,"upper":true}}}'
# 기대: "HELLO\nHELLO\n" 가 stdout 에 보임
```

fetch_rerank_model 은 실행하면 ~600MB 다운로드 — 운영 정책상 별도 결정. 등록만 확인:
```bash
curl -s -X POST $HOST/mcp/ ... -d '{"jsonrpc":"2.0","id":3,"method":"tools/list",...}' \
  | python3 -c "import sys,json; print([t['name'] for t in json.load(sys.stdin)['result']['tools'] if 'rerank' in t['name']])"
# 기대: ['fetch_rerank_model']
```

---

## 4. Wave-5 End-to-End — stress_strain_plot 업로드 → 호출 → 검색

자동 스크립트: `bash deploy/apptainer/seed-stress-strain.sh` (아래 단계를 묶음).

### 4a. zip 생성 + 업로드

```bash
cd <repo>/examples/wave-5/stress_strain_plot
zip -r /tmp/stress_strain_plot.zip .
ls -la /tmp/stress_strain_plot.zip                # 보통 5~10KB

curl -s -X POST $HOST/api/mcp_tools/upload \
  -F "bundle=@/tmp/stress_strain_plot.zip" \
  -F 'metadata={"uploader":"ops-verify@local"}'
# 기대: 202 {"job_id":"...","status":"queued"}
```

`job_id` 받아 폴링:
```bash
JOB=<위 응답의 job_id>
for i in 1 2 3 4 5; do
  curl -s $HOST/api/mcp_tools/jobs/$JOB | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status'), d.get('step',''))"
  sleep 5
done
# 기대: queued → building → smoke_running → registered (보통 30~90s)
```

**실패 시 진단**:
```bash
curl -s $HOST/api/mcp_tools/jobs/$JOB | python3 -m json.tool
# log_tail 50줄 + error 코드 확인
# 주요 실패 코드: BUILD_TIMEOUT / SMOKE_EXIT_MISMATCH / RUNTIME_GUI_REQUIRED (이 도구는 OK 여야 함)
```

### 4b. 등록 확인

```bash
curl -s $HOST/api/mcp_tools/ | python3 -m json.tool
# stress_strain_plot 등록됨, current_version=1, sha=<64 hex>

# MCP tool list 가 14 → 15 로 증가
curl -s -X POST $HOST/mcp/ ... tools/list ... \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)['result']['tools']))"
# 기대: 15
```

### 4c. 도구 호출 (MCP 직접 — Claude Desktop 대신 curl 검증)

```bash
curl -s -X POST $HOST/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc":"2.0","id":10,"method":"tools/call",
    "params":{
      "name":"stress_strain_plot",
      "arguments":{"material_name":"SUS304","e_modulus":200.0,"yield_stress":215.0,"ultimate_strain":0.4}
    }}' | python3 -m json.tool
```

**기대 응답 (P1.5 + P1.6 + P1.8)**:
- `result.content[0].type == "text"` — summary JSON
  - `persisted.record_id == "SIM-HE-CAE-2026-..."` (다음 가용 SEQ)
  - `persisted.attachment_count == 1`
  - `persisted.section_count == 2` (메인 + parsed)
  - `persisted.embedded == true` (P1.7)
  - `attachments` 배열에 절대 URL 1개
- `result.content[1].type == "image"`, `mimeType == "image/png"`, `data` 가 base64

### 4d. 파일 시스템 검증

```bash
# attachment 영구 저장 확인 (P1.6)
ls -la <repo>/api_server/static/attachments/SIM-HE-CAE-2026-*/
# 기대: sus304.png (~37KB)

# attachment URL 로 직접 다운로드
curl -s -o /tmp/sus304.png $HOST/attachments/SIM-HE-CAE-2026-XXXXXXXXXX/sus304.png
file /tmp/sus304.png
# 기대: PNG image data, 720 x 480
```

### 4e. DB 검증 (P1.6 + P1.7)

```sql
-- records 행
SELECT id, data_type, team, "group", title, attachment_count, has_attachments
  FROM records WHERE data_type='SIM' AND team='HE' AND "group"='CAE'
  ORDER BY created_at DESC LIMIT 3;

-- sections + embedding (P1.7)
SELECT section_id, title, length(content_text), (embedding IS NOT NULL) AS embedded
  FROM record_sections WHERE record_id='<위 SIM-...>';

-- mcp_uploads + history
SELECT name, current_sha, current_version, registered_at, registered_by FROM mcp_uploads;
SELECT name, sha, version, registered, smoke_result->'overall' FROM mcp_uploads_history;
```

### 4f. 시맨틱 검색 회수 (재귀 RAG)

도구가 만든 record 를 즉시 의미검색이 발견하는지:

```bash
curl -s -G "$HOST/api/search" \
  --data-urlencode "mode=semantic" \
  --data-urlencode "q=SUS304 stress strain bilinear" \
  --data-urlencode "limit=5" | python3 -m json.tool
# 기대: 위에서 만든 SIM-HE-CAE-... 가 top 결과에 등장 (score > 0.5)
```

```bash
curl -s -X POST $HOST/api/recommend/agents \
  -H 'Content-Type: application/json' \
  -d '{"q":"SUS304 stress strain","top_k":5}' | python3 -m json.tool
# 기대: 적합 agent + (P1.7 embedding 덕분에) 위 record 가 evidence 로 포함
```

### 4g. Claude Desktop 실 사용 (선택)

extension 으로 클라이언트 자동 등록:
1. VSCode 에 v0.16.0 확장 설치 (`./out/...` 또는 `/downloads/`)
2. Settings → "Mobile eXperience AI Data Hub: Settings" → base URL = `$HOST`
3. "Reset Connection" → 11 클라이언트 중 `claude_desktop` 선택 → 자동 install
4. Claude Desktop 완전 종료 → 재시작 → 좌하 MCP 아이콘에 14~15 tools 표시
5. 채팅: "SUS304 의 stress-strain 곡선 그려줘"
6. 기대: Claude 가 stress_strain_plot 호출 → **채팅에 PNG 인라인 표시** + record id 인용 + attachment URL

---

## 5. Wave-6 P1 (federation) — 옵션

외부 MCP 서버가 있을 때만:

```bash
# 1. config 생성
cp <repo>/config/upstream_mcps.example.yaml <repo>/config/upstream_mcps.yaml
vim <repo>/config/upstream_mcps.yaml          # 실 url, env_var 명, enabled=true

# 2. 토큰 env
export ANALYTICS_MCP_TOKEN=<your-token>

# 3. env 추가 + 재기동
echo 'AIDH_UPSTREAM_CONFIG=/opt/aidh/config/upstream_mcps.yaml' >> deploy/apptainer/.env
bash deploy/apptainer/restart.sh

# 4. 등록 확인
curl -s $HOST/api/mcp/upstreams | python3 -m json.tool
# 기대: alias 별 last_health_status="ok", last_tool_count > 0

# 5. tools/list 가 자체 14 + 동적 스크립트 + 도구 + namespaced (alias__tool_name) 까지 노출
curl -s -X POST $HOST/mcp/ ... tools/list ... \
  | python3 -c "import sys,json; tools=json.load(sys.stdin)['result']['tools']; \
                ns=[t['name'] for t in tools if '__' in t['name']]; print('federated:', ns)"

# 6. proxy 호출 audit log
curl -s "$HOST/api/metrics/mcp/proxy" | python3 -m json.tool
```

---

## 6. 회귀·이상 진단

| 증상 | 진단 위치 | 조치 |
|---|---|---|
| 도구 업로드 200 응답이지만 status=failed | `GET /api/mcp_tools/jobs/<id>` 의 `log_tail` | 메시지 코드 (BUILD_TIMEOUT, LDD_MISSING_LIB 등) → wave-5 plan §13 카탈로그 |
| 도구 호출 시 PNG 가 채팅에 안 보임 | MCP 응답에 `content[1].type=="image"` 있는지 | 없으면 매니페스트 `return.capture_files.enabled=true` 확인 |
| attachment URL 이 상대로만 나옴 | env `MCP_BASE_URL` 미설정 | `.env` 에 추가 후 재기동 |
| Claude Desktop "지원포맷 아님" | 클라이언트 config 가 url 직접 등록 | extension 0.16.0 의 `claude_desktop` install (mcp-remote stdio wrap) 사용 |
| dedup_key 가 동작 안 함 | DB 로그에 JSON path 에러 | SQLite (테스트만), 운영은 PG → 정상 |
| embedding=null 인 sections | `embed_handler` 백필 job 실행 | `curl -X POST $HOST/api/jobs/embed?backfill=true` |
| HNSW 인덱스 미생성 | `\di idx_sections_embedding_hnsw` 결과 없음 | `alembic upgrade head` 재시도. ivfflat 만 있으면 0018 적용 안 됨 |
| federation upstream 가 자꾸 down | `mcp_proxy_calls` 의 error_code | `UPSTREAM_UNREACHABLE / AUTH_FAILED / TIMEOUT` 별 plan §8 카탈로그 |

---

## 7. 로그 위치

| 항목 | 경로 |
|---|---|
| API access (구조화 JSON) | `logs/api.access.log` |
| MCP 호출 (JSONL) | `logs/mcp-calls.jsonl` |
| Apptainer build log (wave-5 P1) | `logs/aidh-build-<sha>.log` |
| Postgres | `logs/postgres-*.log` |
| Alembic | stdout (재기동 시 console) |

```bash
# MCP 호출 집계 — 도구별 p95 latency / error rate
curl -s "$HOST/api/metrics/mcp?tail=500" | python3 -m json.tool
```

---

## 8. 1주 후 자연어 재발견 시나리오

P1.7 의 진정한 가치 — 도구 결과가 다음 검색의 근거:

1. Day 0: Claude Desktop "SUS304 stress-strain 그려줘" → 위 §4c 동작
2. Day 7: Claude Desktop "지난주 SUS304 결과 보여줘"
   - LLM: `recommend_agents(q="...")` → agent 추천
   - LLM: `agent_search(agent, q="SUS304 stress strain", mode="hybrid")` → SIM-HE-CAE-... record 발견
   - LLM: `get_record_sections(SIM-HE-CAE-..., sections=["1"])` → 본문 + attachment URL
   - LLM 답변: "지난주 SUS304 분석: yield 215 MPa, max 322.5 MPa. [PNG](http://.../sus304.png) (source: SIM-HE-CAE-... §1)"

이 흐름이 작동하면 wave-5 P1 라인 완성.

---

## 9. 멀티 런타임 운영 체크리스트 (v0.5.0 — P1.9)

v0.5.0 부터 `generate_def` 가 4 런타임 지원. 폐쇄망 운영을 위해 base sif **사전 fetch + 배치 필수**.

### 9.1 base sif 사전 준비 (1회)

| runtime | base image | 사전 sif 파일명 |
|---|---|---|
| Python 3.12 | `python:3.12-slim` | `python-3.12-slim.sif` |
| Python 3.11 | `python:3.11-slim` | `python-3.11-slim.sif` |
| Node 20 | `node:20-slim` | `node-20-slim.sif` |
| Node 22 | `node:22-slim` | `node-22-slim.sif` |
| JVM 17 | `eclipse-temurin:17-jre` | `eclipse-temurin-17-jre.sif` |
| JVM 21 | `eclipse-temurin:21-jre` | `eclipse-temurin-21-jre.sif` |
| .NET 8 | `mcr.microsoft.com/dotnet/runtime:8.0` | `mcr.microsoft.com-dotnet-runtime-8.0.sif` |

```bash
# 외부 접근 가능 머신에서:
for IMG in "python:3.12-slim" "node:20-slim" "eclipse-temurin:17-jre" "mcr.microsoft.com/dotnet/runtime:8.0"; do
  SIF=$(echo "$IMG" | tr ':/' '-').sif
  apptainer pull "$SIF" "docker://$IMG"
done

# target 서버로 scp:
scp *.sif user@<target>:/opt/aidh/base-sifs/

# target 서버 .env:
echo "AIDH_BASE_SIF_DIR=/opt/aidh/base-sifs" >> deploy/apptainer/.env
bash deploy/apptainer/restart.sh --api
```

### 9.2 런타임별 smoke 검증

각 런타임이 올바르게 빌드/실행되는지 1 회 검증.

| runtime | 예제 zip | 기대 응답 |
|---|---|---|
| Python | `examples/wave-5/stress_strain_plot/` | ImageContent + record_id |
| Node | `examples/wave-5/node_csv_summary/` | TextContent (JSON) + record_id |
| JVM | (예제 미작성 — 매니페스트 예시는 plan §5.9 참조) | TextContent + record_id |
| .NET | (예제 미작성 — 매니페스트 예시는 plan §5.9 참조) | TextContent + record_id |

검증 명령 (Node 예제):

```bash
HOST="http://<server>:8001"
cd <repo>/examples/wave-5/node_csv_summary
zip -r /tmp/node_csv.zip csv_summary.js manifest.yaml samples/

JOB=$(curl -s -F "uploader=ops" -F "package=@/tmp/node_csv.zip" \
        $HOST/api/mcp_tools/upload \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['job_id'])")

# 폴링 — completed 까지 대기
while true; do
  ST=$(curl -s $HOST/api/mcp_tools/jobs/$JOB | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  echo "  status=$ST"
  [[ "$ST" == "completed" || "$ST" == "failed" ]] && break
  sleep 2
done

# tools/call 검증
curl -s -X POST $HOST/mcp -H 'Accept: application/json,text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"csv_summary","arguments":{"csv_path":"/work/samples/case_numeric.json"}}}' \
  | sed -n 's/^data: //p' | head -1 | python3 -m json.tool
```

### 9.3 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `BUILD_FAIL: proxyconnect tcp` | 사내 proxy 가 docker.io 접근 차단 | `.env` 에 base sif 사전 배치 → `AIDH_BASE_SIF_DIR` 지정 |
| `Bootstrap: docker` 로 떨어짐 (사전 sif 있음에도) | basename 미스매치 — `:` 와 `/` 모두 `-` 치환 | `mv` 로 이름 통일 |
| `apptainer: Invalid value "none"` (--net=none) | apptainer 의 `--net` 은 boolean | 자동 처리 — 코드는 `--net --network none` 사용 |
| sif 크기 폭주 (수 GB) | `%files . /opt/tool` 의 `.` 가 process cwd | 자동 처리 — `subprocess.run(cwd=dest_dir)` |
| .NET self-contained 가 동작 안 함 | 매니페스트의 `target_framework` 가 image 와 불일치 | `net8.0` ↔ runtime 8.0, `net9.0` ↔ runtime 9.0 매칭 |

### 9.4 force docker bootstrap (디버깅)

테스트 / CI / "사전 sif 가 손상되었나?" 의심 시 강제로 docker bootstrap 시도:

```bash
AIDH_DISABLE_LOCALIMAGE=1 bash deploy/apptainer/restart.sh --api
# 다음 업로드는 base sif 무시하고 docker://... 로 시도
# (인터넷 접근 가능한 환경에서만 동작)
```

운영 시 항상 해제 (`unset AIDH_DISABLE_LOCALIMAGE`).

---

## 완료 신호

위 모든 단계 PASS 시 wave-5 P1~P1.9 운영 검증 종료. report-generator 가 자동 보고서 작성 가능:

```bash
/pdca report wave-5-binary-mcp
```

다음 트랙 후보:

- Wave-5 P2 Dashboard UI (사용자 진입 장벽 낮춤)
- Wave-6 P2 stdio transport + 헬스체크 worker
- Wave-7 — agent 통합 (wave-5 도구를 agent_definitions 에 자동 등록 → recommend_agents 가 도구 추천)
- 운영 회귀 자동화 (`verify-wave-5.sh` cron)
