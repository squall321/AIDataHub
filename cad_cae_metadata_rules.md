# CAD / CAE 메타데이터 규칙 (v1.0)

> CAD(MCAD/ECAD)·CAE(솔버 덱/결과) 데이터를 AIDataHub 에 수집하기 위한 정의·컨벤션.
> 스키마 근거: [`schemas/cad.py`](./api_server/src/api/schemas/cad.py) ·
> [`schemas/sim.py`](./api_server/src/api/schemas/sim.py) ·
> [`schemas/eng_meta.py`](./api_server/src/api/schemas/eng_meta.py) ·
> [`schemas/attachment.py`](./api_server/src/api/schemas/attachment.py)
>
> 작성일: 2026-07-19 · 형식 버전: 1.0 · DB 마이그레이션 불필요(순수 additive)

---

## 0. 30초 요약

```text
CAD 레코드 (data_type=CAD, 1 레코드 = 1 설계 데이터셋)
├─ content: CADContent { cad_type, file_format(원본), derived_formats[], components[], eng_meta, bom }
└─ 첨부 kind="cad": 원본(ODB++/Parasolid) + 파생(ECAD-JSON/STEP), extra.format_role 로 구분

SIM 레코드 (data_type=SIM, 1 레코드 = 1 해석 job)
├─ content: SimContent { solver, inputs, outputs, runtime, eng_meta, bom }
└─ 첨부 kind="cae": 솔버 입출력 덱/결과, extra 에 solver/role/unit_system

공용 eng_meta: 과제코드 + 개발단계 리비전(dv1…pra) + 설계안 + DOE + 모델 rev
BOM 연계: components[].bom_code (부품 단위) + bom.codes (레코드 요약)
```

---

## 1. 정의 — cad 와 cae 의 경계

| | `cad` (첨부 kind) | `cae` (첨부 kind) |
|---|---|---|
| 담는 것 | **형상(geometry)** 데이터 | **솔버가 소비/생산**하는 해석 덱·결과 |
| MCAD | Parasolid(x_t/x_b), CATPart, STEP, IGES, STL | — |
| ECAD | ODB++(원본), ECAD-JSON(파생) | — |
| 해석 | — | LS-DYNA(k/key/dyn/dynain/d3plot), Abaqus(inp/odb), ANSYS(cdb), Nastran(bdf/nas/fem/op2), OpenRadioss(rad) |
| 판단 기준 | "이 파일로 형상을 볼 수 있나" | "이 파일을 솔버에 넣거나 솔버가 뱉었나" |

확장자 추론(`infer_attachment_kind`)이 자동 처리하지 못하는 경우 — **인제스트 시 kind 직접 지정**.

- ECAD ODB++ 아카이브(.tgz) → 추론상 `archive` 가 되므로 `kind="cad"` 지정
- ECAD-JSON 파생본(.json) → 추론상 `data` 가 되므로 `kind="cad"` 지정
- 확장자 없는 LS-DYNA 산출물(d3plot, d3plot01, binout0000, dynain, d3hsp,
  rcforc, nodout, glstat, messag …)은 파일명 접두로 `cae` 자동 추론된다.
  그 밖의 확장자 없는 덱은 `kind="cae"` 직접 지정.

알려진 확장자 충돌(오분류 시 kind 직접 지정으로 교정).

- `.key` — Apple Keynote 와 충돌. 이 도메인은 LS-DYNA 덱이 압도적이라 `cae` 우선.
- `.odb` — Abaqus 결과 기준. ECAD ODB++ 는 디렉토리/아카이브라 해당 없음.

변환기 거울 맵 — `converter/docx_parser.py` 와 `md_converter/parser.py` 의
확장자 맵은 `schemas/attachment.py` 의 **거울**이다. kind/확장자를 늘릴 때
세 곳을 함께 갱신한다(과거 md 맵은 k/inp/cdb/odb 를 `data` 로 분류했었다 —
cae 신설로 이관 완료).

## 2. CAD 레코드 구성 (MCAD / ECAD)

**1 레코드 = 1 설계 데이터셋** (보드 리비전 하나, 부품/어셈블리 하나). 원본과 파생을
같은 레코드의 첨부로 담는다 — 분리하면 계보 유지 비용만 는다.

