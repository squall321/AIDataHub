# Wave-5 매니페스트 스펙 — LLM 친화 정의

이 문서는 LLM (Claude / GPT / Cline 등) 이 사용자의 "내 CLI 도구를 MCP 로 만들고 싶다" 요청을 받아 매니페스트를 **대신 채워주기 위한** 표준 입력 정의다. 사람 개발자도 그대로 읽고 작성 가능.

---

## LLM 에이전트가 사용자에게 물어볼 순서 (대화 흐름)

LLM 은 다음 7개 질문을 순서대로 던져서 매니페스트를 완성한다. 사용자가 모르는 항목은 추론·기본값 사용.

| # | LLM 의 질문 | 추론 가능? | 매핑되는 필드 |
|---|---|---|---|
| 1 | 도구 이름 (영문 snake_case)? | 파일명에서 추론 | `name` |
| 2 | 한 문장 설명 (이 도구가 무엇을 함)? | 코드 docstring 으로 추론 | `description` |
| 3 | 실행파일/스크립트 경로 (zip 안 상대경로)? | 단일 파일이면 자동 | `script` |
| 4 | 어떤 runtime? (Python 3.12 / Node 20 / JVM 17 / .NET 8 / Linux ELF / Win32) | 확장자 + magic bytes 로 자동, 버전 미지정 시 default 사용 | `runtime` |
| 5 | 입력 인자 — 각 (이름, 타입, 필수여부, 기본값, 한 줄 설명) | argparse / commander / picocli 파싱으로 추론 | `args[]` |
| 6 | 결과 형식? (text / json) | stdout 마지막 줄이 JSON 이면 자동 json | `return.format` |
| 7 | 결과를 허브 records 에 자동 저장할지? (Y/N + data_type) | 기본 N, 사용자 명시 시만 활성 | `persist_output` |

---

## 매니페스트 JSON Schema (요약)

```yaml
# 필수 (required)
name: string                 # snake_case, ^[a-z][a-z0-9_]{2,40}$
description: string          # 사람·LLM 이 도구 용도 판단 — 정확하고 짧게
script: string               # zip 내부 상대경로 (예: "tool.py", "bin/app", "App.jar")

# runtime 분류 (필수)
runtime: python | node | jar | dotnet | binary | wine
# 각 runtime 별 부가 필드 (선택)
python_version: "3.12"       # python 일 때
node_version: "20"           # node 일 때
jdk_version: "17"            # jar 일 때
target_framework: "net8.0"   # dotnet 일 때

# 인자 명세 (LLM 이 가장 신경 써야 하는 부분)
args:
  - name: snake_case           # required, ^[a-z][a-z0-9_]{0,30}$
    type: string|integer|number|boolean
    required: bool             # default false
    default: <any>             # required=false 면 권장
    description: string        # LLM 이 사용자 입력 검증할 때 참고

# 실행 정책
timeout_sec: int               # 1..1800, default 60
args_style: long_flags | positional  # default long_flags
env_allowlist: [string]        # PATH 는 항상 통과, 그 외 명시
return:
  format: text | json          # default text

# 컨테이너 격리 (default 가장 엄격, 매니페스트로 완화)
platform_capability:
  net: bool                    # default false (--net=none)
  gpu: bool                    # default false
resource_limits:
  cpu_percent: int             # default 200
  ram_gib: int                 # default 2
  disk_gib: int                # default 1

# 결과 자동 적재 (wave-5 의 매력 포인트)
persist_output:
  enabled: bool                # default false
  data_type: DOC|DATA|SIM|CAD|LOG|FORM|OTHER  # 기존 enum 만
  team: string                 # HE / ...
  group: string                # CAE / ...
  title_template: string       # placeholder: {tool_name}, {timestamp}, {args.X}, {parsed.Y}
  tags: [string]               # records.tags 에 추가
  summary_template: string     # 선택
  body_template: string        # 선택 (DOC 적재 시 sections 생성용)
  dedup_key: string            # 선택 — 같은 key 의 호출은 신규 record 대신 updated_at 갱신
                               #   예: "{args.material_name}_{args.e_modulus}"

# Agent ↔ Tool 노출 제한 (default: 모든 agent 노출)
restrict_agents: [string]      # 선택 — 지정된 agent_type 만 호출 가능
require_agent_tag: [string]    # 선택 — agent.common_tags 가 모든 태그 포함 시만 노출

# LLM 라우팅 보조 — recommend_agents 가 자연어→도구 매칭 시 활용
llm_hints:
  when_to_use: string          # "사용자가 ... 요청할 때" — 도구 호출 트리거 패턴
  not_for: string              # "X 는 다루지 않음" — 잘못된 매칭 방지
  example_calls:               # 자연어→인자 매핑 학습 예
    - natural_language: string
      args: {key: value}
  output_description: string   # 결과 형식 설명 (LLM 이 인용할 때 도움)
```

---

## LLM 채움 규칙 (중요)

1. **추론 가능한 필드는 기본값으로 채우고 사용자에게 확인** — 매번 다 물어보지 말 것.
2. **`args` 추론**:
   - Python `argparse` 코드 정적 파싱 (필요 시 `--help` 실행으로 보강 — 폐쇄망에선 정적 파싱만)
   - Node `commander`, `yargs`, Java `picocli`, .NET `System.CommandLine` 패턴 인식
   - 추론 결과는 `description` 자동 생성 (코드 주석 또는 도움말 문자열에서)
3. **`runtime` 자동 결정**:
   - `.py` 파일 + shebang `#!/usr/bin/env python` → `runtime: python`
   - `.js` + `package.json` → `node`
   - `.jar` → `jar`
   - PE32 + .NET metadata → `dotnet`
   - PE32 + 일반 → `wine`
   - ELF + dynamic → `binary` (자동 ldd 분석)
