# PPT 베스트 사례 — AI/디지털트윈 (수동 라벨링 11장 발췌)

## 시연 내용

**PPT 작성 표준 + 본문 라벨링 4원칙** 의 실증. 같은 11장 PPT 의 본문을 직접 손으로 4원칙(H2 번호 / Claim→Evidence / Figure N. 캡션 / 산문→표) 으로 재구성한 결과.

## 폴더 구조

```text
ppt_pair_AI_DigitalTwin/
├── ppt예제.pptx                          ← 수정 전 PPT
├── ppt수정예제.pptx                       ← 수정 후 PPT
├── ppt예제.json                          ← 수정 전 변환 결과 (doc_id = DOC-HE-CAE-2026-0000009011)
├── ppt수정예제.json                       ← 수정 후 변환 결과 (doc_id = DOC-HE-CAE-2026-0000009012)
├── DOC-HE-CAE-2026-0000009011/               ← ppt예제.json 의 attachments 가 가리키는 폴더
│   ├── F001.png ~ F006.png              (figure 6장 — 슬라이드 본문 그림)
│   ├── A001.png ~ A006.png              (attachment kind=figure 사본)
│   └── ... (총 12 파일, 2.6 MB)
├── DOC-HE-CAE-2026-0000009012/               ← ppt수정예제.json 의 attachments 가 가리키는 폴더
│   └── ... (동일한 그림 사본 12 파일)
└── README.md (이 파일)
```

## JSON 의 path 가 어떻게 참조하는가

`ppt예제.json` 안:

```json
"figures": [
  {
    "id": "DOC-HE-CAE-2026-0000009011-F001",
    "image_path": "DOC-HE-CAE-2026-0000009011/F001.png"   ← 이 폴더 기준 상대 경로
  }
],
"attachments": [
  {
    "id": "DOC-HE-CAE-2026-0000009011-A001",
    "kind": "figure",
    "file_path": "DOC-HE-CAE-2026-0000009011/A001.png"
  }
]
```

→ 이 README 와 같은 위치에서 상대 경로로 그대로 열린다 (`./DOC-HE-CAE-2026-0000009011/F001.png`).
→ DB 적재 시에는 `/attachments/{file_path}` 정적 마운트를 통해 동일 path 로 접근.

## 파일

| 파일 | 설명 |
|------|------|
| `ppt예제.pptx` | 수정 전 — 단순 슬라이드 11장 |
| `ppt수정예제.pptx` | 수정 후 — 본문 첫 줄에 `1.1`/`1.2` H2 번호, Claim→Evidence 들여쓰기, `Figure N.` 캡션, 산문 → 표 |
| `ppt예제.json` | 수정 전 변환 결과 |
| `ppt수정예제.json` | 수정 후 변환 결과 (변환기의 본문 H2 휴리스틱이 sub-section 으로 승격) |
| `DOC-*-009011/` | 수정 전 PPT 에서 추출된 그림/첨부 12 파일 |
| `DOC-*-009012/` | 수정 후 PPT 에서 추출된 그림/첨부 12 파일 |

## 핵심 메시지

본문 4원칙만 손봐도 변환기가 H1 → H2 → H3 깊이 트리, Claim→Evidence 들여쓰기, 캡션, 표를 자동 추출.

**작은 모델이 sections 트리만 봐도 의미 구조 즉시 인식.**
