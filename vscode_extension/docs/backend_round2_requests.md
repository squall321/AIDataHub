# Backend Round 2 — Follow-up requests from extension implementation

> 라운드 1 (`backend_changes_required.md` / `api_server/docs/extension_integration_plan.md`) 은 머지 완료 가정. 본 문서는 **확장 구현을 진행하면서 발견된 갭** 만 누적 기록한다. 백엔드 에이전트는 이 항목들을 라운드 1 PR 머지 이후 별도 PR 로 처리.
>
> 모든 항목은 **Why (구현에서 막힌 지점)** + **Proposal (서버 계약 변경)** + **Impact on extension** 3-line 형식으로 기록.

---

## R2-01. (P0) `/api/auth/keys/verify` — `auth_required=false` 일 때도 200 허용

**Why.** 확장의 [Test Connection] 흐름은 `health.auth_required` 가 false 면 API Key 입력을 건너뛸 수 있어야 한다. 그런데 verify 라우트가 `require_api_key` 의존성을 무조건 사용하면, 인증 비활성 서버에서 키 미제공 호출이 401 을 받는다.

**Proposal.**
```python
# routes/auth.py — verify_key
if not settings.auth_required:
    return {"ok": True, "key_name": None, "agent_scopes": []}
return {"ok": True, "key_name": principal.name, "agent_scopes": list(principal.agent_scopes or [])}
```
또는 dependency 를 `Depends(get_optional_principal)` 로 바꿔 None 허용.

**Impact on extension.** 인증 비활성 서버에서 빈 키로 [Save & Continue] 가능 — 현재 fallback 로직 단순화.

---

## R2-02. (P0) `GET /api/meta/options` 에 누락된 필드: `seq_policy`, `tag_suggestions`

**Why.** 폼은 `seq` 를 사용자가 직접 입력하지만, 사업부별로 "다음 seq 자동 부여" 정책 vs "수동" 정책이 갈린다. 또 tag 입력 시 자유 입력만 허용하면 오타로 검색 매칭률이 떨어진다.

**Proposal.** options 응답에 두 키 추가:
```jsonc
{
  "seq_policy": "manual" | "auto",     // 기본 manual; auto 면 폼에서 seq 입력란 숨김
  "tag_suggestions": ["iga", "ls-dyna", "battery", "crash", ...]   // 분포가 큰 상위 N (예: top 50)
}
```
구현: `tag_suggestions` 는 `SELECT unnest(tags) tag, COUNT(*) c FROM records GROUP BY 1 ORDER BY c DESC LIMIT 50` 한 번 (응답에 5분 캐시 그대로 적용).

**Impact on extension.** `seq_policy=auto` 면 form 에서 seq 입력 자체를 숨기고 백엔드가 채움. tag 칩 컨트롤이 자동완성 제안을 띄울 수 있음.

→ 동시에 ingest 도 `seq=Form(0)` 또는 미지정 시 `(data_type, division, team, year)` 의 `MAX(seq)+1` 자동 부여 (라운드 1 §6.3 — 이번에 함께 처리 권장).

---

## R2-03. (P1) `/api/convert/ingest` 응답에 분류 필드 echo 포함

**Why.** 현재 ingest 응답의 `record` dict 는 `id/data_type/title/summary/tags/agents/division/team/year/seq/source_file/content_hash` 까지만 노출한다. 확장 결과 화면이 "이 파일은 status=draft, classification=internal 로 적재되었습니다" 를 보여주려면 이 필드들이 응답에 있어야 한다.

**Proposal.** `routes/convert.py` 의 응답 `record` dict 에 다음 추가:
```python
"classification": record.classification,
"status":          record.status,
"domain":          record.domain,
"language":        record.language,
"derivation":      record.derivation,
"quality_score":   record.quality_score,
"valid_from":      record.valid_from.isoformat() if record.valid_from else None,
"valid_until":     record.valid_until.isoformat() if record.valid_until else None,
"subject_keywords": list(record.subject_keywords or []),
```

**Impact on extension.** Result 화면에 더 풍부한 영수증 표시 가능. 디버깅 시 "내가 보낸 값과 서버에 들어간 값이 일치하는지" 한눈에 확인.

---