4. **`persist_output` 권장 시점**:
   - 도구가 분석 결과를 생성 (plot, 표, 보고서 등) → 권장 ON
   - 도구가 단순 조회 (echo, ls 같은) → 권장 OFF
5. **`timeout_sec` 권장**: 도구 종류에 따라 자동 — plot 30s, 데이터 분석 120s, 큰 시뮬레이션 600s.

---

## 좋은 매니페스트 vs 나쁜 매니페스트

### 좋은 예 (LLM 이 채워야 할 모범)

```yaml
name: stress_strain_plot
description: "재료의 탄성계수 E, 항복 응력, 최대 변형률을 받아 단순 elastic-plastic stress-strain 곡선을 PNG 로 그린다."
script: stress_strain_plot.py
runtime: python
python_version: "3.12"   # default — 미지정 시 서버 default 적용
args:
  - {name: material_name, type: string, required: true, description: "재료 이름 (예: SUS304)"}
  - {name: e_modulus, type: number, required: true, description: "탄성계수 (GPa)"}
  - {name: yield_stress, type: number, required: true, description: "항복 응력 (MPa)"}
  - {name: ultimate_strain, type: number, default: 0.20, description: "최대 변형률 (0~1)"}
return:
  format: json
timeout_sec: 30
persist_output:
  enabled: true
  data_type: SIM
  team: HE
  group: CAE
  title_template: "Stress-Strain: {args.material_name} (E={args.e_modulus}GPa)"
  tags: [stress-strain, material, plot]
```

### 나쁜 예 (LLM 이 자주 빠지는 함정)

```yaml
name: stress-strain-plot        # 안 됨 — dash 사용, snake_case 위반
description: "그래프"            # 안 됨 — 너무 짧음, LLM 라우팅 의미 못 잡음
script: ./tool                  # 안 됨 — 경로 모호, 확장자 + 디렉토리 명확히
args:
  - {name: M, type: string}     # 안 됨 — 단일 문자 이름, description 없음
return:
  format: json                  # 안 됨 — 도구가 실제로는 text 반환, 거짓 정보
```

### GUI 코드 거절 (자동 정적 분석)

| 코드 패턴 | 동작 | 권장 대안 |
|---|---|---|
| `import tkinter` / `PyQt5/6` / `PySide2/6` / `wxPython` / `kivy` | 자동 거절 (`RUNTIME_GUI_REQUIRED`) | argparse 기반 CLI 로 리팩토링 |
| `input(`, `getpass.getpass`, `click.prompt` | 자동 거절 (`INTERACTIVE_INPUT_FORBIDDEN`) | 모든 입력을 argv 로 받기 |
| `webbrowser.open`, `os.startfile`, `xdg-open` | 자동 거절 (`EXTERNAL_OPEN_FORBIDDEN`) | 파일 경로만 반환, 열기는 caller 책임 |
| `plt.show()` (matplotlib) | 경고 + 자동 보정 (`MPLBACKEND=Agg` 강제) | `matplotlib.use("Agg")` + `savefig()` 사용 |
| 자동화 도구 (selenium 헤드리스 안 됨, PyAutoGUI 등) | opt-in 가능 — `platform_capability.virtual_display: true` | xvfb-run 자동 wrap |

**좋은 패턴 (stress_strain_plot 표준)**:
```python
import argparse, matplotlib
matplotlib.use("Agg")            # 헤드리스 필수
import matplotlib.pyplot as plt
p = argparse.ArgumentParser(); p.add_argument("--out", required=True)
args = p.parse_args()
fig, ax = plt.subplots(); ax.plot([1,2,3], [1,4,9])
fig.savefig(args.out)            # show() 아닌 savefig
plt.close(fig)
```

---

## LLM 이 사용자에게 보낼 최종 확인 메시지 (템플릿)

```
다음과 같이 매니페스트를 작성했습니다. 확인 후 "OK" 또는 수정 사항을 알려주세요.

도구 이름: {name}
용도: {description}
실행 방식: {runtime} (예: Python 3.11 스크립트)

입력 인자:
{for arg in args}
  - {arg.name} ({arg.type}{arg.required?'*':''}): {arg.description}
{endfor}

자동 저장: {persist_output.enabled ? 'ON ({persist_output.data_type})' : 'OFF'}
실행 시간 한도: {timeout_sec}s

문제 없으면 zip 만들어 업로드합니다.
```

---

## 자주 묻는 질문 (FAQ — LLM 이 사용자에게 답할 때)

| Q | A |
|---|---|
| 윈도우즈에서 만든 .exe 도 됨? | 됨. `runtime: wine` 또는 .NET 이면 `runtime: dotnet`. 빌드는 서버가 알아서. |
| Apptainer 가 뭐임? | 컨테이너 형식. 사용자는 몰라도 됨. 서버가 알아서 만든다. |
| 인자 이름에 dash (-) 써도 됨? | 아니. snake_case 만. argv 로 전달 시 자동으로 `--arg-name` 으로 변환됨. |
| 결과 PNG 같은 바이너리는? | `/work/` 안에 저장하면 서버가 캡쳐해서 반환. `out_path` 같은 인자로 받아라. |
| 호출당 시간이 분 단위면? | `timeout_sec: 600` 까지 OK. 그 이상은 비동기 job 으로 분리 권장 (별 wave). |
| 외부 네트워크 필요? | `platform_capability.net: true`. 단 보안 감사 대상 — 정말 필요할 때만. |
