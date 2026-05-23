# Wave-5 업로드 예제 모음

Wave-5 의 "CLI 도구 → MCP tool 자동 등록" 파이프라인을 실제로 어떻게 쓰는지 보여주는
예제 + 정의 파일 모음.

## 디렉토리

| 경로 | 용도 |
|---|---|
| `MANIFEST-SPEC.md` | 매니페스트 표준 + LLM 이 사용자 도와 채우는 흐름 정의. 모든 업로더가 1회 읽을 것. |
| `stress_strain_plot/` | Python (matplotlib) 도구 — 완성된 업로드 zip 의 모범. |
| (예정) `node_csv_parser/` | Node 도구 예 |
| (예정) `dotnet_simple_calc/` | .NET self-contained 예 |
| (예정) `win32_legacy_exe/` | Wine 경유 Win32 binary 예 |

## 업로더가 알아야 할 4가지 (Wave-5 본질)

1. **소스 그대로 올려도 됨** (Python / Node / Java / .NET) — 빌드 불필요.
2. **매니페스트는 LLM 이 도와줌** — `MANIFEST-SPEC.md` 의 7-step 대화 흐름을 그대로 사용.
3. **컨테이너·sif·apt 는 서버가 알아서** — 업로더는 zip 1개만.
4. **결과 자동 적재 (`persist_output`)** 가 wave-5 의 sweet spot — 도구 출력이 다음 검색의 근거.

## 가장 간단한 워크플로 (1분 요약)

```
[로컬]
  1) 도구 소스 작성 (예: tool.py)
  2) MANIFEST-SPEC.md 보고 manifest.yaml 작성 — LLM 에게 작성 도움 요청 가능
  3) samples/ 안에 호출 예제 JSON 1~2개
  4) requirements.txt (Python 일 때)

[업로드]
  curl -X POST http://AIDH:8001/api/mcp_tools/upload \
       -F "bundle=@tool.zip" \
       -F 'metadata={"uploader":"me@co.com"}'
  → { job_id: "..." }

[등록 완료]
  Claude Desktop / Cursor → 자연어 호출 → 도구 실행 → records 자동 적재
```

## LLM 에 매니페스트 채움 의뢰 시 권장 프롬프트

```
다음 도구를 AI Data Hub wave-5 의 MCP tool 로 업로드하려고 합니다.
MANIFEST-SPEC.md 의 7-step 흐름으로 manifest.yaml 을 채워주세요.

[도구 소스]
{여기에 tool.py 내용 복사}

[추가 정보]
- 용도: {한 문장}
- 결과 형식: {JSON / PNG 파일 / 표 / ...}
- 자동 적재: {ON/OFF, data_type}
```

LLM 이 7개 질문 → manifest.yaml 초안 → 사용자 확인 → 업로드.

## 더 자세한 안내

- 전체 로드맵: `docs/01-plan/MASTER-PLAN.md`
- Wave-5 4-phase 상세: `docs/01-plan/wave-5-binary-mcp.md`
- 매니페스트 스키마: `MANIFEST-SPEC.md`
- 완성된 예제: `stress_strain_plot/README.md`