## R2-04. (P1) CORS preflight: `X-API-Key` 가 `Access-Control-Allow-Headers` 에 포함되었는지 자동검증 테스트

**Why.** 라운드 1 §1 에서 헤더에 `X-API-Key` 를 추가했지만, regression 으로 떨어지면 webview 의 모든 인증 호출이 사전요청에서 차단된다.

**Proposal.** `tests/test_cors.py` 추가:
```python
async def test_options_preflight_allows_x_api_key(client):
    res = await client.options(
        "/api/meta/options",
        headers={
            "Origin": "vscode-webview://abc",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key, Content-Type",
        },
    )
    assert res.status_code in (200, 204)
    allow = res.headers.get("access-control-allow-headers", "").lower()
    assert "x-api-key" in allow
    assert res.headers.get("access-control-allow-origin") == "vscode-webview://abc"
```

**Impact on extension.** 백엔드 회귀 시 즉시 빨간불.

---

## R2-05. (P1) ingest 응답에 `record_url` 힌트 추가

**Why.** 결과 화면의 [Open in Browser] 버튼이 `http://{baseUrl}/api/records/{id}` 를 열지만, 향후 백엔드가 별도 viewer (Next.js admin 등) 를 가지면 클라이언트가 또 패치되어야 한다.

**Proposal.** ingest 응답에 선택적으로:
```jsonc
{
  "record_id": "DOC-HE-CAE-2026-000001",
  "record_url": "http://10.10.20.5:8000/api/records/DOC-HE-CAE-2026-000001",
  ...
}
```
환경변수 `RECORD_VIEWER_BASE_URL` 이 설정되어 있으면 그 베이스로, 아니면 `request.base_url` 로 조립.

**Impact on extension.** 개봉 URL 결정 책임이 백엔드로. 클라이언트는 단순 `vscode.env.openExternal(record_url)`.

---

## R2-06. (P2) 업로드 진행률을 위한 `Content-Length` 보장

**Why.** webview 의 `XMLHttpRequest.upload.onprogress` 는 `lengthComputable=true` 여야 게이지가 움직인다. multipart/form-data 자체는 항상 length-computable 이지만, 백엔드가 chunked-only proxy 뒤에 있으면 깨질 수 있다.

**Proposal.** docs 만 업데이트: "ingress 프록시(nginx/traefik) 는 `proxy_request_buffering on` 권장; chunked-only 모드는 진행률을 깨뜨림". 코드 변경 없음 — 운영 가이드 갱신.

**Impact on extension.** 이미 `lengthComputable` 체크를 하고 있어 코드는 안전하지만, 운영 환경에서 게이지가 안 움직이면 이 문서를 가리키면 됨.

---

## R2-07. (P2) ingest 의 `tags` / `agents` / `subject_keywords` — 공백/대소문자 정규화 정책 명시

**Why.** 클라이언트에서 trim 만 하고 case 는 그대로 전송한다. 백엔드가 어떻게 정규화하는지 응답에 안 보여서 사용자 입력 중복(`IGA` vs `iga`) 시 어느 쪽이 살아남는지 불투명.

**Proposal.** 정책 결정:
- 옵션 A: 백엔드에서 소문자로 통일 + dedup.
- 옵션 B: 그대로 보존.

문서로 명시. 옵션 A 면 `db_writer` 에서 적용 + 응답 `record.tags` 가 정규화된 형태로 echo (R2-03 과 결합).

**Impact on extension.** 클라이언트가 dedup 에 동일 규칙을 사용해야 사용자 혼란 없음.

---

## 처리 우선순위 요약

| #     | Pri | 라운드 1 의존 |
|-------|-----|----------------|
| R2-01 | P0  | §1·§3          |
| R2-02 | P0  | §2 + §6.3      |
| R2-03 | P1  | §2·§4          |
| R2-04 | P1  | §1             |
| R2-05 | P1  | §4             |
| R2-06 | P2  | (운영)         |
| R2-07 | P2  | §4             |

본 문서가 갱신될 때마다 미러 (`vscode_extension/docs/backend_changes_required.md`) 와 정본 (`api_server/docs/extension_integration_plan.md`) 의 §"Round 2 follow-ups" 섹션을 함께 업데이트한다.
