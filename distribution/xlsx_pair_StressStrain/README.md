# Excel 베스트 사례 — Stress-Strain Curve (SS400 가정)

## 시연 내용

**Excel 작성 6원칙** (특히 ★ 6번 `_META` + `_GLOSSARY`) 의 실증. SS400 저탄소강 인장시험 가정 데이터 31행을 두 버전으로 비교.

## 파일

| 파일 | 설명 |
|------|------|
| `original.xlsx` | 수정 전 — 헤더 단위 없음, `_META`/`_GLOSSARY` 없음 |
| `rule_compliant.xlsx` | 수정 후 — 헤더 단위 명시(`Strain (-)`, `Stress (MPa)`), Region 컬럼, `_META` 시트(15 키), `_GLOSSARY` 시트(3컬럼 정의) |
| `original.json` | 수정 전 변환 결과 (`DATA-HE-CAE-2026-008001`) |
| `rule_compliant.json` | 수정 후 변환 결과 (`DATA-HE-CAE-2026-008002`) |

## 핵심 메시지

단위 없는 숫자 < 단위 있는 숫자 < **의미 있는 숫자**. `_META` + `_GLOSSARY` 시트가 시험조건·재료·컬럼 의미를 데이터와 함께 묶는다.

## 데이터 (31행)

- Young's modulus E = 200 GPa
- Yield σ_y = 250 MPa @ ε = 0.00125
- UTS σ_u = 450 MPa @ ε = 0.15
- Fracture σ = 410 MPa @ ε = 0.20

`_META.method` = ASTM E8/E8M (가정), `_META.material` = SS400 (저탄소강), `_META.specimen` = 직경 12.5mm GL 50mm.
