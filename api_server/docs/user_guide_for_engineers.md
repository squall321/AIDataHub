# 사업부 엔지니어를 위한 사용자 가이드

> 대상 독자: 사업부 엔지니어 — 자기가 만든 보고서/시험 데이터/PPT 를 사내 AI 허브에 직접 올리려는 사람.
> 백엔드 운영자/AI 에이전트 개발자가 아니다. CLI/Python 지식 없이 VS Code 만으로 끝낸다.

이 문서는 첫 업로드까지 5 단계로 안내한다. 모든 작업은 본인 노트북의 VS Code 안에서 일어난다.

---

## 1단계 — 관리자에게 API key 발급 받기

먼저 사내 AI 허브 관리자(IT 또는 데이터 운영 담당자)에게 다음 두 가지를 요청한다:

1. **API 서버 URL** — 보통 `http://<사내 IP>:8000` 형태. 예: `http://10.20.30.40:8000`.
2. **X-API-Key** — 본인용 키 1개 (사람당 1개 권장, 분실 시 재발급).

키는 한 번 발급되면 같은 값으로 다시 못 본다. 발급받은 즉시 비밀번호 관리자(또는 사내 vault)에 저장한다.
키는 비밀번호와 동일하게 취급한다 — 채팅/메일에 평문으로 붙여넣지 않는다.

키가 유효한지는 다음 한 줄로 확인 가능 (관리자가 도와줄 수 있다):

```powershell
curl -X POST "http://10.20.30.40:8000/api/auth/keys/verify" -H "X-API-Key: <발급받은 키>"
```

응답 `200 OK` + `{"valid": true, ...}` 면 정상.

---

## 2단계 — VS Code 확장 설치

1. **사내 공유 폴더/사내망**에서 `ai-data-uploader-x.y.z.vsix` 파일을 받는다 (관리자가 제공).
2. VS Code 실행 → 좌측 사이드바 **Extensions** 아이콘 클릭.
3. Extensions 패널 우상단의 `…` (More Actions) → **Install from VSIX...** 선택.
4. 받은 `.vsix` 파일을 선택. 잠시 후 "Successfully installed" 토스트.
5. VS Code 명령 팔레트(`Ctrl+Shift+P`) → `AI Data: Open Uploader` 실행 → 패널 표시 확인.

마켓플레이스에서는 받지 않는다 — 사내 전용이다.

---

## 3단계 — 확장 설정 (1회)

확장 패널 좌상단의 **톱니바퀴** 아이콘 → **Settings** 진입:

| 설정             | 값                                            |
|------------------|-----------------------------------------------|
| API URL          | `http://10.20.30.40:8000` (관리자가 알려준 값)|
| X-API-Key        | (1단계에서 발급받은 키)                       |
| Default Team | 본인 부서 (예: `HE`)                          |
| Default Group     | 본인 팀 (예: `CAE`)                           |

저장하면 자동으로 `POST /api/auth/keys/verify` 가 호출되어 키 유효성을 확인한다.
좌하단에 녹색 **Connected** 뱃지가 뜨면 준비 완료.

빨간 **Auth Failed** 가 뜨면: API URL 오타, 키 만료, 사내망 미접속 중 하나. 7번 트러블슈팅 참고.

---

## 4단계 — 첫 파일 업로드 walkthrough

이 walkthrough 는 IGA 보고서(`iga_summary.docx`) 1 건을 올린다고 가정한다.

### Step 1 — 파일을 끌어다 놓기

확장 패널의 큰 점선 박스(**DropZone**)에 `iga_summary.docx` 를 드래그·드롭.
또는 박스 안의 **Browse...** 버튼 → 파일 선택.

지원 확장자: `.docx`, `.xlsx`, `.pptx`, `.md`, `.markdown`, `.pdf`.
크기 상한은 서버 설정 `MAX_UPLOAD_MB` (기본 50 MB).

### Step 2 — 메타 폼 채우기

파일이 추가되면 그 아래에 메타 폼이 펼쳐진다. 필수와 선택을 구분해서 입력한다.

**필수 필드**:

| 필드     | 의미                          | 예시         |
|----------|-------------------------------|--------------|
| Team | 팀 코드                       | `HE`         |
| Group     | 그룹 코드                     | `CAE`        |
| Year     | 적재 연도                     | `2026`       |
| Seq      | 순번 (비우면 auto-seq 자동)   | (비움)       |

**권장 필드** (검색·분류에 핵심):

| 필드             | 의미                                   | 예시                              |
|------------------|----------------------------------------|-----------------------------------|
| Tags             | 콤마 구분 키워드                       | `IGA, LS-DYNA, NURBS`             |
| Agents           | 이 record 를 사용할 에이전트 종류      | `iga-analyst, cae-reporter`       |
| Status           | 자료 상태                              | `approved` / `draft`              |
| Language         | 언어                                   | `ko` / `en` / `mixed`             |
| Subject Keywords | 본문 외 추가 검색어                    | `등기하해석, 트림드볼륨`           |
| Quality Score    | 0~100 자기평가                         | `80`                              |

**선택 필드** (override):

- **Title Override** — 비우면 변환기가 첫 H1 또는 파일명에서 자동 추출. 어색하면 수동 지정.
- **Summary Override** — 비우면 자동 요약. 정확한 한 줄을 직접 쓰는 게 가장 좋다.

### Step 3 — Send

폼 우하단의 **Send** 버튼 클릭. 진행률 바가 표시되고 보통 1~3 초 안에 끝난다 (PDF OCR 은 더 걸릴 수 있다).

내부적으로 일어나는 일:
1. 멀티파트 업로드 (`POST /api/convert/ingest`)
2. 서버가 변환기를 돌려 `schema_v1` JSON 생성
3. RecordIn 정규화 + DB INSERT/UPDATE
4. 응답에 `record_id` + `status` 반환

