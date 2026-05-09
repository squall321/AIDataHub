# MCP Integration Guide (Cline SR / Claude Desktop)

> **Note**: 이 문서는 기존 `mcp_integration.md` 의 확장판이다. 이전 가이드는 4개
> 도구만 다루었으나, 본 가이드는 Agent 30 이후의 9개 도구 (discover_schema,
> discover_capabilities, ask, find_related, explain_field, query_data,
> list_agents, get_record, search) 와 실제 운영 패턴을 모두 포함한다.

---

## 1. What is MCP

**Model Context Protocol (MCP)** 는 LLM 클라이언트(Cline SR, Claude Desktop,
Cursor 등) 와 외부 도구/데이터 소스를 연결하기 위한 표준 프로토콜이다.
서버는 stdio 또는 SSE 트랜스포트를 통해 `tools`, `resources`, `prompts` 를
노출하고, 클라이언트는 이를 LLM 호출 컨텍스트에 자동 주입한다. 이 프로젝트의
`ai-data-hub` MCP 서버는 사내 REST API 를 LLM 호출 가능한 도구로 래핑하여,
사용자가 자연어로 질문하면 LLM 이 적절한 도구를 호출하고 사내 데이터 허브에서
컨텍스트를 가져와 응답하도록 한다.

---

## 2. Prerequisites

| 항목                         | 확인 방법                                                       |
|------------------------------|-----------------------------------------------------------------|
| API 서버 실행 중             | `curl http://localhost:8000/health` → `{"status":"ok"}`         |
| Python 3.12 + venv 활성      | `& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -V` |
| `mcp` 패키지 설치            | `pip show mcp` (>= 1.2.0)                                       |
| MCP-capable client 설치      | Cline SR 확장 또는 Claude Desktop                               |
| (운영 시) API key 발급       | `POST /api/auth/keys` (BOOTSTRAP_API_KEY 헤더 필요)             |

```powershell
# 1) 의존성 설치
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# 2) API 서버 기동 (별도 터미널)
$env:PYTHONPATH = "src"
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m api.main

# 3) MCP 서버 단독 검증 (stdio)
$env:API_URL = "http://localhost:8000"
$env:PYTHONPATH = "d:\Personal\AI_data\api_server\src"
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m mcp_server
```

---

## 3. Cline SR Configuration

### 3.1 settings 파일 위치

