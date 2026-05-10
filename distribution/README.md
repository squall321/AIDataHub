# AI Data Hub — 배포 패키지

이 폴더에는 **사업부 문서 → AI-친화 JSON 변환 시스템**의 모든 핵심 자료가 들어 있다.

## 구성

| 파일/폴더 | 용도 |
|-----------|------|
| [`ai_data_strategy_deck.html`](./ai_data_strategy_deck.html) | 메인 발표자료 (25 슬라이드). **브라우저로 열기**. 문제 → 전략 → 작성 표준 (이론 + 실증) → JSON / DB / 변환기 / 7대 원칙 / 실증. |
| [`META_FORMAT_AUDIT.md`](./META_FORMAT_AUDIT.md) | 공용 메타데이터 포맷 감사 보고서 (P0 8건 / P1 8건 / P2 5건 식별). |
| [`SERVER_SETUP_GUIDE.md`](./SERVER_SETUP_GUIDE.md) | **서버 셋업 가이드 (Windows)** — `deploy\SERVER_QUICK_SETUP.bat` 더블클릭 + PG 비번 입력만으로 완료. 4단계 / 1입력 / 약 10~15분. |
| [`CLIENT_SETUP_GUIDE.md`](./CLIENT_SETUP_GUIDE.md) | **클라이언트 셋업 가이드** — config.ini 작성 + setup.bat 더블클릭 + 일상 명령(ask/search/get/ingest). AI 에이전트 통합 패턴 포함. |
| [`deploy/`](./deploy/) | 서버 셋업 스크립트 묶음 — Windows (`SERVER_QUICK_SETUP.bat`, `install_*windows.ps1`, vendor 사전 빌드 binary), Linux (`install_*linux.sh` — apt/dnf + source build fallback), Docker (`install.sh` + docker-compose). [`deploy/README_LINUX.md`](./deploy/README_LINUX.md) 별도. |
| [`client_setup/`](./client_setup/) | 클라이언트 셋업 키트 (CLI) — 6개 .bat (ask/search/get/ingest/related/show_guide) + lib/ PowerShell 헬퍼. AI 에이전트·자동화·분석가 친화. |
| [`vscode_extension/`](./vscode_extension/) | **VS Code Extension (GUI)** — `ai-data-hub-uploader-0.5.0.vsix` (32.9 KB) + USER_GUIDE.md. 비개발자 친화 5-step Webview (DropZone → Form → Send). 비개발 사업부 엔지니어가 자료 적재할 때. |
| [`api_server/`](./api_server/) | API 서버 본체 — FastAPI + SQLAlchemy + 6 변환기 + MCP + 30+ 라우터. `setup.bat` / `run.bat` / `ingest.bat` 포함. **`/dashboard` 정적 SPA** (5탭: 상태·카탈로그·검색·그룹·가이드) 도 함께 포함되어 별도 프론트 서버 불필요. |
| [`word_pair_KooRemapper/`](./word_pair_KooRemapper/) | Word 베스트 사례 — `core_properties` + 본문 마커 5종 적용 전후 비교. |
| [`ppt_pair_AI_DigitalTwin/`](./ppt_pair_AI_DigitalTwin/) | PPT 베스트 사례 — 본문 4원칙(H2 번호/Claim-Evidence/Figure N./표) 수동 라벨링 적용 전후 비교. |
| [`xlsx_pair_StressStrain/`](./xlsx_pair_StressStrain/) | Excel 베스트 사례 — `_META` + `_GLOSSARY` 시트 적용 전후 비교 (SS400 stress-strain 가정 데이터). |

## 페어 폴더 공통 구조

```text
NN_pair_*/
├── original.{docx|pptx|xlsx}        ← 수정 전 (양식 미적용)
├── rule_compliant.{docx|pptx|xlsx}  ← 수정 후 (작성 표준 적용)
├── original.json                    ← 수정 전 변환 결과
├── rule_compliant.json              ← 수정 후 변환 결과
├── DOC-* / DATA-* (옵션)            ← JSON 의 image_path/file_path 가 가리키는 자원 폴더
│                                      (그림·차트 추출 시 변환기가 자동 생성;
│                                       Word/Excel 페어는 figures=0 이라 없음)
└── README.md                        ← 페어 설명 + 메타 비교 + 폴더 구조
```

**자원 참조 규칙**: JSON 의 `figures[i].image_path` 와 `attachments[i].file_path` 는 모두 페어 폴더 기준 **POSIX 상대 경로**(`{doc_id}/F{nnn}.{ext}`). 페어 폴더에서 그대로 열린다. DB 적재 후에는 `/attachments/{file_path}` 정적 마운트로 같은 경로 사용.

## 빠른 시작

1. `ai_data_strategy_deck.html` 을 브라우저(Chrome/Edge)로 연다 — 전체 흐름 25 슬라이드.
2. 각 페어 폴더의 `README.md` 를 읽으며 변환 결과 차이를 확인.
3. JSON 파일을 텍스트 에디터로 열어 `meta` / `sections` / `figures` / `tables` 구조를 직접 본다.

## 운영 진입 흐름

| 역할 | 단계 | 진입점 |
|------|------|--------|
| **서버 운영자** (Windows) | 4단계 셋업 | [`SERVER_SETUP_GUIDE.md`](./SERVER_SETUP_GUIDE.md) → `deploy\SERVER_QUICK_SETUP.bat` |
| **서버 운영자** (Linux) | 1명령 셋업 | [`deploy/README_LINUX.md`](./deploy/README_LINUX.md) → `bash install_postgres_linux.sh` |
| **AI agent / 분석가** (CLI) | config + setup | [`CLIENT_SETUP_GUIDE.md`](./CLIENT_SETUP_GUIDE.md) → `client_setup\setup.bat` |
| **사업부 엔지니어** (GUI) | vsix 설치 | `vscode_extension\ai-data-hub-uploader-0.5.0.vsix` (자세한 사용법 [`USER_GUIDE.md`](./vscode_extension/USER_GUIDE.md)) |
| **운영자 모니터링** (브라우저) | 서버 기동 후 | `http://localhost:8000/dashboard/` (5탭: 상태/카탈로그/검색/그룹/가이드) |

## 핵심 메시지

> **데이터 구조의 질이 작은 AI 모델의 운영 가능성을 결정.**
> 본문 0byte 변경 + 메타 양식만 손봐도 RAG-친화 자산으로 전환.
