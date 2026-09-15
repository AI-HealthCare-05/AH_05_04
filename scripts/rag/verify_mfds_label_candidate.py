"""#591: 로컬 XML 후보의 구조·원본 해시만 검사합니다. Source 승인/적재가 아닙니다."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

SECTION_TITLES = {"EE": "효능효과", "UD": "용법용량", "NB": "사용상의주의사항"}
MAX_XML_BYTES = 2 * 1024 * 1024


def inspect_xml(raw: bytes, section: str) -> dict[str, object]:
    """원문은 재직렬화·요약하지 않고 보존하며, 로그용 집계만 반환합니다."""
    if section not in SECTION_TITLES:
        raise ValueError("UNSUPPORTED_SECTION")
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
    try:
        root = ElementTree.fromstring(source)
    except (ElementTree.ParseError, ValueError):
        raise ValueError("XML_INVALID") from None
    if root.tag != "DOC" or root.get("type") != section or root.get("title") != SECTION_TITLES[section]:
        raise ValueError("XML_SECTION_MISMATCH")
    paragraphs = list(root.iter("PARAGRAPH"))
    if not paragraphs or not any("".join(node.itertext()).strip() for node in paragraphs):
        raise ValueError("XML_BODY_EMPTY")
    return {
        "section": section,
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "article_count": len(list(root.iter("ARTICLE"))),
        "paragraph_count": len(paragraphs),
        "table_element_count": sum(node.tag.lower() == "table" for node in root.iter()),
    }


def inspect_directory(directory: Path) -> list[dict[str, object]]:
    results = []
    for section in SECTION_TITLES:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="EE.xml, UD.xml, NB.xml이 있는 로컬 폴더")
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