| 범위        | 경로                                                           |
|-------------|----------------------------------------------------------------|
| 사용자 전역 | `%APPDATA%\Cline\mcp_settings.json`                            |
| 워크스페이스 | `<repo>/.vscode/cline_mcp_settings.json`                       |
| (alt) Claude Dev | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` |

### 3.2 권장 JSON

```json
{
  "mcpServers": {
    "ai-data-hub": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {
        "API_URL": "http://localhost:8000",
        "API_KEY": "<your-key>",
        "PYTHONPATH": "d:\\Personal\\AI_data\\api_server\\src",
        "PYTHONIOENCODING": "utf-8"
      },
      "disabled": false,
      "autoApprove": [
        "discover_schema",
        "discover_capabilities",
        "list_agents",
        "get_record",
        "query_data",
        "search",
        "ask",
        "find_related",
        "explain_field"
      ]
    }
  }
}
```

> 시스템 PATH 의 `python` 이 venv 가 아닌 경우 `command` 를 venv 절대경로로
> 바꾼다: `"d:\\Personal\\AI_data\\api_server\\.venv\\Scripts\\python.exe"`.
> `autoApprove` 는 read-only 도구만 등록한다 (현재 9개 모두 read-only).

### 3.3 Windows 경로 주의

JSON 안에서는 백슬래시를 `\\` 로 이스케이프하거나, 슬래시를 사용한다 (`d:/Personal/...`).

---

## 4. Claude Desktop Configuration

### 4.1 설정 파일 위치

| OS        | 경로                                                        |
|-----------|-------------------------------------------------------------|
| Windows   | `%APPDATA%\Claude\claude_desktop_config.json`               |
| macOS     | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux     | `~/.config/Claude/claude_desktop_config.json`               |

### 4.2 macOS 예시

```json
{
  "mcpServers": {
    "ai-data-hub": {
      "command": "/Users/me/AI_data/api_server/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "env": {
        "API_URL": "http://localhost:8000",
        "API_KEY": "<your-key>",
        "PYTHONPATH": "/Users/me/AI_data/api_server/src"
      }
    }
  }
}
```

### 4.3 Windows 예시 (Claude Desktop)

```json
{
  "mcpServers": {
    "ai-data-hub": {
      "command": "d:\\Personal\\AI_data\\api_server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server"],
      "env": {
        "API_URL": "http://localhost:8000",
        "API_KEY": "<your-key>",
        "PYTHONPATH": "d:\\Personal\\AI_data\\api_server\\src",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

설정 후 Claude Desktop 을 완전히 종료(시스템 트레이) 후 재시작한다.

---

## 5. Available Tools Reference

### 5.1 `discover_schema()` — 시작점 (가장 먼저 호출)

| 항목     | 값                                                                |
|----------|-------------------------------------------------------------------|
| 시그니처 | `discover_schema() -> dict`                                       |
| When     | 에이전트가 데이터 허브 구조를 모를 때 첫 호출                      |
| Backend  | `GET /api/discover` + `GET /api/schema`                            |

**예시 사용자 질문**: "이 데이터 허브에 뭐가 있는지 알려줘"
→ LLM 호출: `discover_schema()`
→ 응답: `{discover: {agents: [...], data_types: [...], starting_points: [...]}, schema: {...}}`

### 5.2 `discover_capabilities(agent_type)`

| 항목     | 값                                                          |
|----------|-------------------------------------------------------------|
| 시그니처 | `discover_capabilities(agent_type: str)`                    |
| When     | 특정 에이전트 타입의 보유 데이터/태그가 궁금할 때           |
| Backend  | `GET /api/agents/{type}` + `/api/agents/{type}/records`     |

**예시**: "iga-analyst 에이전트는 뭘 다뤄?"
→ `discover_capabilities("iga-analyst")`
→ `{agent: {...common_tags, data_types}, record_count: 12, sample_records: [...]}`

### 5.3 `ask(query, limit=5)`

| 항목     | 값                                                      |
|----------|---------------------------------------------------------|
| 시그니처 | `ask(query: str, limit: int = 5)`                       |
| When     | 사용자가 한국어/영어 자연어로 질문할 때                  |
| Backend  | `POST /api/ask {query, limit}`                          |

**예시**: "최근 1주일 IGA 시뮬레이션 결과"
→ `ask("최근 1주일 IGA 시뮬레이션 결과", 5)`
→ `{interpreted_query: {...}, results: [...], follow_up_queries: [...]}`

### 5.4 `find_related(record_id, mode='auto')`

| 항목     | 값                                                          |
|----------|-------------------------------------------------------------|
| 시그니처 | `find_related(record_id: str, mode: str = "auto")`          |
| Modes    | `tags` \| `graph` \| `semantic` \| `auto`                   |
| When     | 한 record 를 알고 있고 비슷/연관 record 를 찾고 싶을 때     |

**예시**: "DOC-HE-CAE-2026-000001 이거랑 비슷한 자료 있어?"
→ `find_related("DOC-HE-CAE-2026-000001", mode="auto")`
→ `{related: [...], by_mode: {tags: [...], graph: [...], semantic: [...]}}`

### 5.5 `explain_field(field_name)`

| 항목     | 값                                            |
|----------|-----------------------------------------------|
| 시그니처 | `explain_field(field_name: str)`              |
| When     | 특정 필드의 의미/허용값이 궁금할 때           |
| Backend  | `GET /api/schema` 에서 단일 필드 추출         |

**예시**: "data_type 필드는 뭐야?"
→ `explain_field("data_type")`
→ `{spec: {...}, is_enum: true, allowed_values: ["DOCUMENT","TABLE",...]}`

### 5.6 `query_data(agent, query='', limit=5)`

| 항목     | 값                                                                  |
|----------|---------------------------------------------------------------------|
| 시그니처 | `query_data(agent: str, query: str = "", limit: int = 5)`           |
| When     | 에이전트 타입을 알고 그 안에서 키워드로 좁힐 때                      |
| Backend  | `GET /api/data?agent={agent}&query={query}&limit={limit}`           |

**예시**: "iga-analyst 에이전트에서 offset 처리 관련 자료 찾아줘"
→ `query_data(agent="iga-analyst", query="offset 처리", limit=5)`

### 5.7 `list_agents()`

| 항목     | 값                                                              |
|----------|-----------------------------------------------------------------|
| 시그니처 | `list_agents() -> dict`                                         |
| When     | 어떤 에이전트가 있는지 빠르게 보고 싶을 때                      |
| Backend  | `GET /api/agents`                                               |

### 5.8 `get_record(record_id)`

| 항목     | 값                                          |
|----------|---------------------------------------------|
| 시그니처 | `get_record(record_id: str)`                |
| When     | 특정 레코드 전문(섹션·태그·메타) 가 필요할 때 |
| Backend  | `GET /api/records/{id}`                     |

### 5.9 `search(mode, query='', tags=None)`

| 항목     | 값                                                              |
|----------|-----------------------------------------------------------------|
| 시그니처 | `search(mode: str, query: str = "", tags: list[str] = None)`    |
| Modes    | `tag` \| `fts` \| `semantic`                                    |
| Backend  | `GET /api/search?mode={...}&q={...}&tags={...}`                 |

---

## 6. Recommended Agent Patterns

### 6.1 Discovery-First Pattern

데이터 허브를 처음 접하는 에이전트의 안전한 진입.

```
1) discover_schema()                         # 전체 구조 / 에이전트 목록 / 필드
2) discover_capabilities(agent_type=...)     # 관심 에이전트 좁히기
3) query_data(agent=..., query=...)          # 실제 데이터 조회
4) get_record(record_id=...)                 # 상세 본문
```

### 6.2 Specific Agent Role Pattern

에이전트 타입을 사용자가 명시했거나 시스템 프롬프트에 박혀 있을 때.

```
1) query_data(agent="cae-reporter", query=user_query, limit=5)
2) (필요 시) get_record(record_id=results[0].id)
```

### 6.3 Find-Related (Graph Traversal) Pattern

```
1) get_record(record_id=X)
2) find_related(record_id=X, mode="auto")
3) (선택) get_record() 으로 흥미로운 후보 깊이 파기
```

### 6.4 Natural-Language First Pattern

사용자 질의가 모호하거나 의도 파악이 우선일 때.

```
1) ask(query=user_query, limit=5)            # interpreted_query 로 의도 확인
2) follow_up_queries 가 있으면 그 중 하나로 ask() 재호출
3) 결과 record 를 get_record() 로 보강
```

---

## 7. End-to-End Scenarios

### 시나리오 1: "사업부 IGA 해석 자료 찾아줘"

```
User: 사업부 IGA 해석 자료 찾아줘
LLM:  ask("사업부 IGA 해석 자료", 5)
      → {results: [...], interpreted_query: {agent: "iga-analyst", ...}}
LLM:  (필요 시) query_data(agent="iga-analyst", limit=10)
LLM:  사용자에게 결과 요약 응답
```

### 시나리오 2: "이 문서 요약해줘 (DOC-HE-CAE-2026-000001)"

```
User: DOC-HE-CAE-2026-000001 문서 요약해줘
LLM:  get_record("DOC-HE-CAE-2026-000001")
      → {title, summary, sections: [...], tags: [...]}
LLM:  sections 를 토대로 사용자에게 자연어 요약 제공
```

### 시나리오 3: "이거랑 비슷한 자료 있어?"

```
User: DOC-HE-CAE-2026-000001 이거랑 비슷한 거 더 보여줘
LLM:  find_related("DOC-HE-CAE-2026-000001", mode="auto")
      → {related: [{id, title}, ...], by_mode: {...}}
LLM:  태그/그래프/시맨틱 별로 묶어서 사용자에게 제시
```

### 시나리오 4: "어떤 부서가 어떤 자료를 가지고 있어?"

```
User: 어느 부서가 어떤 데이터를 갖고 있어?
LLM:  discover_schema()
      → {discover: {agents: [{type, owner_dept, data_types, count}, ...]}}
LLM:  agents 배열을 부서별로 그룹화하여 사용자에게 표 형태로 제공
```

### 시나리오 5: "ID는 모르겠고 그냥 검색해줘"

```
User: 배터리 셀 열폭주 시뮬레이션 보고서 있나?
LLM:  ask("배터리 셀 열폭주 시뮬레이션 보고서", 10)
      → {results: [...]}
LLM:  결과가 비어 있으면 search(mode="fts", query="열폭주") 폴백
LLM:  최상위 결과 1-3개 요약 + 추가 follow-up 제안
```

---

## 8. Troubleshooting

| 증상                                                         | 원인 / 조치                                                                                           |
|--------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Cline 에서 `Failed to start MCP server`                      | `command` 의 파이썬 경로 확인. PowerShell 에서 `& "<경로>" -V` 로 검증.                              |
| 도구는 보이는데 호출 시 timeout                              | API 서버 미기동. `curl http://localhost:8000/health` 확인. `API_TIMEOUT` 환경변수 늘리기.            |
| 도구 목록이 비어 있음                                        | `mcp_server` import 단계 실패. 직접 `python -m mcp_server` 실행해 traceback 확인.                    |
| `error: http_error, status_code: 404`                        | 잘못된 record_id 또는 라우터 미구현. `GET /docs` 에서 라우트 확인.                                    |
| 한글이 `?` 로 표시됨                                         | `PYTHONIOENCODING=utf-8` 설정. Windows 콘솔이라면 `chcp 65001`.                                       |
| `401 Unauthorized` (AUTH_REQUIRED=true 환경)                 | env 의 `API_KEY` 가 누락 또는 만료. (현 MCP wrapper 는 `API_KEY` 미주입 — 11절 참고)                  |
| Cline 은 잘 되는데 다른 클라이언트에서 안 됨                 | 클라이언트별 stdio launch 규약 차이. 필요 시 SSE 트랜스포트 검토.                                     |

---

## 9. Security

### 9.1 API key 관리

- API key 는 환경변수(`API_KEY`)로만 주입한다. JSON 설정 파일을 git 에 커밋하지
  않는다 (`.vscode/` 는 워크스페이스에서 ignore 권장).
- Cline SR 의 SecretStorage 또는 OS 키체인 활용을 권장한다 (`docs/cline_sr_recommendations.md`).
- 운영에서는 `BOOTSTRAP_API_KEY` 와 일반 `X-API-Key` 를 분리한다.
  - bootstrap: 키 발급/관리만 수행. constant-time 비교.
  - X-API-Key: 일반 호출용. agent_scopes 로 권한 제한.

### 9.2 Read-only by default

현재 9개 MCP 도구는 모두 read-only (조회만). `autoApprove` 에 안전하게 등록 가능.

| 도구                         | 부수효과                                       |
|------------------------------|------------------------------------------------|
| discover_schema              | 없음 (캐시 60s)                                |
| discover_capabilities        | 없음                                           |
| ask                          | 없음 (POST 지만 멱등 검색)                     |
| find_related                 | 없음                                           |
| explain_field                | 없음                                           |
| query_data                   | `read_count` 백그라운드 증가 (관측용)          |
| list_agents                  | 없음                                           |
| get_record                   | (향후) `read_count` 증가 — best-effort         |
| search                       | 없음                                           |

### 9.3 향후 write tools

문서 인제스트(POST)·삭제(DELETE) 같은 mutating tool 추가 시:

- `autoApprove` 에 절대 등록하지 않는다.
- 별도 admin scope 의 API key 만 사용 가능하도록 한다 (`agent_scopes: ["admin:*"]`).
- 클라이언트 UI 에서 명시적 confirm 을 거쳐 호출.

---

## 10. References

- API reference: `docs/api_reference.md`
- Setup: `docs/setup_guide.md`
- Observability (`/metrics`): `docs/observability.md`
- Cline SR 운영 권장사항: `docs/cline_sr_recommendations.md`
- 부하 시나리오: `scripts/load_test/README.md`
- 검증 스크립트: `scripts/mcp_smoke.py`
