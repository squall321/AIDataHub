# CAD/CAE 메타 규칙 회귀 — cae kind·추론 거울 동기화·DevRevision 어휘·normalizer 보존
"""cad_cae_metadata_rules.md v1.0 계약의 회귀 테스트.

적대검증에서 확정된 결함들의 재발 방지가 목적이다.

1. normalizer 가 SIM/CAD 신규 필드(eng_meta/bom/derived_formats)를 드롭하지 않는가
2. 확장자→kind 추론 3벌(schemas / docx 거울 / md 거울)이 cae 에 대해 동일한가
3. DevRevision 이 통제어휘 표 밖 조합(dv 단독, pre1 …)을 거부하는가
4. 확장자 없는 LS-DYNA 산출물(d3plot, dynain …)이 cae 로 추론되는가
"""
from __future__ import annotations

import pytest

from api.ingest.normalizer import normalize
from api.schemas import ATTACHMENT_KINDS, DevRevision
from api.schemas.attachment import infer_attachment_kind as schema_infer
from converter.docx_parser import infer_attachment_kind as docx_infer
from md_converter.parser import infer_attachment_kind_from_url as md_infer

CAE_EXTS = ["k", "key", "dyn", "dynain", "d3plot", "inp", "cdb", "odb",
            "rad", "bdf", "nas", "fem", "op2"]

ENG_META = {
    "project": "S26-X",
    "dev_revision": {"phase": "dv", "round": "1"},
    "doe": {"study": "cms_L3", "case": "p4", "factors": {"gap": 0.3}},
}


def test_cae_kind_registered():
    assert "cae" in ATTACHMENT_KINDS
    assert len(ATTACHMENT_KINDS) == 11


@pytest.mark.parametrize("ext", CAE_EXTS)
def test_kind_mirrors_agree_on_cae(ext: str):
    fn = f"file.{ext}"
    assert schema_infer(fn) == docx_infer(fn) == md_infer(fn) == "cae"


def test_extensionless_lsdyna_outputs_infer_cae():
    for name in ["d3plot", "d3plot01", "binout0000", "dynain", "d3hsp", "rcforc"]:
        assert schema_infer(name) == "cae", name
    assert schema_infer("README") == "other"           # 비CAE 무확장자는 폴백
    assert schema_infer("board_odbpp.tgz") == "archive"


def test_dev_revision_vocabulary():
    r = DevRevision(phase="dv", round="1")
    assert (r.code, r.seq) == ("dv1", 210)
    assert DevRevision(phase="pv", round="r").seq == 390
    assert DevRevision(phase="pra").seq == 400
    # JSON 숫자 round 수용
    assert DevRevision(phase="dv", round=1).code == "dv1"
    # 표 밖 조합 거부: dv 단독 / pre+round / mp+round / code 불일치
    for bad in [dict(phase="dv"), dict(phase="pre", round="1"),
                dict(phase="mp", round="r"),
                dict(phase="dv", round="1", code="pv1")]:
        with pytest.raises(ValueError):
            DevRevision(**bad)


def _content_of(result):
    if hasattr(result, "content"):
        return result.content
    if isinstance(result, tuple):
        for e in result:
            if hasattr(e, "content"):
                return e.content
            if isinstance(e, dict) and "content" in e:
                return e["content"]
    if isinstance(result, dict):
        return result.get("content", result)
    raise AssertionError(f"unknown normalize return: {type(result)}")


def test_normalizer_preserves_sim_eng_meta():
    raw = {"id": "SIM-MX-CA-2026-0000000001", "data_type": "SIM", "title": "t",
           "content": {"solver": "LS-DYNA", "inputs": {"op": "cclip"},
                       "eng_meta": ENG_META,
                       "bom": {"codes": ["2007-1"], "coverage": "partial"}}}
    content = _content_of(normalize(raw))
    assert content.get("eng_meta") == ENG_META
    assert content.get("bom", {}).get("codes") == ["2007-1"]


def test_normalizer_preserves_cad_eng_meta():
    raw = {"id": "CAD-MX-CA-2026-0000000001", "data_type": "CAD", "title": "t",
           "content": {"cad_type": "ECAD", "file_format": "ODB++",
                       "derived_formats": ["ecad-json"],
                       "eng_meta": ENG_META, "bom": {"codes": ["2007-1"]}}}
    content = _content_of(normalize(raw))
    assert content.get("derived_formats") == ["ecad-json"]
    assert content.get("eng_meta") == ENG_META
    assert content.get("bom", {}).get("codes") == ["2007-1"]
