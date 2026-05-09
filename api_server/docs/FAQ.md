# FAQ — 자주 묻는 질문

사업부 사용자 / 운영자가 자주 마주치는 8 가지. 답은 짧게, 추가 문서 링크는 한 줄로.

---

## Q1. 변환 결과가 이상해요. 제목이 본문으로 들어가거나 표가 통째로 사라져요.

**원인은 거의 항상 원본 Word 작성 표준 미준수.** 변환기는 마법이 아니다 — 작성 규칙을 지키면 깨끗한 JSON 이 나오고, 안 지키면 휴리스틱이 추측한다.

### Word 작성 3 원칙

1. **Heading 스타일을 쓴다.** "제목 1 / 제목 2 / 제목 3" 또는 "Heading 1/2/3". 굵게+큰글씨로 보이게만 만들면 변환기는 본문 단락으로 본다.
2. **표는 Word 표 기능으로 만든다.** 그림으로 박힌 표·텍스트박스 표는 추출 불가.
3. **그림에는 캡션을 단다.** "그림 1 — XX" 형태. 캡션 없으면 figure ref 가 깨진다.

### 변환기별 알려진 한계

각 변환기가 못 하는 것은 [`converter_limits.md`](converter_limits.md) 에 한 표로 정리. 자주 걸리는 것:

- **Word**: track changes 가 본문에 섞임 → 제안 검토를 적용/거부 후 저장.
- **Excel**: 셀 수식·차트 데이터·서식 의미는 보존 안 됨 → `_GLOSSARY` 시트 또는 `_META` 시트로 의미 보완.
- **PPT**: 애니메이션·빌드 효과 무시 → 정적으로도 정보가 완전하도록 작성.
- **MD**: HTML 임베드·사용자 정의 확장 무시 → CommonMark + GFM 만 사용.
- **PDF**: 스캔 PDF 는 `--ocr` 옵션 없이 텍스트 추출 불가 + 다단 정확도 떨어짐 → Word→PDF 권장.

---

## Q2. API key 를 분실했어요.

**관리자에게 재발급을 요청한다.** 키는 sha256 해시로 저장되므로 운영자도 원본을 모른다 — 분실 시 재발급이 유일.

요청할 때 알려야 할 것:
- 본인 사번/이름
- 분실 키의 사용 범위 (전체 / 일부 부서)
- (선택) 직전 적재 record_id 한두 개 — 관리자가 본인 key 였는지 확인

발급 후에는 비밀번호 관리자/사내 vault 에 즉시 저장. 채팅·메일에 평문 붙여넣기 금지.

---

## Q3. 용량 초과(413 Payload Too Large)가 떠요.

서버의 `MAX_UPLOAD_MB`(기본 **50 MB**) 를 초과한 경우다. 두 가지 대응:

### A) 파일 자체를 줄이기

- **PPT**: 슬라이드 내 그림 해상도 낮추기 (Office의 "사진 압축" 기능). 보통 절반 이상 줄어듦.
- **PDF**: Acrobat 의 "PDF 최적화" 또는 Word→PDF 재출력 (스캔→Word OCR→PDF 흐름이 가장 깨끗).
- **Excel**: 외부 객체(이미지/임베디드 PPT) 분리 → 첨부로 따로 등록.

### B) 분할 적재

자료를 논리적으로 분할 가능하면 (예: 챕터별 Word) 여러 record 로 나눠 등록 후 `parent_record_id` 또는
`related_record_ids` 로 연결. 분할 시 첫 record 가 parent, 나머지가 child 가 자연스럽다.

운영자가 상한을 늘릴 수도 있다 — 100 MB 이상 일상화면 관리자에게 요청.

---

## Q4. 동일한 파일을 또 올렸는데 status 가 `skipped` 로 떴어요.

**정상 동작이다. 멱등성(idempotency) 보장 메커니즘.**

서버는 적재 시 파일의 `content_hash` (sha256) 를 계산해서 같은 ID + 같은 hash 면 INSERT/UPDATE 를
건너뛴다. 메타만 바뀌었어도 본문이 같으면 skipped 가 정상.

진짜 본문을 바꾼 뒤에도 skipped 가 뜨면: 파일 저장이 안 된 채 업로드된 케이스. 원본 파일을 닫고
다시 저장한 뒤 재업로드.

메타만 갱신하고 싶다면 본문 미세 수정 대신 `PATCH /api/records/{id}` 직접 호출 (관리자 권한 필요).

---

## Q5. PDF 가 OCR 이 안 돼요.

**tesseract 가 별도 설치되어 있어야 OCR 폴백이 동작한다.** 서버 운영자 작업.

확인 방법 (서버에서):

```powershell
tesseract --version   # 5.x 권장. 한글이면 kor 언어팩도 필요
```

설치 (운영자):

- **Windows**: <https://github.com/UB-Mannheim/tesseract/wiki> 인스톨러 → 한글 언어팩 체크.
- **Ubuntu**: `sudo apt-get install tesseract-ocr tesseract-ocr-kor`