```jsonc
{
  "data_type": "CAD",
  "project": "S26-XXX",                    // RecordIn 공통 필드
  "content": {
    "cad_type": "ECAD",                    // MCAD | ECAD | DRAWING
    "file_format": "ODB++",               // 원본(네이티브) 포맷
    "derived_formats": ["ecad-json"],     // 파생 포맷 (MCAD 은 ["STEP"])
    "components": [                        // 부품 단위 BOM 브리지
      { "name": "C-CLIP ANT", "bom_code": "2007-008xxxx", "refdes": "SP101", "qty": 2 }
    ],
    "eng_meta": { /* §4 */ },
    "bom": { "system": "PLM", "codes": ["2007-008xxxx"], "coverage": "partial" }
  }
}
```

첨부 `extra` 관례.

| 첨부 | kind | extra |
|---|---|---|
| 원본 ODB++ / Parasolid | `cad` | `{"format_role": "native", "format": "ODB++"}` |
| 파생 ECAD-JSON / STEP | `cad` | `{"format_role": "derived", "format": "ecad-json", "derived_by": "<변환기명@버전>"}` |

> ECAD-JSON·MCAD-STEP 파생 포맷은 **예약만** 해둔 상태다(변환기 미구현).
> 파생 포맷이 들어오기 시작하면 `derived_by` 로 변환기 버전을 반드시 남긴다 —
> AI 분석 결과의 재현성이 변환기 버전에 종속되기 때문이다.

## 3. SIM 레코드 구성 (CAE)

**1 레코드 = 1 해석 job** (전처리 op 실행, 솔버 런).

```jsonc
{
  "data_type": "SIM",
  "project": "S26-XXX",
  "content": {
    "solver": "LS-DYNA",
    "solver_version": "R16.1.1",
    "inputs":  { "op": "cclip", "config": { /* op 파라미터 */ }, "model": "<입력 첨부 id>" },
    "outputs": { "result_files": ["<첨부 id>"], "report": { /* op 리포트 JSON */ } },
    "runtime": { "job_id": 868, "status": "normal-termination", "elapsed_s": 2 },
    "eng_meta": { /* §4 */ },
    "bom": null
  }
}
```

첨부(`kind="cae"`) `extra` 관례 — **unit_system 은 필수**(덱 파일 안에 명시가 없어
메타로만 보존 가능한 정보다).

```jsonc
{
  "solver": "LS-DYNA",
  "format": "keyword",              // keyword | inp | bulk | binary-result
  "role": "input",                  // input | output | intermediate(dynain)
  "unit_system": "mm-t-s",
  "model_summary": { "nodes": 56, "elements": 18, "parts": 1 }   // 자동 추출 권장
}
```

## 4. 공용 eng_meta — 과제·개발단계·설계안·DOE

```jsonc
"eng_meta": {
  "project": "S26-XXX",                       // record.project 와 동일 값 병기
  "dev_revision": { "phase": "dv", "round": "1" },   // code/seq 자동 유도 → dv1/210
  "design_variation": "antA",                 // 설계안 (사람이 고른 대안, 소수)
  "doe": { "study": "cms_L3", "case": "p4", "factors": { "gap": 0.3 } },
  "model_revision": "v3"                      // 같은 단계 내 재작업 rev
}
```

**개발단계 통제어휘** (phase+round → code/seq 는 스키마가 자동 유도·검증).

| code | phase | round | seq | 의미 |
|---|---|---|---|---|
| pre | pre | — | 100 | 선행 |
| dv1 / dv2 / dv3 / dvr | dv | 1/2/3/r | 210/220/230/290 | 설계 검증 차수 / 최종 |
| pv1 / pv2 / pv3 / pvr | pv | 1/2/3/r | 310/320/330/390 | 양산 검증 차수 / 최종 |
| pra | pra | — | 400 | 양산 승인 |
| mp | mp | — | 500 | 양산 |

