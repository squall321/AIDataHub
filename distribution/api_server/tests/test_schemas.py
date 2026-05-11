"""Pydantic 스키마 단위 테스트."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import (
    CADContent,
    DataContent,
    DocumentContent,
    RecordID,
    RecordIn,
    SimContent,
    format_id,
    is_legacy_id,
    normalize_id,
    parse_id,
)


# ---------------------------------------------------------------------------
# id_format
# ---------------------------------------------------------------------------
class TestIdFormat:
    def test_valid_canonical(self) -> None:
        parts = parse_id("DOC-HE-CAE-2026-0000000001")
        assert parts == {
            "data_type": "DOC",
            "team": "HE",
            "group": "CAE",
            "year": 2026,
            "seq": 1,
        }

    def test_valid_all_data_types(self) -> None:
        for dt in ("DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"):
            parts = parse_id(f"{dt}-HE-CAE-2026-0000000001")
            assert parts["data_type"] == dt

    def test_legacy_default_doc(self) -> None:
        parts = parse_id("HE-CAE-2026-0000000001")
        assert parts["data_type"] == "DOC"
        assert parts["team"] == "HE"
        assert parts["group"] == "CAE"
        assert parts["year"] == 2026
        assert parts["seq"] == 1
        assert is_legacy_id("HE-CAE-2026-0000000001") is True
        assert is_legacy_id("DOC-HE-CAE-2026-0000000001") is False

    def test_legacy_with_explicit_default(self) -> None:
        parts = parse_id("HE-CAE-2026-0000000001", default_data_type="DATA")
        assert parts["data_type"] == "DATA"

    def test_normalize_legacy(self) -> None:
        assert normalize_id("HE-CAE-2026-0000000001") == "DOC-HE-CAE-2026-0000000001"
        assert normalize_id(
            "HE-CAE-2026-0000000001", default_data_type="SIM"
        ) == "SIM-HE-CAE-2026-0000000001"

    def test_normalize_canonical_unchanged(self) -> None:
        assert (
            normalize_id("CAD-VD-PLM-2030-0000000123") == "CAD-VD-PLM-2030-0000000123"
        )

    def test_format_id(self) -> None:
        assert (
            format_id("DOC", "HE", "CAE", 2026, 1) == "DOC-HE-CAE-2026-0000000001"
        )
        assert (
            format_id("SIM", "MX", "DEV", 2099, 999_999)
            == "SIM-MX-DEV-2099-0000999999"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "FOO-HE-CAE-2026-0000000001",   # invalid data_type
            "DOC-h-CAE-2026-0000000001",    # team too short / lowercase
            "DOC-HE-cae-2026-0000000001",   # group lowercase
            "DOC-HE-CAE-1999-0000000001",   # year out of range
            "DOC-HE-CAE-2026-12345",    # seq not 6 digits
            "HE-CAE-2026-12345",        # legacy seq wrong width
            "DOC-HE-CAE-2026-0000000001-extra",
            "DOC HE CAE 2026 000001",
        ],
    )
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_id(bad)

    def test_recordid_model_roundtrip(self) -> None:
        rid = RecordID.from_string("DOC-HE-CAE-2026-0000000007")
        assert rid.data_type == "DOC"
        assert rid.seq == 7
        assert rid.to_string() == "DOC-HE-CAE-2026-0000000007"

    def test_recordid_validation_errors(self) -> None:
        with pytest.raises(ValidationError):
            RecordID(
                data_type="DOC", team="he", group="CAE", year=2026, seq=1
            )
        with pytest.raises(ValidationError):
            RecordID(
                data_type="DOC", team="HE", group="CAE", year=1999, seq=1
            )


# ---------------------------------------------------------------------------
# RecordIn
# ---------------------------------------------------------------------------
class TestRecordIn:
    def test_minimal(self) -> None:
        r = RecordIn(
            id="DOC-HE-CAE-2026-0000000001",
            data_type="DOC",
            title="hello",
            content={"meta": {}, "sections": []},
        )
        assert r.summary == ""
        assert r.tags == []
        assert r.agents == []
        assert r.schema_version == "1.0"
        assert r.author == ""
        assert r.version == "1.0"

    def test_legacy_id_accepted(self) -> None:
        # RecordIn.validate_id 가 레거시도 허용 (정규화는 normalizer 가 담당).
        r = RecordIn(
            id="HE-CAE-2026-0000000001",
            data_type="DOC",
            title="legacy id ok",
            content={},
        )
        assert r.id == "HE-CAE-2026-0000000001"

    def test_invalid_id(self) -> None:
        with pytest.raises(ValidationError):
            RecordIn(
                id="not-a-valid-id",
                data_type="DOC",
                title="x",
                content={},
            )

    def test_invalid_data_type(self) -> None:
        with pytest.raises(ValidationError):
            RecordIn(
                id="DOC-HE-CAE-2026-0000000001",
                data_type="XYZ",  # type: ignore[arg-type]
                title="x",
                content={},
            )

    def test_tags_must_be_strings(self) -> None:
        with pytest.raises(ValidationError):
            RecordIn(
                id="DOC-HE-CAE-2026-0000000001",
                data_type="DOC",
                title="x",
                content={},
                tags=[1, 2, 3],  # type: ignore[list-item]
            )


# ---------------------------------------------------------------------------
# DocumentContent
# ---------------------------------------------------------------------------
class TestDocumentContent:
    def test_minimal_empty(self) -> None:
        c = DocumentContent()
        assert c.schema_version == "1.0"
        assert c.sections == []

    def test_full(self) -> None:
        c = DocumentContent(
            schema_version="1.0",
            meta={"doc_id": "DOC-HE-CAE-2026-0000000001", "title": "x"},
            toc=[{"id": "1", "level": 1, "title": "Intro"}],
            sections=[
                {"id": "1", "level": 1, "title": "Intro", "blocks": []},
            ],
            figures=[],
            tables=[{"id": "T1", "caption": "x"}],
            sources=[],
        )
        assert len(c.sections) == 1

    def test_section_missing_id_or_title(self) -> None:
        with pytest.raises(ValidationError):
            DocumentContent(
                meta={},
                sections=[{"level": 1, "title": "no id"}],
            )
        with pytest.raises(ValidationError):
            DocumentContent(
                meta={},
                sections=[{"id": "1", "level": 1}],
            )


# ---------------------------------------------------------------------------
# DataContent
# ---------------------------------------------------------------------------
class TestDataContent:
    def test_basic(self) -> None:
        c = DataContent(
            caption="material props",
            headers=["name", "E", "rho"],
            rows=[["steel", 210e9, 7850], ["alu", 70e9, 2700]],
            units={"E": "Pa", "rho": "kg/m^3"},
            notes="standard",
        )
        assert len(c.rows) == 2

    def test_row_width_mismatch(self) -> None:
        with pytest.raises(ValidationError):
            DataContent(
                caption="bad",
                headers=["a", "b"],
                rows=[["x"]],
            )

    def test_no_headers_allows_any_rows(self) -> None:
        c = DataContent(
            caption="free", headers=[], rows=[["x", 1], [None]]
        )
        assert len(c.rows) == 2


# ---------------------------------------------------------------------------
# SimContent
# ---------------------------------------------------------------------------
class TestSimContent:
    def test_basic(self) -> None:
        c = SimContent(
            solver="LS-DYNA",
            solver_version="R12",
            inputs={"k_file": "main.k"},
            outputs={"d3plot": "/path/d3plot"},
            runtime={"cpu_time": 3600, "status": "ok"},
        )
        assert c.solver == "LS-DYNA"
        assert c.runtime is not None

    def test_missing_solver(self) -> None:
        with pytest.raises(ValidationError):
            SimContent(inputs={}, outputs={})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CADContent
# ---------------------------------------------------------------------------
class TestCADContent:
    def test_basic(self) -> None:
        c = CADContent(
            cad_type="MCAD",
            file_format="CATPart",
            file_metadata={"path": "/x", "size_bytes": 100, "hash_sha256": "abcd"},
            components=[{"name": "bracket"}],
        )
        assert c.cad_type == "MCAD"
        assert c.file_format == "CATPart"

    def test_invalid_cad_type(self) -> None:
        with pytest.raises(ValidationError):
            CADContent(
                cad_type="WHATEVER",  # type: ignore[arg-type]
                file_format="STEP",
            )
