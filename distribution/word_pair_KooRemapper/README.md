# Word 베스트 사례 — KooRemapper Manual

## 시연 내용

**Word 작성 표준** 적용 전후 변환 결과 비교. 본문/표/그림은 동일, `core_properties` 4 필드 + 본문 마커 5종(`[DOC_TYPE]/[SUMMARY]/[TAGS]/[AGENT_SCOPE]/[SOURCES]`) 만 추가.

## 파일

| 파일 | 설명 |
|------|------|
| `original.docx` | 수정 전 — 양식 미적용 docx (Heading 스타일은 풍부, 메타 비어 있음) |
| `rule_compliant.docx` | 수정 후 — `core_properties` 채움 + 마커 5종 본문 첫머리에 삽입 |
| `original.json` | 수정 전 변환 결과 (`HE-CAE-2026-0000007001`) |
| `rule_compliant.json` | 수정 후 변환 결과 (`HE-CAE-2026-0000007002`) |

## 메타 비교

| 항목 | 원본 변환 | 룰-적합 변환 |
|------|-----------|--------------|
| title | `'KooRemapper_Manual'` | `'KooRemapper Manual'` |
| author | `'python-docx'` | `'박국진'` |
| summary[:60] | `''` | `'KooRemapper 의 NURBS 기반 IGA 변환 절차·옵션·검증 방법을 정리한 사용자 매뉴얼.'` |
| tags | 0개 | 6개 |
| sections (top/total) | 44 / 306 | 44 / 306 |
| tables | 44 | 44 |
| warnings | 0 | 0 |

## 핵심 메시지

본문 0byte 변경. 메타 4축만 채워도 작은 모델이 검색·라우팅 가능 상태로 전환.