- 매칭 질의는 `code` ("dv1 만"), 범위 질의는 `seq` ("pv1 이후" = `seq >= 310`).
- **round 규칙(스키마가 강제)** — dv/pv 는 round 필수(1/2/3/r), pre/pra/mp 는
  round 금지. 표 밖 조합(dv 단독, pre1, mpr …)은 검증에서 거부된다.
  round 는 JSON 숫자(1)로 보내도 "1" 로 수용된다.
- **design_variation ≠ DOE.** 설계안은 사람이 고른 대안(소수), DOE 는 인자 조합
  자동 생성 케이스(대량). 한 필드에 섞으면 설계안 비교 질의가 오염된다.
- 주의: `content` 최상위는 확장 허용(extra=allow)이라 **eng_meta 키 자체의
  오타는 조용히 통과**한다(블록 내부 오타는 거부됨). 인제스트 후
  `content.eng_meta` 존재 확인을 권장.

**DOE 계층 = 레코드 계층 + 메타 병기.**

```text
depth 0: DOE 캠페인 레코드 — eng_meta.doe = {study} (case 없음), 인자표·베이스 모델 첨부
   └ depth 1: 케이스 레코드 × N — parent_record_id → 캠페인, eng_meta.doe = {study, case, factors}
```

레코드 계층은 "이 케이스의 캠페인 전체" 탐색용, `eng_meta.doe` 는
"gap=0.3 인 케이스만" 슬라이스용 — 역할이 달라 병기한다.

## 5. BOM 연계

목표 질의: "이 보드/모델에 들어간 부품의 BOM 코드로 PLM 을 바로 조회".

- **부품 단위** — `content.components[]` 의 `bom_code` (ECAD 는 `refdes` 병기).
  파생 ECAD-JSON/MCAD-STEP 인제스트 시 여기를 자동 채우는 것이 목표.
- **레코드 요약** — `content.bom` (BomLink): `codes[]` 전체 목록 + `coverage`
  (full/partial/none — 코드 추출 완전성). 요약이 있어야 "BOM 코드 X 가 쓰인
  CAD/SIM 레코드 전부" 역질의가 레코드 스캔 없이 된다.
- SIM 은 해석 모델에 포함된 부품의 BOM 코드를 담는다(선택). CAE 덱의 *PART
  타이틀 ↔ BOM 코드 매핑이 확보되는 시점에 자동화한다.

## 6. 질의 예시 (설계 목표 검증)

| 질의 | 사용 축 |
|---|---|
| "S26 과제 DV2 이후 안테나 B안 LS-DYNA 입력덱 전부" | `project` + `eng_meta.dev_revision.seq>=220` + `design_variation` + 첨부(`kind=cae`, `extra.solver`) |
| "cms_L3 DOE 에서 gap=0.3 케이스" | `eng_meta.doe.study/factors` |
| "BOM 코드 2007-008xxxx 가 들어간 설계/해석 데이터" | `content.bom.codes` (CAD+SIM) |
| "이 케이스의 캠페인 전체 보기" | `parent_record_id` / `depth` |

## 7. 남겨둔 것 (구현하지 않음, 자리만)

- **ECAD→JSON 변환기 / MCAD→STEP 변환기** — `derived_formats` 예약과 첨부
  `extra.format_role="derived"` 관례만 확정. 변환기가 생기면 `derived_by` 기입.
- **dev_revision 컬럼 승격** — 지금은 content(JSONB) 안. 슬라이스 수요가
  확인되면 Migration 으로 승격(0006 의 domain/subject_keywords 전례).
- **BOM 시스템 실연동** — `bom.system` 이름만 기록. PLM API 연계는 별도 과제.
- **distribution/ 배포 사본** — 소스와 별개 스냅샷이라 이 변경이 자동 반영되지
  않는다. 다음 배포 갱신(merge-from-drive 등) 때 재생성해야 배포 서버가
  `kind="cae"` 를 수용한다.
- **MCP 인라인 업로드 경로** — `mcp_upload_svc.py` 가 kind 를 자체 규칙으로
  부여(enum 밖 `resource` 포함, 선재 결함)해 cad/cae 를 만들지 못한다.
  CAD/CAE 수집은 정식 ingest 경로 사용을 전제로 하고, MCP 업로드 정합은
  별도 후속 과제.
