# 설정 (CONFIG)

## API 서버 주소 — 한 곳만 바꾸면 전파

현재 캐노니컬 URL 은 [`.api_url`](./.api_url) 마커 파일에 한 줄로 저장되어 있다:

```text
http://110.15.177.125:8000
```

(이 PC 의 외부 IP. 같은 LAN 내부에서는 LAN IP `192.168.0.133:8000` 도 사용 가능.)

본 팩의 모든 .md / .py / .sh / .ts / .json 파일에 동일 URL 이 **하드코딩** 되어 있어 standalone 으로 동작한다. 변경 시 두 가지 방식 중 하나:

### 1. 일괄 갱신 (권장 — 영구 반영)

```bash
# Python (cross-platform)
cd agent_pack
python update_url.py http://new-server:8000

# 또는 PowerShell
.\update_url.ps1 http://new-server:8000

# 미리보기 (변경 안 함)
python update_url.py --dry-run http://new-server:8000
```

스크립트는:

- `.api_url` 의 현재 URL 을 읽어 그것만 정확히 치환 (다른 URL 건드리지 않음)
- .md / .py / .sh / .ts / .json / .txt / .yaml 파일 전체 스캔
- 치환 후 `.api_url` 도 갱신 (idempotent — 재실행 시 "already at NEW" 출력)
- 변경된 파일·횟수 보고

### 2. 환경변수 override (런타임 임시)

각 예제 클라이언트는 `AIDH_API_URL` 환경변수가 있으면 우선 사용:

```bash
# Linux / macOS / Git Bash
export AIDH_API_URL="http://other-server:8080"
python examples/python_client.py
bash examples/curl_smoke.sh

# PowerShell
$env:AIDH_API_URL = "http://other-server:8080"
python examples/python_client.py

# Node (TS client)
AIDH_API_URL=http://other-server:8080 node examples/ts_client.js
```

env var 가 빈 문자열 / 미설정이면 하드코딩 값으로 자동 폴백.

API key 도 동일 방식으로 `AIDH_API_KEY` env var 지원.

---

## 인증 (X-API-Key)

기본은 **인증 OFF** (`AUTH_REQUIRED=false`). 헬스체크 응답 `auth_required` 필드로 확인:

```bash
curl http://110.15.177.125:8000/api/system/health
# {"status":"ok","auth_required":false,...}
```

`auth_required=true` 이면 모든 요청에 `X-API-Key` 헤더 필요:

```bash
# 환경변수로 주입 (예제 클라이언트는 자동 인식)
export AIDH_API_KEY="your-key-here"
python examples/python_client.py

# 또는 직접
curl -H "X-API-Key: your-key" http://110.15.177.125:8000/api/discover
```

키 발급은 운영자가:

```powershell
cd api_server
.venv\Scripts\activate
$env:PYTHONPATH = "src"
python -m api.cli issue-key --name <agent-name>
```

출력된 평문 키를 한 번만 받아서 안전하게 저장 (DB 에는 hash 만 저장됨).

---

## CORS

서버는 다음을 자동 허용:

- 모든 origin (`*`) — 개발 단계 기본값
- `vscode-webview://*` (정규식)
- `EXTRA_ALLOWED_ORIGINS` 환경변수에 콤마로 추가된 origin

운영 시에는 `*` 제거 + 화이트리스트 권장 — `api_server/src/api/routes/__init__.py:54-63`.

---

## Rate limiting

현재 서버 측 rate limit **미적용**. 작은 모델/agent 가 폭주적으로 호출하지 않도록 클라이언트 측 자체 throttle 권장 (예: 10 req/s 미만).

---

## 응답 헤더

| 헤더 | 의미 |
|---|---|
| `X-Request-ID` | 모든 응답에 동봉됨. 트러블슈팅 시 운영자에게 전달. |
| `Content-Type` | `application/json` (대부분), markdown text 인 경우 `text/markdown`. |

---

## 환경 / 배포 컨텍스트

본 서버는 다음 스택으로 운영:

- **OS**: Windows / Linux 모두 지원
- **PostgreSQL 18 + pgvector 0.8.2** — `vector(384)` cosine 인덱스
- **임베딩**: `intfloat/multilingual-e5-small` (default), 또는 `hash` / `openai`
- **FastAPI + uvicorn** — 단일 워커
- **dashboard**: <http://110.15.177.125:8000/dashboard/>
