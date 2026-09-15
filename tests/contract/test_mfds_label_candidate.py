"""#591 로컬 후보 검사: 합성 XML만 사용하며 의료 원문을 포함하지 않습니다."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.rag.verify_mfds_label_candidate import (
    MAX_XML_BYTES,
    NN_ARTICLE_TITLES,
    SECTION_TITLES,
    build_handoff_coordinates,
    inspect_directory,
    inspect_xml,
)


def synthetic_xml(section: str) -> bytes:
    return (
        f'<DOC type="{section}" title="{SECTION_TITLES[section][0]}"><ARTICLE title="synthetic title">'
        "<PARAGRAPH>before &lt;synthetic heading&gt;<table><tr><td>cell</td></tr></table>"
        "after</PARAGRAPH></ARTICLE></DOC>"
    ).encode()


@pytest.mark.parametrize("section", ("EE", "UD", "NB"))
def test_structure_and_hash_use_complete_original_bytes(section):
    raw = synthetic_xml(section)
    result = inspect_xml(raw, section)
    assert result == {
        "section": section,
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "document_title": SECTION_TITLES[section][0],
        "article_count": 1,
        "paragraph_count": 1,
        "nonempty_paragraph_count": 1,
        "empty_article_titles": [],
        "content_status": "COMPLETE",
        "table_element_count": 1,
    }
    assert "synthetic heading" not in str(result)


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"", "XML_SIZE_INVALID"),
        (b"x" * (MAX_XML_BYTES + 1), "XML_SIZE_INVALID"),
        (b'<!DOCTYPE DOC [<!ENTITY x "sensitive">]><DOC/>', "XML_DECLARATION_UNSAFE"),
        (b"\x00<DOCTYPE>", "XML_DECLARATION_UNSAFE"),
        (b'<?xml version="1.0" encoding="iso-8859-1"?><DOC/>', "XML_ENCODING_UNSUPPORTED"),
        (b"<DOC>private-content", "XML_INVALID"),
        (b"<html>access denied</html>", "XML_SECTION_MISMATCH"),
        ('<DOC type="EE" title="효능효과"><PARAGRAPH> </PARAGRAPH></DOC>'.encode(), "XML_BODY_EMPTY"),
    ],
)
def test_invalid_input_fails_without_echoing_contents(raw, reason):
    with pytest.raises(ValueError, match=f"^{reason}$"):
        inspect_xml(raw, "EE")


def test_wrong_section_is_rejected():
    with pytest.raises(ValueError, match="XML_SECTION_MISMATCH"):
        inspect_xml(synthetic_xml("UD"), "EE")


def test_article_title_only_body_is_accepted():
    raw = '<DOC type="EE" title="효능효과"><ARTICLE title="synthetic efficacy"/></DOC>'.encode()
    result = inspect_xml(raw, "EE")
    assert result["nonempty_paragraph_count"] == 0
    assert result["empty_article_titles"] == []
    assert result["content_status"] == "COMPLETE"


def test_observed_nb_title_variant_is_accepted():
    raw = '<DOC type="NB" title="사용상주의사항"><PARAGRAPH>synthetic warning</PARAGRAPH></DOC>'.encode()
    assert inspect_xml(raw, "NB")["document_title"] == "사용상주의사항"


def synthetic_nn_xml(*, empty_title: str | None = None) -> bytes:
    articles = "".join(
        f'<ARTICLE title="{title}"><PARAGRAPH>{"" if title == empty_title else "synthetic body"}</PARAGRAPH></ARTICLE>'
        for title in NN_ARTICLE_TITLES
    )
    return f'<DOC type="NN" title="e약은요 정보">{articles}</DOC>'.encode()


def test_e_drug_seven_articles_are_validated_and_empty_body_is_reported():
    empty_title = NN_ARTICLE_TITLES[2]
    result = inspect_xml(synthetic_nn_xml(empty_title=empty_title), "NN")
    assert result["article_count"] == 7
    assert result["nonempty_paragraph_count"] == 6
    assert result["empty_article_titles"] == [empty_title]
    assert result["content_status"] == "PARTIAL_OFFICIAL"


def test_e_drug_missing_or_reordered_article_fails_closed():
    raw = (
        '<DOC type="NN" title="e약은요 정보">'
        + "".join(f'<ARTICLE title="{title}"><PARAGRAPH>body</PARAGRAPH></ARTICLE>' for title in NN_ARTICLE_TITLES[:-1])
        + "</DOC>"
    ).encode()
    with pytest.raises(ValueError, match="XML_ARTICLE_SET_INVALID"):
        inspect_xml(raw, "NN")

    reordered = list(NN_ARTICLE_TITLES)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    raw = (
        '<DOC type="NN" title="e약은요 정보">'
        + "".join(f'<ARTICLE title="{title}"><PARAGRAPH>body</PARAGRAPH></ARTICLE>' for title in reordered)
        + "</DOC>"
    ).encode()
    with pytest.raises(ValueError, match="XML_ARTICLE_SET_INVALID"):
        inspect_xml(raw, "NN")

    duplicate = synthetic_nn_xml().replace(
        b"</DOC>",
        f'<ARTICLE title="{NN_ARTICLE_TITLES[-1]}"><PARAGRAPH>body</PARAGRAPH></ARTICLE></DOC>'.encode(),
    )
    with pytest.raises(ValueError, match="XML_ARTICLE_SET_INVALID"):
        inspect_xml(duplicate, "NN")


def test_all_three_sections_required(tmp_path):
    for section in ("EE", "UD", "NB"):
        (tmp_path / f"{section}.xml").write_bytes(synthetic_xml(section))
    assert len(inspect_directory(tmp_path)) == 3
    (tmp_path / "NB.xml").unlink()
    with pytest.raises(ValueError, match="XML_FILE_INVALID"):
        inspect_directory(tmp_path)


def test_optional_e_drug_document_is_inspected_when_present(tmp_path):
    for section in ("EE", "UD", "NB"):
        (tmp_path / f"{section}.xml").write_bytes(synthetic_xml(section))
    (tmp_path / "NN.xml").write_bytes(synthetic_nn_xml())
    results = inspect_directory(tmp_path)
    assert [result["section"] for result in results] == ["EE", "UD", "NB", "NN"]


@pytest.mark.parametrize(
    "include_e_drug,expected_sections", [(False, ["EE", "UD", "NB"]), (True, ["EE", "UD", "NB", "NN"])]
)
def test_handoff_coordinates_are_stable_and_scoped(include_e_drug, expected_sections):
    coordinates = build_handoff_coordinates("200400463", include_e_drug=include_e_drug)
    assert [item["document_type"] for item in coordinates] == expected_sections
    assert coordinates[0] == {
        "document_type": "EE",
        "external_document_id": "mfds-label:200400463:EE",
        "locator": "mfds-label/200400463/EE",
        "source_url": "https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/200400463/EE",
    }
    assert len({item["external_document_id"] for item in coordinates}) == len(coordinates)
    assert len({item["locator"] for item in coordinates}) == len(coordinates)


@pytest.mark.parametrize("item_seq", ["", "20040046", "2004004630", "20040046x"])
def test_handoff_coordinates_reject_invalid_item_seq(item_seq):
    with pytest.raises(ValueError, match="ITEM_SEQ_INVALID"):
        build_handoff_coordinates(item_seq, include_e_drug=False)


def test_committed_precheck_manifest_has_unique_fail_closed_coordinates():
    manifest_path = Path("docs/validation/rag/issue-591/mfds-16-product-precheck.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "LOCAL_PRECHECK_ONLY"
    assert manifest["source_ingested"] is False
    assert manifest["db_requery_verified"] is False
    assert manifest["rag_use_approved"] is False
    assert manifest["source_code"] is None
    assert manifest["source_version"] is None

    products = manifest["products"]
    assert len(products) == 16
    assert len({product["item_seq"] for product in products}) == 16
    coordinates = []
    for product in products:
        assert product["permit_evidence"] == "NO_CANCELLATION_MARKER_OBSERVED"
        assert all(len(value) == 64 for value in product["documents"].values())
        include_e_drug = product["classification"] == "일반의약품"
        expected = build_handoff_coordinates(product["item_seq"], include_e_drug=include_e_drug)
        assert list(product["documents"]) == [item["document_type"] for item in expected]
        coordinates.extend(expected)
    assert len({item["external_document_id"] for item in coordinates}) == len(coordinates)
    assert len({item["locator"] for item in coordinates}) == len(coordinates)


def test_symlink_input_rejected(tmp_path):
    original = tmp_path / "source.xml"
    original.write_bytes(synthetic_xml("EE"))
    (tmp_path / "EE.xml").symlink_to(original)
    with pytest.raises(ValueError, match="XML_FILE_INVALID"):
        inspect_directory(tmp_path)