설치 후 서버 환경변수 `TESSERACT_CMD=tesseract` (PATH 에 잡히면 생략 가능). 서버 재기동.

사용자 입장에서는 변환기가 자동으로 텍스트 레이어 → OCR 폴백 순으로 시도하므로 별도 옵션 지정 불필요.
다만 **OCR 결과는 일반 텍스트 추출보다 품질이 낮다** — 가능하면 Word→PDF 정상 출력본을 사용한다.

---

## Q6. Excel 의 `_META` 시트가 뭐예요?

**Excel 파일의 의미 메타를 표준화하기 위한 약속된 시트 이름.**

일반 Excel 변환은 셀 값만 추출하고 단위·의미·범위는 잃어버린다. 이를 보완하려고 다음 시트를 추가한다:

- **`_META`** — 시트 단위 의미: 작성 목적, 단위계, 갱신 주기 등.
- **`_GLOSSARY`** — 컬럼별 의미: 컬럼명 → 한국어 풀이 + 단위 + 허용 범위.

### `_META` 시트 예

| key             | value                                  |
|-----------------|----------------------------------------|
| purpose         | IGA 시뮬 결과 정리 (HE-CAE 2026 1Q)    |
| unit_system     | SI                                     |
| update_cycle    | weekly                                 |
| owner           | hong.gildong                           |
| valid_from      | 2026-01-01                             |

### `_GLOSSARY` 시트 예

| column   | meaning              | unit | range      |
|----------|----------------------|------|------------|
| pr       | NURBS 차수 (r 방향)  | -    | 1~3        |
| sigma_y  | 항복 강도            | MPa  | 200~800    |

이 두 시트는 변환 결과의 `meta` 와 `tables[].columns_meta` 로 자동 매핑된다.
자세한 표준은 `excel_to_json_conversion_rules.md` 참고.

---

## Q7. VS Code 확장 패널이 빈 화면이에요.

증상별 5 가지:

| 증상                                     | 원인                                          | 대응                                           |
|------------------------------------------|-----------------------------------------------|------------------------------------------------|
| 패널 자체가 안 열림                      | 확장 미활성화                                 | Extensions 패널에서 enable 확인 → VS Code 재시작 |
| 빈 회색 패널 (Webview 안 뜸)             | Webview 보안 정책 차단                        | VS Code 1.85+ 사용. 사내 보안 정책 점검         |
| "Auth Failed" 빨간 뱃지                  | API URL 오타 / key 만료 / 사내망 미접속       | 설정 다시 입력 → `verify` 재시도               |
| Division/Team 셀렉트박스 비어 있음       | `/api/meta/options` 호출 실패                 | 패널 새로고침. 그래도 비면 서버 상태 확인       |
| Send 시 무한 로딩                        | 대용량 PDF + OCR 처리 중                      | 정상 — 길면 30 초까지 걸린다. 그 이상은 큐 적체  |

VS Code 개발자 도구(`Help → Toggle Developer Tools` → Console 탭) 로 정확한 에러 확인 가능.
스크린샷을 관리자에게 보내면 빠르게 진단된다.

---

## Q8. 내가 올린 record 를 수정하려면?

세 가지 시나리오:

### A) 본문도 바뀐 경우 — 같은 ID 로 재업로드

VS Code 확장에서 **같은 division/team/year/seq 로 새 파일을 보낸다.** content_hash 가 다르면
서버가 자동으로 `status: updated` 로 처리하고, version 이 +1 되며 `parent_record_id` 가
이전 버전을 가리킨다 (lineage chain).

이전 버전과의 차이는 `GET /api/records/{id}/diff?from=v1&to=v2` 로 확인.

### B) 메타만 바뀐 경우 — PATCH

```
PATCH /api/records/{id}
Content-Type: application/json
{
  "tags": ["IGA", "NURBS", "추가태그"],
  "summary": "수정된 요약"
}
```

본문(content) 은 PATCH 로 안 바뀐다 — 본문 변경은 반드시 재업로드.

### C) 잘못 올려서 지우고 싶은 경우 — Soft delete vs Hard delete

- **Soft delete (권장)**: `DELETE /api/records/{id}` 기본. `deleted_at` + `deleted_by` 만 셋되고
  일반 조회에서 제외된다. 실수 복구 가능.
- **Hard delete (제한적)**: `DELETE /api/records/{id}?hard=true`. 별도 admin 권한 필요. 복구 불가.
  audit_log 에는 자국이 남는다.

운영 정책상 사용자는 보통 soft delete 만 가능. 실제 디스크 정리는 정기 작업으로 운영자가 수행.

---

## 더 읽어볼 것

- [`user_guide_for_engineers.md`](user_guide_for_engineers.md) — 첫 업로드까지 5 단계 walkthrough.
- [`converter_limits.md`](converter_limits.md) — 변환기별 알려진 한계 표.
- [`api_reference.md`](api_reference.md) — 모든 엔드포인트 파라미터·응답·curl 예시.
- [`governance.md`](governance.md) — audit_log, soft delete, lineage, diff 상세.