### Step 4 — 결과 확인

응답 패널에 다음 형태로 표시:

```
✓ DOC-HE-CAE-2026-0000000001  (created)
  title: KooRemapper IGA 변환 매뉴얼
  capabilities: [sections, blocks, tables, figures]
  warnings: 0
```

- `status: created` — 신규 등록.
- `status: updated` — 같은 ID 가 이미 있어서 갱신.
- `status: skipped` — 같은 content_hash 가 이미 있어서 멱등 무시 (4번 트러블슈팅 참고).

`record_id` 는 메모해두면 나중에 검색·삭제·diff 때 유용하다.

---

## 5단계 — 메타 입력 팁

좋은 메타가 곧 좋은 검색 결과다. 5 가지 원칙:

1. **Tags 는 5~8 개**. 너무 적으면 안 잡히고, 20 개 이상이면 노이즈. 동의어는 한 개로 통일.
2. **Agents 는 본인 자료를 누가 써야 하는지 기준으로 고른다.** 모르겠으면 관리자가 운영하는 5 종 표준 에이전트(`iga-analyst`, `cae-reporter`, `material-reviewer`, `process-checker`, `code-assistant`) 중 가장 가까운 것 1~2 개.
3. **Summary Override 는 한 문장으로**. "무엇을, 누가, 왜, 결과는" 중 핵심 2~3 개. 100 자 이내.
4. **Status = approved 는 신중히**. 사내 정식 검토를 마친 자료에만. 작업 중인 건 `draft`.
5. **Quality Score 는 자기검열**. 표/그림 캡션 누락, 표준 양식 미준수면 낮춘다 (50~70).

---

## 6단계 — 내 데이터를 다른 사람이 어떻게 찾는가

올린 자료는 다음 경로로 다른 사람·AI 가 찾는다:

### Agent scope 검색 (Cline SR 핵심 경로)

```
GET /api/data?agent=iga-analyst&query=NURBS+차수&limit=5
```

→ Tags + Title + Summary + Agents 매칭으로 relevance 스코어 계산. 본인이 입력한 `agents` 와
`tags` 가 그대로 반영된다.

### 자연어 질의

```
POST /api/ask  {"query": "IGA 차수 어떻게 정해?"}
```

→ 서버가 자연어를 `interpreted_query` 로 번역 → 매칭. 본인이 입력한 `query_examples` 가 있으면
이 단계에서 가산점이 붙는다 (없으면 자동 생성).

### 태그 / 부서 / 연도 필터

```
GET /api/records?tag=IGA&team=HE&year=2026
```

### 관련 자료 traversal

본인 자료의 `related_record_ids` 를 채워두면, 다른 자료가 본인 자료를 detail 조회 후
"관련 자료" 로 자동 추적한다 (`find_related` MCP 도구).

요약: **검색에 잡히고 싶으면 tags · agents · summary 를 의미있게 채운다.**

---

## 7단계 — 흔한 실수 / 트러블슈팅 5 종

### 7.1 "Send 후 Auth Failed (401)"

원인: API key 만료 또는 오타.
대응: 설정에서 키 다시 입력. 그래도 안 되면 관리자에게 재발급 요청.

### 7.2 "변환 결과가 이상하다 (제목·표가 깨짐)"

원인: Word 작성 표준 미준수 (Heading 스타일 미적용, 표가 이미지로 박힘 등).
대응: `word_to_json_conversion_rules.md` 의 3 원칙 확인 후 원본 수정 → 재업로드.
같은 자료를 다시 올리면 `status: updated` 로 덮어써진다.

### 7.3 "용량 초과 (413 Payload Too Large)"

원인: 서버 `MAX_UPLOAD_MB` 초과 (기본 50 MB).
대응: PDF·PPT 의 그림 해상도 낮추기, 첨부 분리. 아니면 관리자에게 상한 조정 요청.

### 7.4 "같은 파일을 다시 올렸는데 status: skipped"

원인: content_hash 가 동일하면 멱등 처리됨 (의도된 동작).
대응: 정상이다. 진짜 변경했는데도 skipped 면 저장이 안 됐다는 뜻 — 파일 다시 확인.

### 7.5 "메타 폼에 Team/Group 옵션이 안 뜬다"

원인: `/api/meta/options` 호출 실패 (네트워크 또는 서버 미기동).
대응: 패널 우상단 새로고침 버튼 → 다시 시도. 그래도 안 되면 관리자에게 서버 상태 확인 요청.

---

## 부록 — 자주 쓰는 명령

| 하고 싶은 일                  | 방법                                                     |
|-------------------------------|----------------------------------------------------------|
| 내가 올린 record 다시 보기    | `GET /api/records/{id}` (또는 확장 패널 History 탭)      |
| 내가 올린 record 수정         | 같은 파일 수정 후 같은 메타로 재업로드 → `updated`       |
| 잘못 올린 record 삭제         | 관리자에게 요청 (soft delete 권장, hard delete 별도 권한)|
| 내가 올린 자료 통계           | `GET /api/analytics/distribution?team=HE&group=CAE`   |

---

## 더 읽어볼 것

- [`FAQ.md`](FAQ.md) — 자주 묻는 8 가지.
- [`converter_limits.md`](converter_limits.md) — 변환기별 알려진 한계.
- `vscode_extension/docs/USER_GUIDE.md` — 확장 자체의 더 자세한 매뉴얼.
- 본 프로젝트 루트의 `word_to_json_conversion_rules.md`, `excel_to_json_conversion_rules.md`,
  `ppt_to_json_conversion_rules.md`, `pdf_to_json_conversion_rules.md`,
  `md_to_json_conversion_rules.md` — 작성 표준.
