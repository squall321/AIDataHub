"""doc_types.mode + external_id_map + 35종 doc_type seed

Revision ID: 0026
Revises: 0024
Create Date: 2026-05-28

도입 이유:
    1. doc_types 에 mode 컴럼 추가 — 자료 성격 축 (llm_context / data_extract / hybrid).
       data_extract 는 embedding 생성 skip → 천만 건급 대용량에서 비용 절감.
    2. external_id_map 테이블 추가 — SignalForge / MXWP 등 외부 시스템이
       자신의 ID 로 sync 할 때 우리 record_id 로 매핑.
    3. 35종 doc_type seed — 사업부 파일럿 즉시 사용 가능.

다운그레이드 시:
    - mode 컴럼 DROP
    - external_id_map DROP
    - 기존 4개 외 seed row 는 유지 (이미 record 가 참조할 수 있으므로
      수동 정리).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# 35 doc_types seed (mode 포함)
# ---------------------------------------------------------------------------
_NEW_DOC_TYPES: list[dict[str, str]] = [
    # 1. 공통 텍스트형 자료
    {"code": "meeting_minutes", "name": "Meeting Minutes", "mode": "llm_context",
     "description": "회의록 · 의사결정 기록."},
    {"code": "design_spec", "name": "Design Specification", "mode": "llm_context",
     "description": "설계 사양서."},
    {"code": "test_plan", "name": "Test Plan", "mode": "llm_context",
     "description": "시험 계획서."},
    {"code": "policy", "name": "Policy", "mode": "llm_context",
     "description": "정책 · 규정."},
    {"code": "standard", "name": "Standard", "mode": "llm_context",
     "description": "표준 문서 (KS / ASTM / ISO 등)."},
    {"code": "lessons_learned", "name": "Lessons Learned", "mode": "llm_context",
     "description": "프로젝트 교훈 · 재발 방지 기록."},

    # 2. 재료 / 시험 데이터
    {"code": "material_test_data", "name": "Material Test Data", "mode": "data_extract",
     "description": "재료 인장/충격/피로 시험 수치 포인트."},
    {"code": "material_test_report", "name": "Material Test Report", "mode": "llm_context",
     "description": "재료 시험 해석 · 결론 보고서."},
    {"code": "correlation_study", "name": "Correlation Study", "mode": "hybrid",
     "description": "시험 - 해석 대비 자료."},

    # 3. 시장 VOC
    {"code": "voc_report", "name": "Market VOC Report", "mode": "llm_context",
     "description": "사용자 클레임 · 리뷰 원본."},
    {"code": "voc_metrics", "name": "VOC Metrics", "mode": "data_extract",
     "description": "VOC 빈도 · 심각도 집계."},

    # 4. 시뮬레이션 분야별
    {"code": "drop_test_sim", "name": "Drop Test Simulation", "mode": "hybrid",
     "description": "낙하시험 시뮬 (입력 + 결과)."},
    {"code": "crash_sim", "name": "Crash Simulation", "mode": "hybrid",
     "description": "충돌 · 강도 해석."},
    {"code": "impact_sim", "name": "Impact Simulation", "mode": "hybrid",
     "description": "충격 · 관통 해석."},
    {"code": "vibration_sim", "name": "Vibration / Modal Simulation", "mode": "hybrid",
     "description": "진동 · 모달 해석."},
    {"code": "fatigue_sim", "name": "Fatigue Simulation", "mode": "hybrid",
     "description": "피로 해석."},
    {"code": "thermal_sim", "name": "Thermal Simulation", "mode": "hybrid",
     "description": "열 전달 해석."},
    {"code": "cfd_sim", "name": "CFD Simulation", "mode": "hybrid",
     "description": "유체 · 공력 해석."},
    {"code": "acoustic_sim", "name": "Acoustic Simulation", "mode": "hybrid",
     "description": "음향 · 스피커 해석."},
    {"code": "emc_sim", "name": "EMC / EMI Simulation", "mode": "hybrid",
     "description": "전자파 적합성 해석."},
    {"code": "antenna_sim", "name": "Antenna / RF Simulation", "mode": "hybrid",
     "description": "안테나 · RF 해석."},
    {"code": "injection_molding_sim", "name": "Injection Molding Simulation", "mode": "hybrid",
     "description": "사출 성형 해석."},
    {"code": "warpage_sim", "name": "Warpage Simulation", "mode": "hybrid",
     "description": "와피지 해석."},
    {"code": "optical_sim", "name": "Optical / Display Simulation", "mode": "hybrid",
     "description": "광학 · 디스플레이 해석."},
    {"code": "multiphysics_sim", "name": "Multiphysics Simulation", "mode": "hybrid",
     "description": "다물리 · 연성 해석."},

    # 5. 배터리 세분화
    {"code": "battery_thermal_sim", "name": "Battery Thermal Simulation", "mode": "hybrid",
     "description": "배터리 열 전달 · 열폭주 해석."},
    {"code": "battery_mech_sim", "name": "Battery Mechanical Simulation", "mode": "hybrid",
     "description": "배터리 압축 · 찌그러짐 해석."},
    {"code": "battery_swelling_sim", "name": "Battery Swelling Simulation", "mode": "hybrid",
     "description": "셀 스웰링 해석."},
    {"code": "battery_electrochem_sim", "name": "Battery Electrochemical Simulation", "mode": "hybrid",
     "description": "배터리 전기화학 (Randles 등)."},

    # 6. 일반화 시뮬 (fallback)
    {"code": "simulation_setup", "name": "Simulation Setup (Generic)", "mode": "hybrid",
     "description": "분류 안 되는 시뮬 입력 카드 일반."},
    {"code": "simulation_result", "name": "Simulation Result (Generic)", "mode": "data_extract",
     "description": "분류 안 되는 시뮬 결과 일반."},
    {"code": "simulation_report", "name": "Simulation Report", "mode": "llm_context",
     "description": "시뮬 해석 · 결론 보고서."},

    # 7. White Paper / 전략
    {"code": "whitepaper", "name": "White Paper", "mode": "llm_context",
     "description": "MX White Paper 계열 문서."},
    {"code": "feasibility_study", "name": "Feasibility Study", "mode": "llm_context",
     "description": "기술 · 사업 타당성 검토."},

    # 8. 공통 보완
    {"code": "safety_assessment", "name": "Safety Assessment", "mode": "hybrid",
     "description": "안전 평가 보고서."},
]


def upgrade() -> None:
    # --------------------------- 1. doc_types.mode 컴럼 추가 ----------------
    op.add_column(
        "doc_types",
        sa.Column(
            "mode",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'llm_context'"),
        ),
    )
    # 기존 4개 (manual/report/checklist/training) 은 default 값으로 자동 설정됨.
    # CHECK constraint — 세 값만 허용.
    op.create_check_constraint(
        "ck_doc_types_mode",
        "doc_types",
        "mode IN ('llm_context','data_extract','hybrid')",
    )

    # --------------------------- 2. external_id_map 테이블 ----------------
    op.create_table(
        "external_id_map",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=40), nullable=False,
                  comment="외부 시스템 식별자 (e.g. 'signalforge', 'mxwp')"),
        sa.Column("external_id", sa.String(length=120), nullable=False,
                  comment="외부 시스템의 record id"),
        sa.Column("record_id", sa.String(length=80), nullable=False,
                  comment="AX Hub records.id FK"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "external_id", name="uq_external_id_map_source_external"),
        sa.ForeignKeyConstraint(
            ["record_id"], ["records.id"], name="fk_external_id_map_record",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_external_id_map_record", "external_id_map", ["record_id"])
    op.create_index("idx_external_id_map_source", "external_id_map", ["source"])

    # --------------------------- 3. 35종 seed 삽입 (느슨 충돌 무시) ----------
    # 기존 4개 (manual/report/checklist/training) 는 이미 존재.
    # ON CONFLICT (code) DO UPDATE SET name/mode/description 으로 재적용 허용 —
    # 기존 4개는 mode 만 채워지고 어휘/설명은 유지.
    rows_sql_parts = []
    for d in _NEW_DOC_TYPES:
        # PG 이스케이프 싱글 쿠온 대체
        n = d["name"].replace("'", "''")
        desc = d["description"].replace("'", "''")
        rows_sql_parts.append(
            f"('{d['code']}', '{n}', '{desc}', '{{}}', '{d['mode']}')"
        )
    values_sql = ",\n        ".join(rows_sql_parts)
    op.execute(
        f"""
        INSERT INTO doc_types (code, name, description, expected_sections, mode)
        VALUES
        {values_sql}
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            mode = EXCLUDED.mode
        """
    )


def downgrade() -> None:
    # 1. seed rows 삭제 (신규 추가분만 — 기존 4개는 유지)
    codes = "','".join(d["code"] for d in _NEW_DOC_TYPES)
    op.execute(f"DELETE FROM doc_types WHERE code IN ('{codes}')")

    # 2. external_id_map
    op.drop_index("idx_external_id_map_source", table_name="external_id_map")
    op.drop_index("idx_external_id_map_record", table_name="external_id_map")
    op.drop_table("external_id_map")

    # 3. doc_types.mode 어디서 참조하는 record 가 있을 수 있으므로
    #    몇은 ON UPDATE/SELECT 에는 영향 없음. 그대로 drop.
    op.drop_constraint("ck_doc_types_mode", "doc_types", type_="check")
    op.drop_column("doc_types", "mode")
