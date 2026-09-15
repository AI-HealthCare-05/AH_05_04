"""#591: 로컬 XML 후보의 구조·원본 해시만 검사합니다. Source 승인/적재가 아닙니다."""

import argparse
import json
import sys
from pathlib import Path

from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    MAX_XML_BYTES,
    NN_ARTICLE_TITLES,
    SECTION_TITLES,
    inspect_xml,
)

__all__ = ["MAX_XML_BYTES", "NN_ARTICLE_TITLES", "SECTION_TITLES", "inspect_xml"]


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
    sections = ("EE", "UD", "NB", "NN") if include_e_drug else ("EE", "UD", "NB")
    if not item_seq.isascii() or not item_seq.isdigit() or len(item_seq) != 9:
        raise ValueError("ITEM_SEQ_INVALID")
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
