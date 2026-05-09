# 설정 (CONFIG)

## API 서버 주소

본 가이드 팩 전체에 다음 URL 이 **하드코딩** 되어 있다:

```text
http://110.15.177.125:8000
```

(이 PC 의 외부 IP. 같은 LAN 내부에서는 LAN IP `192.168.0.133:8000` 도 사용 가능 — 본 팩은 외부 직결을 기본으로 한다.)

### URL 변경이 필요한 경우

다른 서버/포트에서 운영 중이면 본 팩의 모든 파일에서 `110.15.177.125:8000` 를 일괄 교체:

**Python 클라이언트** ([`examples/python_client.py`](./examples/python_client.py)):

```python
BASE = "http://110.15.177.125:8000"  # ← 이 한 줄만 변경
```

**curl 스크립트** ([`examples/curl_smoke.sh`](./examples/curl_smoke.sh)):

```bash
BASE="http://110.15.177.125:8000"  # ← 이 한 줄만 변경
```

**TypeScript 클라이언트** ([`examples/ts_client.ts`](./examples/ts_client.ts)):

```typescript
const BASE = "http://110.15.177.125:8000";  // ← 이 한 줄만 변경
```

**전체 일괄 교체** (Linux/macOS/Git Bash):

```bash
NEW="http://your-server:8000"
grep -lr "http://110.15.177.125:8000" agent_pack/ | xargs sed -i "s|http://110\.15\.177\.125:8000|$NEW|g"
```

PowerShell:

```powershell
$NEW = "http://your-server:8000"
Get-ChildItem agent_pack -Recurse -File | ForEach-Object {
  (Get-Content $_.FullName) -replace 'http://110\.15\.177\.125:8000', $NEW | Set-Content $_.FullName
}
```

---

## 인증 (X-API-Key)

기본은 **인증 OFF** (`AUTH_REQUIRED=false`). 헬스체크 응답 `auth_required` 필드로 확인:

```bash
curl http://110.15.177.125:8000/api/system/health
# {"status":"ok","auth_required":false,...}
```

`auth_required=true` 이면 모든 요청에 `X-API-Key` 헤더 필요:

```bash
curl -H "X-API-Key: <발급받은_키>" http://110.15.177.125:8000/api/discover
```

Python:

```python
import urllib.request
req = urllib.request.Request(
    "http://110.15.177.125:8000/api/discover",
    headers={"X-API-Key": "your-key-here"},
)
```

키 발급은 운영자가 다음 명령으로:

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
