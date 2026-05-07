# AI Data Hub Uploader (VS Code Extension)

`AI_data/api_server` 를 백엔드로 사용하여, 사업부 문서/데이터를 **VS Code 안에서 드래그&드롭만으로 적재**할 수 있게 해주는 확장.

> **상태**: P3~P5 (Welcome / DropZone / Form / Upload / Result 화면 모두 동작). 다음은 `.vsix` 패키징 + e2e 테스트.

## 빌드 / 실행

```powershell
npm install
npx tsc -p .            # → out/
# F5 (VS Code Extension Development Host) 또는
npx @vscode/vsce package  # .vsix 패키징
```

활성화 시 connected=false 면 새 탭에 Welcome 자동 오픈. URL/API Key 입력 후 Save & Continue → DropZone → 파일 드롭 → 폼 → Send.

## 목표

- 비개발자/현장 사용자도 CLI 없이 파일을 AI Data Hub 에 적재.
- 백엔드 IP/API Key 만 한 번 설정하면, 이후는 파일을 끌어다 놓고 메타데이터를 채운 뒤 **Send** 버튼 한 번으로 끝.

## UX 한 줄 요약

```
[VS Code 시작] → 새 탭(Webview)에 설정 화면 → IP/API Key 입력 →
[연결 OK] → 드롭존 화면 → 파일 드롭 → 메타데이터 폼 → [Send] →
백엔드 /api/convert/ingest 호출 → 결과 토스트
```

## 폴더 구조 (계획)

```
vscode_extension/
├── README.md                 ← 이 파일
├── docs/
│   ├── PLAN.md               ← 통합 기획서 (메인)
│   ├── ux_flow.md            ← 화면 흐름 + 와이어프레임
│   ├── metadata_spec.md      ← 메타데이터 폼 양식 정의 (단일 진실)
│   └── architecture.md       ← 모듈 구조 / 통신 / 보안
└── (구현 시 추가)
    ├── package.json
    ├── tsconfig.json
    ├── src/
    │   ├── extension.ts
    │   ├── webview/
    │   │   ├── settings.html
    │   │   ├── upload.html
    │   │   └── app.tsx
    │   ├── client/
    │   │   └── apiClient.ts
    │   └── state/
    │       └── secretStore.ts
    └── media/
```

## 백엔드 변경 요청

확장이 필요로 하는 신규/보강 API 는 다음 문서로 별도 정리되어 있습니다 (백엔드 개발 에이전트가 처리):

- `AI_data/api_server/docs/extension_integration_plan.md`

## 메타데이터 핵심 (요약)

확장이 입력받는 메타데이터는 **확장자별로 자동 결정되는 필수항목**과 **공통 분류 항목** 으로 나뉩니다. 자세한 내용은 [`docs/metadata_spec.md`](docs/metadata_spec.md) 참고.

| 그룹           | 필드                                                 | 비고                                  |
|----------------|------------------------------------------------------|---------------------------------------|
| 조직/식별      | `division`, `team`, `year`, `seq`                    | 백엔드 `Record.id` 생성에 사용        |
| 분류           | `classification`, `status`, `domain`, `language`     | 거버넌스                              |
| 검색/연결      | `tags`, `agents`, `subject_keywords`                 | RAG 매칭                              |
| 첨부 자체      | `title` (override), `summary` (override)             | 변환기 자동 추출 결과 위에 덮어씀     |
| 데이터 품질    | `quality_score`, `valid_from`, `valid_until`         | 선택                                  |
