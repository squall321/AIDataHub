# Cline SR 운영 권장 사항

사내에서 Cline SR(VS Code 확장) 을 통해 `ai-data-hub` MCP 를 활용할 때
권장되는 운영 / 보안 / 사용 패턴.

---

## 1. API 키 관리 (Secret 처리)

### 1.1 우선순위 (보안 강 → 약)

1. **OS 키체인 / SecretStorage** (가장 권장)
   - VS Code SecretStorage API: `vscode.SecretStorage.store(key, value)`
   - macOS: Keychain / Windows: Credential Manager / Linux: libsecret
   - Cline 확장이 노출하는 secret 등록 UI 활용 (확장 설정 화면).

2. **사용자 환경변수** (보통)
   - `setx API_KEY ...` (Windows) / `export API_KEY=...` (macOS/Linux).
   - settings.json 의 `env` 에 직접 적기보다 안전.

3. **`mcp_settings.json` 에 평문** (지양)
   - 동기화 / git 커밋 / 백업으로 유출 위험.
   - 부득이한 경우 `.gitignore` 강제 + 로컬 권한 600.

### 1.2 권장 .gitignore 추가

```gitignore
# Cline / Claude Desktop secrets
.vscode/cline_mcp_settings.json
**/claude_desktop_config.json
**/.claude/secrets.*
```

### 1.3 키 회전

- 주기적으로 (분기 1회 이상) `POST /api/auth/keys` 로 새 키 발급 후 구 키
  `DELETE /api/auth/keys/{id}`.
- 회전 시 클라이언트 설정 한 곳만 바꾸면 되도록 환경변수 참조 권장:

  ```json
  "env": { "API_KEY": "${env:AI_DATA_HUB_API_KEY}" }
  ```

  (Cline 이 ${env:...} 보간을 지원하는 경우. 미지원 시 SecretStorage 사용.)

---

## 2. Cline 대화에서의 MCP 도구 사용 예시

### 2.1 기본 권장 시스템 프롬프트 추가 (Custom Instructions)

```
당신은 사내 데이터 허브 'ai-data-hub' 에 접근할 수 있다.
원칙:
1) 도구 호출 전, 어떤 도구가 가장 적합한지 한 줄로 추론한다.
2) 데이터 허브 구조를 모르면 먼저 discover_schema() 를 호출한다.
3) 사용자가 record id 를 안 주면 ask() 또는 query_data() 로 먼저 후보를 좁힌다.
4) 결과를 그대로 덤프하지 말고, 사용자가 알고 싶어하는 핵심 + 인용(record id)
   + 다음 가능한 액션 3가지를 제시한다.
5) 한국어 질문은 한국어로, 영어 질문은 영어로 답한다.
```

### 2.2 대화 패턴 1 — 사용자가 사업부 질문

```
User: 우리 사업부 IGA 해석 자료 좀 정리해줘.
Cline: (도구 호출) ask("우리 사업부 IGA 해석 자료", 5)
Cline: (도구 호출) get_record("DOC-...") × top 3
Cline: 사용자 답변 → 핵심 3가지 + record id + "이 중 하나를 더 깊이 볼까요?" 제안.
```

### 2.3 대화 패턴 2 — 사용자가 ID 만 줌

```
User: DOC-HE-CAE-2026-000001 요약해줘.
Cline: get_record("DOC-HE-CAE-2026-000001")
Cline: 본문 요약 + tags + "관련 자료도 볼까요?" → find_related() 후속.
```

---

## 3. 자주 쓰는 프롬프트 템플릿

### 3.1 데이터 탐색

```
이 데이터 허브에 어떤 에이전트들이 있고 각각 무엇을 다루나? 표로 정리해줘.
→ Cline: discover_schema() → agents 표
```

### 3.2 키워드 → 자료

```
"<키워드>" 와 관련된 자료 5개를 찾아 제목/요약/record_id 로 보여줘.
→ Cline: ask("<키워드>", 5)
```

### 3.3 그래프 탐색

```
<RECORD_ID> 의 부모/자식/태그 공유 자료들을 그래프 형태로 펼쳐줘.
→ Cline: find_related("<RECORD_ID>", mode="auto")
```

### 3.4 스키마 학습

```
"<필드명>" 필드의 의미와 허용값을 알려줘.
→ Cline: explain_field("<필드명>")
```

### 3.5 비교

```
record_id A 와 B 의 차이를 표로 보여줘.
→ Cline: get_record(A) + get_record(B) → 표 비교
```

---

## 4. 도메인별 권장 패턴

### 4.1 CAE / 시뮬레이션

- 진입점: `query_data(agent="cae-reporter", query="<해석 키워드>")`
- 보강: `find_related(record_id, mode="graph")` 로 동일 프로젝트 자손 탐색.
- 자주 쓰는 태그: `IGA`, `thermal`, `crash`, `NVH`.

### 4.2 재료 / 배터리

- 진입점: `ask("배터리 셀 열폭주", 10)`
- 보강: `search(mode="tag", tags=["battery", "thermal"])`
- 본문 깊이: `get_record(...)` 후 `sections` 의 `figures` 참조 (그림은 `/figures/...` 정적 마운트).

### 4.3 공정 / 제조

- 진입점: `query_data(agent="process-eng", query="<공정명>")`
- 시계열: 같은 `parent_record_id` 의 children 으로 history 추적.

### 4.4 코드 / 개발 산출물

- 진입점: `query_data(agent="code-reviewer", query="<모듈명>")`
- 본문에 코드 블록이 있으므로 `get_record()` 의 `sections` 그대로 인용 가능.

---

## 5. 주의사항

- `autoApprove` 에 mutating tool (현재 없음) 추가 금지.
- 사내 데이터의 외부 LLM 노출 정책에 따라 LLM 모델 선택 (사내 LLM / Bedrock 등).
- 한국어 인코딩 이슈는 `PYTHONIOENCODING=utf-8` 환경변수로 해결.
- 대화가 길어지면 record 본문이 컨텍스트를 넘는다 — `summary` + `top sections`
  만 전달하도록 시스템 프롬프트에 가이드.

---

## 6. 관련 문서

- `docs/mcp_integration_guide.md` — 등록 / 도구 reference / 시나리오
- `docs/api_reference.md` — REST API 전체 목록
- `docs/observability.md` — 운영 모니터링
- `scripts/mcp_smoke.py` — 9개 도구 검증 스크립트
