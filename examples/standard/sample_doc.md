---
title: KooRemapper IGA 변환 가이드 (표준 예제)
summary: KooRemapper 의 NURBS 기반 IGA 변환 절차 · 옵션 · 검증 방법을 정리한 표준 Markdown 예제이다. 변환 규칙서의 13장(작성 표준) 모든 원칙을 한 파일에 시연한다.
tags: [IGA, NURBS, KooRemapper, sample, standard]
agents: [iga-analyst, cae-reporter, doc-curator]
classification: internal
status: review
domain: cae
language: ko
author: HE/CAE 팀
doc_type: manual
version: 1.0
created: 2026-04-15
modified: 2026-05-08
---

# 1. 개요

이 문서는 KooRemapper 의 IGA(Isogeometric Analysis) 변환 결과를 검토한
표준 Markdown 노트이다. [md_to_json_conversion_rules.md](../../md_to_json_conversion_rules.md)
13장의 작성 원칙(YAML front matter · 번호형 헤딩 · GFM 표 · 인라인 그림 ·
캡션 · 코드 펜스 · 인용)을 한 파일 안에서 모두 시연한다.

## 1.1 배경

기존 FE(Finite Element) 해석은 곡률이 큰 영역에서 메시 의존성이 컸다.
NURBS 기반 IGA 는 형상 표현과 해석 기저를 동일한 NURBS 로 통일하므로,
재메시 없이 정밀도를 끌어올릴 수 있다. 자세한 이론적 배경은
[Hughes 등(2005)](https://example.com/hughes-iga)을 참조한다.

## 1.2 적용 대상

다음 조건에서 IGA 재해석이 권장된다.

- 곡률이 큰 박판/쉘 구조 (필렛, 노치 주변)
- FE 응력 결과의 메시 수렴성이 의심되는 영역
- NURBS 기하 정의가 이미 존재하는 부품 (CAD 원본 활용)

권장하지 않는 경우는 다음과 같다.

1. 단순 직육면체 부품 — FE 로 충분.
2. 비선형 접촉이 지배적인 해석 — 현재 IGA 변환기 미지원.
3. 동적 충돌 시뮬레이션 — 명시 시간 적분 미지원.

---

# 2. 변환 절차

KooRemapper 는 4단계로 동작한다. 각 단계의 입력·출력은 [3절 표 1](#3-표-기준)
에 정리한다.

## 2.1 입력 준비

NURBS 정의를 담은 `.k` 파일과 매핑 대상 FE 결과(`.inp`, `.cdb`)를 준비한다.
경계 조건은 별도 `.json` 매니페스트에 기술한다.

```python
from kooremapper import Remapper, RemapOptions

opts = RemapOptions(
    offset_mm=4.0,
    target_order=3,
    preserve_continuity="C2",
)
remapper = Remapper(opts)
remapper.load_geometry("bracket.k")
remapper.load_field("bracket_fe.inp")
```

펜스에 언어 태그(`python`)를 명시하면 변환기가 `block.marker = "lang:python"`
으로 보존하므로 후속 AI 분석에서 언어 추론 비용이 들지 않는다.

## 2.2 옵션 결정

`offset_mm` 은 NURBS 박스가 FE mesh 를 감싸는 두께이다.
값이 작으면 경계 정확도가 떨어지고, 값이 크면 박스가 인접 부품과 간섭한다.
기본값(4 mm)은 자동차 부품 두께 1.5~3.0 mm 범위에서 검증되었다.

> 주의: `offset_mm` 을 부품 최소 두께의 절반 이하로 두면 NURBS 가
> 두께 방향으로 충분히 펼쳐지지 않아 응력 보간 오차가 커진다.

## 2.3 매핑 실행

매핑은 결정론적이며 동일 입력에 대해 동일 결과를 보장한다.
실행 시간은 노드 수에 거의 선형으로 비례한다 — [4.1 절 성능 표](#4-성능)
참조.

```bash
python -m kooremapper --in bracket.k --field bracket_fe.inp --out bracket.iga
```

## 2.4 결과 검증

결과 `.iga` 파일을 LS-DYNA 또는 Abaqus 로 재해석하여 FE 결과와 비교한다.
검증 기준은 [5절 검증 체크리스트](#5-검증-체크리스트)에 정리한다.

---

# 3. 표 기준

Table 1: KooRemapper 단계별 입력·출력

| 단계 | 입력         | 출력          | 소요 시간(s) | 비고               |
|------|--------------|---------------|--------------|--------------------|
| 1    | `.k`         | NURBS 객체    | 0.5          | 형상 로딩          |
| 2    | NURBS, `.inp`| 매핑 객체     | 1.2          | 옵션 검증 포함     |
| 3    | 매핑 객체    | `.iga`        | 8.7          | 노드 50k 기준      |
| 4    | `.iga`       | 검증 리포트   | 0.3          | 응력 비교 자동화   |

표 캡션은 표의 **위쪽** 한 줄에 `Table N: ...` 형식으로 작성한다.
변환기는 GFM 표에 캡션 문법이 없어 기본 캡션을 `Table N` 으로 채우지만,
직전 단락이 `Table N: ...` 패턴이면 캡션으로 승격한다.

---

# 4. 성능

## 4.1 측정 결과

브라켓 부품(노드 50,000) 기준 실행 시간을 측정하였다.

![NURBS 박스 다이어그램 — FE mesh 를 감싸는 직육면체(offset=4 mm)](nurbs_box.png)

> Figure 1: NURBS 박스 모식도 — FE mesh 의 외곽을 4 mm 오프셋으로 둘러싸고, 박스 내부 좌표를 NURBS 매개변수 (u,v,w) 로 변환한다.

그림 캡션은 그림 **아래쪽**에 `Figure N: ...` 패턴 또는 인용(`> Figure N:`)
형태로 둔다. alt text 자체도 캡션으로 사용되므로 비워두지 않는다.

## 4.2 한계

다음 항목은 현재 변환기가 처리하지 못한다.

- C0 연속만 보장되는 trimmed surface — C2 강제 시 경고 발생
- 비균일 기저(non-uniform knot vector) — 자동 균등화로 처리하나 정밀도 손실
- 다중 패치 간 경계 — 수동 매핑 필요

---

# 5. 검증 체크리스트

운영 전 다음 항목을 모두 통과해야 한다.

- [ ] FE 대비 최대 응력 오차 ≤ 1%
- [ ] 변형 형상 일치 (정성적 시각 확인)
- [ ] 경계 조건 노드 수 보존
- [ ] 질량 총합 보존 (10⁻⁶ 이내)
- [ ] 결과 `.iga` 파일이 LS-DYNA 로 재해석 가능

체크리스트 항목 중 하나라도 실패하면 `output/invalid/` 로 이동하고
검수 큐에 등록한다 — [json_schema_rules.md](../../json_schema_rules.md)
13장과 동일한 정책을 따른다.

---

# 6. 결론

표준 작성 규칙(YAML front matter · 번호형 헤딩 · GFM 표 · 인라인 그림 ·
캡션 · 코드 펜스 · 인용)을 따르면 변환기가 이 문서를 손실 없이 JSON 으로
추출한다. 본 예제의 변환 결과는 `examples/standard/converted/` 에서 확인한다.
