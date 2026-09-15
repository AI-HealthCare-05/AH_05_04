"""#591: 로컬 XML 후보의 구조·원본 해시만 검사합니다. Source 승인/적재가 아닙니다."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

SECTION_TITLES = {
    "EE": ("효능효과",),
    "UD": ("용법용량",),
    # MFDS가 실제 겔포스엠 NB XML에서 공백 없는 제목을 반환한다.
    "NB": ("사용상의주의사항", "사용상주의사항"),
    "NN": ("e약은요 정보",),
}
NN_ARTICLE_TITLES = (
    "이 약의 효능은 무엇입니까?",
    "이 약은 어떻게 사용합니까?",
    "이 약을 사용하기 전에 반드시 알아야 할 내용은 무엇입니까?",
    "이 약의 사용상 주의사항은 무엇입니까?",
    "이 약을 사용하는 동안 주의해야 할 약 또는 음식은 무엇입니까?",
    "이 약은 어떤 이상반응이 나타날 수 있습니까?",
    "이 약은 어떻게 보관해야 합니까?",
)
MAX_XML_BYTES = 2 * 1024 * 1024
_ITEM_SEQ_PATTERN = re.compile(r"[0-9]{9}\Z")


def inspect_xml(raw: bytes, section: str) -> dict[str, object]:
    """원문은 재직렬화·요약하지 않고 보존하며, 로그용 집계만 반환합니다."""
    if section not in SECTION_TITLES:
        raise ValueError("UNSUPPORTED_SECTION")
    source = _decode_xml(raw)
    root = _parse_xml(source)
    if root.tag != "DOC" or root.get("type") != section or root.get("title") not in SECTION_TITLES[section]:
        raise ValueError("XML_SECTION_MISMATCH")
    body = _inspect_body(root, section)
    return {
        "section": section,
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "document_title": root.get("title"),
        **body,
        "table_element_count": sum(node.tag.lower() == "table" for node in root.iter()),
    }


def _decode_xml(raw: bytes) -> str:
    if not raw or len(raw) > MAX_XML_BYTES:
        raise ValueError("XML_SIZE_INVALID")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("XML_ENCODING_UNSUPPORTED") from None
    # UTF-16 등의 null byte 우회와 DTD/entity 선언을 파싱 전에 거부합니다.
    if "\x00" in source or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", source, re.IGNORECASE):
        raise ValueError("XML_DECLARATION_UNSAFE")
    declaration = re.match(r"<\?xml\b[^?]*\?>", source)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[0])
        if encoding and encoding[1].lower() not in ("utf-8", "utf8"):
            raise ValueError("XML_ENCODING_UNSUPPORTED")
    return source


def _parse_xml(source: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(source)
    except (ElementTree.ParseError, ValueError):
        raise ValueError("XML_INVALID") from None


def _inspect_body(root: ElementTree.Element, section: str) -> dict[str, object]:
    articles = list(root.iter("ARTICLE"))
    paragraphs = list(root.iter("PARAGRAPH"))
    nonempty_paragraphs = [node for node in paragraphs if "".join(node.itertext()).strip()]
    nonempty_article_titles = [title for node in articles if (title := (node.get("title") or "").strip())]
    if section == "NN":
        article_titles = tuple((node.get("title") or "").strip() for node in articles)
        if article_titles != NN_ARTICLE_TITLES:
            raise ValueError("XML_ARTICLE_SET_INVALID")
    if not nonempty_paragraphs and not nonempty_article_titles:
        raise ValueError("XML_BODY_EMPTY")
    empty_article_titles = []
    if section == "NN":
        empty_article_titles = [
            title
            for node in articles
            if (title := (node.get("title") or "").strip())
            and not any("".join(paragraph.itertext()).strip() for paragraph in node.iter("PARAGRAPH"))
        ]
    return {
        "article_count": len(articles),
        "paragraph_count": len(paragraphs),
        "nonempty_paragraph_count": len(nonempty_paragraphs),
        "empty_article_titles": empty_article_titles,
        "content_status": "PARTIAL_OFFICIAL" if empty_article_titles else "COMPLETE",
    }


def inspect_directory(directory: Path) -> list[dict[str, object]]:
    results = []
    required_sections = ("EE", "UD", "NB")
    if (directory / "NN.xml").is_file():
        required_sections += ("NN",)
    for section in required_sections:
        path = directory / f"{section}.xml"
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("XML_FILE_INVALID")
            with path.open("rb") as stream:
                raw = stream.read(MAX_XML_BYTES + 1)
        except OSError:
            raise ValueError("XML_FILE_UNREADABLE") from None
        results.append(inspect_xml(raw, section))
    return results


def build_handoff_coordinates(item_seq: str, *, include_e_drug: bool) -> list[dict[str, str]]:
    """실제 Snapshot ID가 생기기 전 사용할 결정적 문서 좌표 후보를 만듭니다."""
    if _ITEM_SEQ_PATTERN.fullmatch(item_seq) is None:
        raise ValueError("ITEM_SEQ_INVALID")
    sections = ("EE", "UD", "NB", "NN") if include_e_drug else ("EE", "UD", "NB")
    return [
        {
            "document_type": section,
            "external_document_id": f"mfds-label:{item_seq}:{section}",
            "locator": f"mfds-label/{item_seq}/{section}",
            "source_url": f"https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{item_seq}/{section}",
        }
        for section in sections
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="EE.xml, UD.xml, NB.xml과 선택적 NN.xml이 있는 로컬 폴더",
    )
    args = parser.parse_args()
    try:
        sections = inspect_directory(args.input_dir)
    except ValueError as error:
        print(json.dumps({"candidate_xml_valid": False, "reason": str(error)}))
        return 1
    print(
        json.dumps(
            {
                "candidate_xml_valid": True,
                "product_identity_verified": False,
                "source_ingested": False,
                "sections": sections,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
