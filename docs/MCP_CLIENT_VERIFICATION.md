# MCP 클라이언트별 검증 체크리스트

각 AI 클라이언트에서 AI Data Hub MCP 서버가 실제로 동작하는지 사람이
직접 확인하는 절차. 서버/프로토콜 자체는 검증됨 (initialize 핸드셰이크,
원격 Host, 11개 도구). 여기서 보는 건 **클라이언트 호환성**이다.

- MCP endpoint: `http://<HOST_IP>:8001/mcp/` (끝 슬래시 포함)
- 자동설치: Extension **Console 탭** → agent 선택 → 클라이언트 선택 →
  "1,2,3 불러오기" → "자동 설치 (MCP + Prompt)"
- 수동설치: 아래 클라이언트별 config 직접 작성

> 공통 사전 확인 (서버 쪽, 1회):
> ```
> curl -s -o /dev/null -w '%{http_code}\n' http://<HOST_IP>:8001/api/system/health   # 200
> curl -s -X POST http://<HOST_IP>:8001/mcp/ \
>   -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
>   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | grep -c '"name"'   # 11
> ```
> 둘 다 통과해야 클라이언트 검증 의미 있음. 외부 PC 에서 접속 시 방화벽
> (bootstrap.sh 가 ufw 개방 / 클라우드는 보안그룹) 먼저 확인.

공통 합격 기준 (모든 클라이언트 동일):
1. 클라이언트 도구 목록에 `aidatahub` 11개 도구가 보인다
2. "이 허브에 레코드 몇 건 있어?" → 모델이 `discover` 호출 → 실제 숫자 답
3. agent 질의 → `get_agent_session` → `agent_search` 호출 → `(source: <record_id> §<sec>)` 인용
4. 자료 없는 질문 → refusal_message 로 거부 (환각 안 함)

---

## 1. Cline (VSCode 확장)

- **MCP transport**: HTTP (Cline 3.x+ 지원)
- **config**: `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
  (mac: `~/Library/Application Support/...`, win: `%APPDATA%\Code\User\globalStorage\...`)
  → `{"mcpServers":{"aidatahub":{"url":"http://<HOST_IP>:8001/mcp/"}}}`
- **system_prompt**: VSCode 설정 `cline.customInstructions` (자동설치가 marker 블록으로 주입)

체크:
- [ ] 자동설치 후 Cline 패널 → MCP Servers 에 `aidatahub` 표시 (초록/connected)
- [ ] 도구 일람 11개 노출
- [ ] "AI Data Hub 에 레코드 몇 건?" → discover 호출 → 숫자 답
- [ ] agent 관련 질문 → agent_search 호출 + record_id 인용
- [ ] Cline 설정에서 `cline.customInstructions` 에 `<!-- aidatahub:system-prompt:* -->` 블록 존재
- 실패 시: Cline 패널의 MCP 탭 새로고침 / VSCode 재시작 / URL 끝 `/` 확인

## 2. Claude Desktop

- **MCP transport**: HTTP 직접 지원은 버전 의존적. 미지원 빌드면
  `mcp-remote` 브리지 필요할 수 있음 (stdio→HTTP).
- **config**: `claude_desktop_config.json`
  (mac: `~/Library/Application Support/Claude/`, linux: `~/.config/Claude/`,
  win: `%APPDATA%\Claude\`) → `mcpServers.aidatahub.url`
- **system_prompt**: 자동 주입 경로 없음 → Claude Desktop → 프로젝트 →
  Instructions 에 **수동 복사** (Console 탭에서 프롬프트 복사)

체크:
- [ ] config 저장 후 Claude Desktop **완전 종료 후 재시작**
- [ ] 입력창 도구(🔌/슬라이더) 아이콘에 `aidatahub` 도구 표시
- [ ] "레코드 몇 건?" → discover 호출 → 숫자 답
- [ ] Project Instructions 에 system_prompt 붙여넣었는지 → 페르소나대로 답/인용
- 실패 시: HTTP transport 미지원 빌드일 수 있음 → `npx mcp-remote http://<HOST_IP>:8001/mcp/`
  를 command 형 stdio 서버로 등록 후 재시도

## 3. Claude Code (CLI)

- **MCP transport**: HTTP 네이티브 지원
- **등록**: 자동설치가 `claude mcp add aidatahub --transport http http://<HOST_IP>:8001/mcp/` 실행
  (수동도 동일 명령)
- **system_prompt**: workspace `CLAUDE.md` (없으면 `~/.claude/CLAUDE.md`) 에 marker 블록 주입

체크:
- [ ] `claude mcp list` → `aidatahub` 표시
- [ ] 새 `claude` 세션 시작 (기존 세션은 미반영)
- [ ] "/mcp" 또는 도구 목록에서 11개 확인
- [ ] "레코드 몇 건?" → discover → 숫자
- [ ] agent 질의 → agent_search + 인용
- [ ] `CLAUDE.md` 에 `<!-- aidatahub:system-prompt:* -->` 블록
- 실패 시: `claude mcp remove aidatahub` 후 재등록 / 새 세션 / URL 끝 `/`

