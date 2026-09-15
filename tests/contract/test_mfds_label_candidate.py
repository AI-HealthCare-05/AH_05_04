"""#591 로컬 후보 검사: 합성 XML만 사용하며 의료 원문을 포함하지 않습니다."""

import hashlib

import pytest

from scripts.rag.verify_mfds_label_candidate import MAX_XML_BYTES, SECTION_TITLES, inspect_directory, inspect_xml


def synthetic_xml(section: str) -> bytes:
    return (
        f'<DOC type="{section}" title="{SECTION_TITLES[section]}"><ARTICLE title="synthetic title">'
        "<PARAGRAPH>before &lt;synthetic heading&gt;<table><tr><td>cell</td></tr></table>"
        "after</PARAGRAPH></ARTICLE></DOC>"
    ).encode()


@pytest.mark.parametrize("section", SECTION_TITLES)
def test_structure_and_hash_use_complete_original_bytes(section):
    raw = synthetic_xml(section)
    result = inspect_xml(raw, section)
    assert result == {
        "section": section,
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "article_count": 1,
        "paragraph_count": 1,
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


def test_all_three_sections_required(tmp_path):
    for section in SECTION_TITLES:
        (tmp_path / f"{section}.xml").write_bytes(synthetic_xml(section))
    assert len(inspect_directory(tmp_path)) == 3
    (tmp_path / "NB.xml").unlink()
    with pytest.raises(ValueError, match="XML_FILE_INVALID"):
        inspect_directory(tmp_path)


def test_symlink_input_rejected(tmp_path):
    original = tmp_path / "source.xml"
    original.write_bytes(synthetic_xml("EE"))
    (tmp_path / "EE.xml").symlink_to(original)
    with pytest.raises(ValueError, match="XML_FILE_INVALID"):
        inspect_directory(tmp_path)
