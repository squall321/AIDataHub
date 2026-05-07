# MCP Integration Guide

## MCP 란

Model Context Protocol(MCP)은 LLM 클라이언트(Cline SR, Claude Desktop 등)와
외부 도구/데이터 소스를 표준화된 방식으로 연결하기 위한 프로토콜이다.
서버는 stdio 또는 SSE 트랜스포트로 도구(`tools`), 리소스(`resources`),
프롬프트(`prompts`)를 노출하고, 클라이언트는 LLM 호출 컨텍스트에 자동으로 주입한다.

이 프로젝트의 `ai-data-hub` MCP 서버는 사내 REST API를
LLM-호출 가능한 도구로 래핑한다. 사용자가 Cline SR 등에서 자연어로 질문하면
LLM이 적절한 도구를 호출 → 사내 데이터 허브에서 컨텍스트를 가져와 응답한다.

## 사전 준비

```powershell
# 1) 의존성 설치
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # mcp>=1.2.0 포함

# 2) API 서버 기동 (별도 터미널)
$env:PYTHONPATH = "src"
python -m api.main      # http://localhost:8000

# 3) 단독 테스트
python -m mcp_server     # stdio 모드로 대기
```

## Cline SR 등록 (settings.json)

VS Code Cline 확장의 MCP 설정 파일(보통
`%APPDATA%\Cline\mcp_settings.json` 또는 워크스페이스
`.vscode/cline_mcp_settings.json`)에 다음을 추가한다.

```json
{
  "mcpServers": {
    "ai-data-hub": {
      "command": "d:\\Personal\\AI_data\\api_server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server"],
      "env": {
        "PYTHONPATH": "d:\\Personal\\AI_data\\api_server\\src",
        "API_URL": "http://localhost:8000",
        "API_TIMEOUT": "30",
        "PYTHONIOENCODING": "utf-8"
      },
      "disabled": false,
      "autoApprove": ["list_agents", "get_record", "query_data", "search"]
    }
  }
}
```

> **Windows 경로 주의**: JSON 안에서는 백슬래시를 `\\`로 이스케이프하거나
> `"d:/Personal/.../python.exe"` 처럼 슬래시를 사용한다.

Claude Desktop 의 경우 `%APPDATA%\Claude\claude_desktop_config.json` 의
`mcpServers` 키에 동일한 항목을 넣는다.

## 노출되는 도구

| Tool        | 인자                                          | 설명                                        |
|-------------|-----------------------------------------------|---------------------------------------------|
| `query_data` | `agent: str`, `query: str = ""`, `limit: int = 5` | 에이전트별 컨텍스트(레코드/섹션) 조회        |
| `list_agents` | (없음)                                        | 등록된 에이전트 목록                          |
| `get_record` | `record_id: str`                              | 단일 레코드 전체 조회                        |
| `search`     | `mode: 'tag'\|'fts'\|'semantic'`, `query`, `tags?` | 검색 (태그/FTS/시맨틱)                       |

각 도구는 내부적으로 `httpx.AsyncClient` 로 `API_URL` 의 REST 엔드포인트를 호출한다.
실패 시 예외 대신 `{"error": ..., "detail": ...}` 페이로드를 반환하여
LLM이 사람이 읽을 수 있는 메시지로 응답할 수 있게 한다.

## 사용 흐름 예시

사용자가 Cline SR 채팅창에서 다음과 같이 입력했다고 하자.

```
IGA 가이드의 offset 처리 부분에 대해 정리해줘.
```

내부 진행:

1. LLM이 도구 목록(`query_data`, `search`, ...)을 보고 `query_data` 호출 결정.
2. `query_data(agent="iga-analyst", query="offset 처리", limit=5)` 호출.
3. MCP 서버가 `GET /api/data?agent=iga-analyst&query=offset+처리&limit=5` 실행.
4. 매칭된 섹션들이 LLM 컨텍스트에 들어감.
5. LLM이 해당 텍스트를 토대로 자연어 답변 생성.

## 트러블슈팅

| 증상                                                | 원인 / 조치                                                                                              |
|----------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Cline 에서 `Failed to start MCP server`            | `command` 경로(파이썬 실행파일)가 정확한지 확인. PowerShell에서 `& "<경로>" -V` 로 실행 가능 여부 검증. |
| 도구는 보이는데 호출 시 timeout                     | API 서버가 안 떠 있음. `curl http://localhost:8000/health` 확인. `API_TIMEOUT` 늘리기.                  |
| 도구가 비어있음                                     | `mcp_server.server` 가 import 단계에서 실패. `python -m mcp_server` 직접 실행해서 traceback 확인.       |
| `error: http_error, status_code: 404`               | API 라우터 미구현 / 잘못된 record_id. `/docs` 에서 라우트 확인.                                          |
| 한글이 `?` 로 표시됨                                | `PYTHONIOENCODING=utf-8` 설정 확인. Windows 콘솔 `chcp 65001`.                                          |
| Cline 은 잘 되는데 다른 클라이언트에서 안 됨        | 클라이언트별 stdio launch 규약이 다를 수 있음. 필요시 `mcp` SDK 의 SSE 트랜스포트로 전환 검토.            |

## 보안 권고

- `API_URL`은 외부 노출하지 말 것 (기본 `localhost`).
- 도구 `autoApprove`는 read-only 도구만 등록한다 (현재 4개 모두 read-only).
- API 서버에 인증 추가 시 MCP 서버에 `API_TOKEN` 등 환경변수를 전달하고
  `_request` 헬퍼에서 헤더에 실어 보내도록 확장한다.
