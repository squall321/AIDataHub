# mcp_scripts/ — Shell script → MCP tool 동적 등록

부팅 시 본 디렉토리의 ``*.mcp.yaml`` 매니페스트가 FastMCP 에 자동 등록된다.
각 매니페스트가 1개의 MCP tool 이 되며, LLM 클라이언트(Claude Desktop / Cline /
Cursor 등) 가 ``tools/list`` 로 발견 가능.

## 매니페스트 스키마

```yaml
name: my_tool                          # 영문 snake_case (tool name)
title: "표시용 한글 제목"              # 선택
description: "한 줄 설명. LLM 이 언제 호출할지 판단."
script: ./my_tool.sh                   # 매니페스트와 같은 dir 기준 상대경로
args:
  - {name: input_path, type: string, required: true, description: "..."}
  - {name: count, type: integer, default: 1, description: "..."}
  - {name: verbose, type: boolean, default: false}
timeout_sec: 60                        # 1~600 (default 30)
args_style: long_flags                 # long_flags(default) | positional
env_allowlist:                         # 자식 프로세스에 통과시킬 env (PATH 는 항상 포함)
  - LANG
  - LC_ALL
return:
  format: text                         # text(default) | json (JSON 이면 stdout 을 parse)
```

## 보안 게이트 (자동)

매 호출마다 적용:

1. 스크립트 절대경로가 이 디렉토리 prefix 인지 검증 (escape 차단)
2. 심볼릭링크 거부
3. ``shell=False`` + 인자는 항상 리스트 (메타문자 무력화)
4. ``env_allowlist`` 외 환경변수 미상속
5. ``timeout_sec`` 강제 (kill on overrun)
6. 전역 동시실행 제한 (env ``AIDH_MCP_SCRIPTS_CONCURRENCY``, default 3)

## 비활성

env ``AIDH_MCP_SCRIPTS=off`` 로 전체 비활성.
env ``AIDH_MCP_SCRIPTS_DIR=/path`` 로 디렉토리 override.

## args_style

- **long_flags** (default): ``./script.sh --input-path /a/b --verbose --count 3``
  - 불리언 true → 플래그만 추가, false → 생략
  - 언더스코어는 dash 로 변환 (``input_path`` → ``--input-path``)
- **positional**: ``./script.sh /a/b 3 true`` (선언 순서대로)

## 반환

모든 호출은 다음 dict 를 반환:

```json
{
  "ok": true,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "parsed": { ... }   // return.format=json 이고 stdout 가 valid JSON 일 때만
}
```

타임아웃 시 ``ok=false, timeout=true``.

## 예제

`echo_args.sh` + `echo_args.mcp.yaml` 참조. tool name ``echo_args`` 가 MCP 에
등록되어 인자를 stdout 으로 echo.
