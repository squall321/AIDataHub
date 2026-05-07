# Backend Changes Required (mirror)

> ⚠️ **이 문서는 미러본입니다.** 정본(canonical) 위치는 백엔드 레포의
> `api_server/docs/extension_integration_plan.md` 입니다. 정본이 갱신되면 본 사본도 동기화하세요.
>
> 본 사본은 확장 개발자가 백엔드 변경 진척을 별도 레포 클론 없이 확인할 수 있게 두는 편의용입니다.

요약 (전체 내용은 정본 참고):

1. **CORS** — `vscode-webview://*` 오리진 허용.
2. **`GET /api/meta/options`** — divisions/teams/agents/classifications/statuses/languages/extensions/max_upload_mb 카탈로그.
3. **`POST /api/auth/keys/verify`** — 부트스트랩 키 없이 일반 키 유효성만 검증.
4. **`/api/convert/ingest` 폼 확장** — `status`, `language`, `subject_keywords`, `derivation`, `quality_score`, `valid_from`, `valid_until`, `title_override`, `summary_override`.
5. **에러 envelope 통일** — `{ error: { code, message, request_id } }`.
6. **`GET /health`** 응답에 `version` / `auth_required` 추가.

상세 응답 스키마, 구현 위치, 수용 기준은 정본을 참고하세요.
