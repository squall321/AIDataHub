# Stress-Strain Plot — Wave-5 업로드 예제

## 1. 무엇을 하는 도구인가

재료의 탄성계수 (E, GPa) · 항복 응력 (σy, MPa) · 최대 변형률 (ε_u, 0~1) 을 받아
단순 bilinear hardening elastic-plastic stress-strain 곡선을 PNG 로 그린다.

stdout 에 JSON 한 줄 반환:
```json
{
  "out_path": "/work/sus304.png",
  "material": "SUS304",
  "yield_strain": 0.001075,
  "max_stress_mpa": 322.5,
  "ultimate_strain": 0.4
}
```

## 2. 파일 구성 (업로드 zip 내용)

```
stress_strain_plot/
├── stress_strain_plot.py        # Python 스크립트 (argparse 기반)
├── manifest.yaml                # wave-5 매니페스트
├── requirements.txt             # matplotlib, numpy
└── samples/                     # smoke run 케이스 3종
    ├── case_sus304.json         # SUS304 (정상)
    ├── case_al6061.json         # Al6061-T6 (default ultimate_strain 사용)
    └── case_invalid_strain.json # 음성 케이스 — exit 2 검증
```

## 3. 로컬 테스트 (서버 업로드 전 확인)

```bash
# Python 3.12 venv (서버 default 동일)
python3.12 -m venv /tmp/aidh-venv && source /tmp/aidh-venv/bin/activate
pip install -r requirements.txt
mkdir -p /tmp/work
python stress_strain_plot.py \
    --material-name "SUS304" \
    --e-modulus 200 \
    --yield-stress 215 \
    --ultimate-strain 0.40 \
    --out-path /tmp/work/sus304.png
# 정상이면 마지막 줄에 JSON, /tmp/work/sus304.png 생성
```

## 4. 서버 업로드

### 방법 A — `aidh-package.exe` (윈도우즈 CLI 헬퍼, Phase 3)

```cmd
> aidh-package.exe stress_strain_plot --upload --base-url http://AIDH_SERVER:8001
```

### 방법 B — Dashboard UI (Phase 2)

http://AIDH_SERVER:8001/dashboard/ → "Tool Upload" 탭 → 디렉토리 드래그.

### 방법 C — `curl` 로 직접 (Phase 1 만 있어도 가능)

```bash
# 1) zip 생성
cd examples/wave-5/stress_strain_plot
zip -r /tmp/stress_strain_plot.zip .

# 2) POST
curl -s -X POST http://AIDH_SERVER:8001/api/mcp_tools/upload \
  -F "bundle=@/tmp/stress_strain_plot.zip" \
  -F 'metadata={"uploader":"alice@example.com"}'
# → 202 { "job_id": "...", "status": "queued" }

# 3) 진행 상황 폴링
curl -s "http://AIDH_SERVER:8001/api/mcp_tools/jobs/<job_id>"
# → { "status": "building" | "smoke_running" | "registered" | "failed", ... }
```

## 5. 등록 완료 후 사용 (MCP 클라이언트에서)

Claude Desktop / Cursor / Continue 등 어디서나 자연어로:

```
사용자: SUS304 의 stress-strain 곡선 그려줘. yield 215 MPa, ultimate 40%.

LLM (MCP 도구 호출):
  recommend_agents(q="SUS304 stress-strain 곡선") → 'aidatahub' agent + 'stress_strain_plot' tool
  stress_strain_plot(material_name="SUS304", e_modulus=200, yield_stress=215, ultimate_strain=0.4)
  → { out_path: "/work/sus304.png", yield_strain: 0.001075, ... }
  
LLM 답변: "SUS304 의 stress-strain 곡선을 생성했습니다. 
  yield strain = 0.107%, max stress = 322.5 MPa.
  결과: SIM-HE-CAE-2026-0000000XYZ (records 자동 적재)
  PNG: /attachments/<id>/plot.png"
```

`persist_output.enabled=true` 이므로:
- records 테이블에 **SIM 타입** 행 자동 INSERT
- title: "Stress-Strain — SUS304 (E=200GPa, σy=215MPa)"
- tags: stress-strain, material, plot, bilinear-hardening
- embedding 생성 → 다음 검색에서 자연스럽게 재발견

## 6. 재호출 / 회귀

같은 zip 재업로드 (sha 동일):
- 서버: cache hit, 무 빌드, `last_used_at` 갱신, 즉시 응답

수정한 zip 재업로드 (sha 다름):
- 서버: version+1 (예: v1 → v2), 새 sif 빌드, FastMCP 의 tool 최신 버전으로 swap
- 이전 v1 은 archive 보존 (rollback 가능)

## 7. 흔한 문제 + 진단

| 현상 | 원인 | 해결 |
|---|---|---|
| smoke step 에서 `module not found: matplotlib` | requirements.txt 누락 또는 버전 미스 | requirements.txt 확인, pip 캐시 정합 |
| `ultimate_strain must be greater than yield strain` (exit 2) | 사용자 입력 모순 (예: σy 너무 큰데 ε_u 너무 작음) | 입력 재검토 — 정상 동작이지만 사용자 알림 필요 |
| smoke 모두 통과하는데 등록은 실패 | 매니페스트 `name` 충돌 (이미 등록된 도구) | 다른 이름 사용 또는 새 sha 로 version bump 의도면 OK |
| PNG 가 비어있음 | `out_path` 가 컨테이너 `/work/` 외부 | `/work/` 또는 default 사용 권장 |

## 8. 학습 포인트

이 예제가 보여주는 wave-5 의 매력:

1. **윈도우즈 개발자 친화** — Python 소스 그대로 OK, 빌드 불필요. matplotlib·numpy 도 폐쇄망 mirror 면 자동 설치됨.
2. **자동 컨테이너화** — 사용자는 sif/def 미언급. 서버가 python:3.11 base + pip install -r requirements 자동.
3. **격리 강제** — `--net=none --readonly --writable-tmpfs --no-home`. 도구가 외부 호출 시도해도 차단.
4. **결과 적재** — `persist_output` 으로 생성된 plot 이 SIM 레코드가 되어 다음 시맨틱 검색의 근거.
5. **LLM 친화 매니페스트** — args 의 description 이 LLM 이 사용자 의도 → 인자 매핑하는 핵심 단서.