## 4. Cursor

- **MCP transport**: HTTP 지원 (Settings → MCP)
- **config**: `~/.cursor/mcp.json` → `mcpServers.aidatahub.url`
- **system_prompt**: workspace `.cursorrules` (없으면 `~/.cursorrules`) marker 블록

체크:
- [ ] 자동설치 후 Cursor 재시작 또는 Settings → MCP 새로고침
- [ ] Settings → MCP 에 `aidatahub` enabled + 도구 11개
- [ ] Composer/Chat 에서 "레코드 몇 건?" → discover → 숫자
- [ ] agent 질의 → agent_search + 인용
- [ ] `.cursorrules` 에 marker 블록
- 실패 시: Settings → MCP → aidatahub 토글 off/on / Cursor 재시작

## 5. VSCode Copilot Chat

- **MCP transport**: `chat.mcp.servers` (Copilot Chat MCP 지원 버전 필요)
- **config**: workspace `.vscode/settings.json` 또는 User settings →
  `chat.mcp.servers.aidatahub.url`
- **system_prompt**: `github.copilot.chat.codeGeneration.instructions`
  배열에 `[aidatahub:<agent>]` 접두 entry

체크:
- [ ] VSCode 재시작
- [ ] Copilot Chat 도구/툴 패널에 `aidatahub` 도구 노출
- [ ] Agent 모드에서 "레코드 몇 건?" → discover 호출 → 숫자
- [ ] codeGeneration.instructions 에 `[aidatahub:...]` entry 존재
- 실패 시: Copilot 확장 버전 확인 (MCP 지원), VSCode 재시작, settings 범위(Workspace/User) 확인

## 6. Gemini CLI

- **MCP transport**: HTTP (Gemini CLI 0.x+)
- **config**: `~/.gemini/mcp.json` → `mcpServers.aidatahub.url`
  (또는 `gemini config mcp add aidatahub <url>`)
- **system_prompt**: 표준 저장 위치 없음 → 세션 시작 시 **수동 복사**

체크:
- [ ] config 저장 후 Gemini CLI 재시작
- [ ] 도구 목록에 `aidatahub` 11개
- [ ] "레코드 몇 건?" → discover → 숫자
- [ ] system_prompt 수동 주입했는지 → 페르소나/인용
- 실패 시: `gemini config mcp list` 확인 / URL 끝 `/` / 재시작

## 7. Codex CLI (OpenAI)

- **MCP transport**: `~/.codex/config.toml` 의 `[mcp_servers.aidatahub]`
  (Codex 의 MCP HTTP 지원 버전 필요 — 미지원 빌드면 stdio 브리지 필요)
- **config**: `~/.codex/config.toml`
  ```toml
  [mcp_servers.aidatahub]
  url = "http://<HOST_IP>:8001/mcp/"
  ```
- **system_prompt**: workspace `AGENTS.md` (없으면 `~/.codex/AGENTS.md`) marker 블록

체크:
- [ ] config.toml 저장 후 Codex CLI 재시작 (새 세션)
- [ ] 도구 목록에 `aidatahub`
- [ ] "레코드 몇 건?" → discover → 숫자
- [ ] `AGENTS.md` 에 marker 블록 → 페르소나/인용
- 실패 시: Codex 버전의 MCP HTTP 지원 여부 확인 / `[mcp_servers.aidatahub]`
  블록 문법 / 새 세션

---

## 공통 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| 도구가 안 보임 | 클라이언트 재시작/새 세션 (대부분 런타임 재발견 안 함). URL 끝 `/` 확인. |
| 연결 timeout | 방화벽 — `sudo ufw allow 8001/tcp` (bootstrap.sh 가 처리) / 클라우드 보안그룹 |
| connection refused | 서버 미기동 — `bash status.sh` / `bash diag.sh --tail-logs` |
| "Not Acceptable" | 클라이언트가 `Accept: application/json, text/event-stream` 미전송 — 클라이언트 MCP 구현/버전 문제 |
| HTTP transport 미지원 | Claude Desktop/Codex 구버전 → `npx mcp-remote <url>` stdio 브리지로 우회 |
| 도구는 되는데 페르소나 안 먹음 | system_prompt 미주입 — 해당 클라이언트의 룰 파일/Instructions 확인 (수동 클라이언트는 직접 복사) |
| 답변에 인용 없음 | system_prompt 의 인용 규약 미적용 — get_agent_session 결과를 세션 규칙으로 채택했는지 |

## 검증 우선순위 (시간 없으면)

1. **Claude Code / Cline** — HTTP MCP 네이티브, 가장 확실. 여기서 먼저 통과시킬 것.
2. Cursor / Gemini — HTTP 지원, config 단순.
3. Claude Desktop / Codex — HTTP transport 버전 의존. 미지원 시 mcp-remote 브리지.
4. Copilot — MCP 지원 버전 + Agent 모드 필요.
