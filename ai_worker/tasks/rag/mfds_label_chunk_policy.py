"""MFDS 허가사항 parsed 문서를 결정적 Knowledge chunk text로 투영합니다.

이 모듈은 순수 변환 계층입니다. DB, artifact reader, receipt, transaction에 의존하지
않습니다. Unicode NFC와 newline·공백 정규화는 이 계층의 책임이며 parser seam
(`parse_mfds_label_artifact()`)은 정규화를 수행하지 않습니다.

`content_hash`가 `chunk_text`의 정확한 UTF-8 bytes에서 계산되므로 렌더링 규약을
byte 단위로 고정합니다. 규약이 바뀌면 `CHUNK_POLICY_VERSION`을 올려야 합니다.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from xml.etree import ElementTree

from ai_worker.tasks.rag.source_ingestion.mfds_label import ParsedMfdsLabelDocument

CHUNK_POLICY_VERSION = "mfds-label-knowledge-chunk@1"
BLOCK_SEPARATOR = "\n\n"
ROW_SEPARATOR = "\n"
CELL_SEPARATOR = "\t"
HEADING_MARKER = "#"

_BLANK_LINES = re.compile(r"\n{3,}")
_TABLE_TAG = "table"
_ROW_TAG = "tr"
_CELL_TAGS = ("td", "th")
_CAPTION_TAG = "caption"


class ChunkPolicyFailureReason(StrEnum):
    CHUNK_POLICY_UNSUPPORTED = "CHUNK_POLICY_UNSUPPORTED"


class ChunkPolicyError(Exception):
    """reason만 노출하는 청킹 정책 오류입니다.

    상위 materialization 계층은 `reason`을 그대로 투영하며 이 예외의 문자열을
    파싱하지 않습니다.
    """

    def __init__(self, reason: ChunkPolicyFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class KnowledgeChunkDraft:
    chunk_index: int
    content_hash: str
    normalization_version: str
    chunk_text: str = field(repr=False)


def build_chunk_drafts(
    parsed: ParsedMfdsLabelDocument,
    *,
    chunk_policy_version: str,
) -> tuple[KnowledgeChunkDraft, ...]:
    """검증된 parsed 문서를 0-indexed chunk draft로 투영합니다."""
    if chunk_policy_version != CHUNK_POLICY_VERSION:
        raise ChunkPolicyError(ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED)
    if parsed.section == "NN" and parsed.empty_article_titles:
        raise ChunkPolicyError(ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED)

    drafts: list[KnowledgeChunkDraft] = []
    for chunk_index, raw_text in enumerate(_chunk_texts(parsed.root)):
        chunk_text = normalize_chunk_text(raw_text)
        if not chunk_text:
            raise ChunkPolicyError(ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED)
        drafts.append(
            KnowledgeChunkDraft(
                chunk_index=chunk_index,
                content_hash=chunk_content_hash(chunk_text),
                normalization_version=CHUNK_POLICY_VERSION,
                chunk_text=chunk_text,
            )
        )
    if not drafts:
        raise ChunkPolicyError(ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED)
    return tuple(drafts)


def chunk_content_hash(chunk_text: str) -> str:
    """정규화된 chunk_text의 정확한 UTF-8 bytes에서 SHA-256을 계산합니다."""
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()


def normalize_chunk_text(text: str) -> str:
    """계약이 고정한 순서로만 정규화합니다.

    NFC -> newline 단일화 -> 줄별 trailing whitespace 제거 -> 연속 빈 줄 축약 ->
    전체 strip. 순서를 바꾸면 결과가 달라지므로 고정합니다.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_LINES.sub(BLOCK_SEPARATOR, text)
    return text.strip()


def _chunk_texts(root: ElementTree.Element) -> list[str]:
    """DOC 직하를 순회해 정규화 전 chunk 문자열을 source order로 만듭니다.

    top-level ARTICLE은 각각 하나의 chunk가 되고, 연속된 non-ARTICLE 자식은
    최대 연속 구간 하나를 하나의 chunk로 묶습니다.
    """
    texts: list[str] = []
    pending: list[str] = []
    inline: list[str] = [root.text or ""]

    def flush_inline() -> None:
        buffered = "".join(inline).strip()
        inline.clear()
        if buffered:
            pending.append(buffered)

    def flush_pending() -> None:
        flush_inline()
        if pending:
            texts.append(BLOCK_SEPARATOR.join(pending))
            pending.clear()

    for child in root:
        if child.tag == "ARTICLE":
            flush_pending()
            texts.append(_render_article(child, 1))
        elif child.tag == "PARAGRAPH":
            flush_inline()
            pending.extend(_render_blocks(child, 1))
        elif _is_table(child.tag):
            flush_inline()
            pending.append(_render_table(child))
        else:
            inline.append(_render_inline(child))
        inline.append(child.tail or "")
    flush_pending()
    return texts


def _render_article(article: ElementTree.Element, level: int) -> str:
    """ARTICLE을 heading 한 줄과 하위 블록으로 렌더링합니다."""
    blocks: list[str] = []
    title = (article.get("title") or "").strip()
    if title:
        blocks.append(f"{HEADING_MARKER * level} {title}")
    blocks.extend(_render_blocks(article, level))
    return BLOCK_SEPARATOR.join(blocks)


def _render_blocks(element: ElementTree.Element, level: int) -> list[str]:
    """자식을 블록 단위로 렌더링합니다. nested ARTICLE은 heading level을 하나 늘립니다."""
    blocks: list[str] = []
    inline: list[str] = [element.text or ""]

    def flush_inline() -> None:
        buffered = "".join(inline).strip()
        inline.clear()
        if buffered:
            blocks.append(buffered)

    for child in element:
        if child.tag == "ARTICLE":
            flush_inline()
            blocks.append(_render_article(child, level + 1))
        elif child.tag == "PARAGRAPH":
            flush_inline()
            blocks.extend(_render_blocks(child, level))
        elif _is_table(child.tag):
            flush_inline()
            blocks.append(_render_table(child))
        else:
            inline.append(_render_inline(child))
        inline.append(child.tail or "")
    flush_inline()
    return blocks


def _render_inline(element: ElementTree.Element) -> str:
    """인라인 위치의 text, 자식, tail을 구분자 없이 source order로 잇습니다.

    table은 계약상 위치와 무관하게 블록이므로 인라인 위치에서는 렌더링할 수 없습니다.
    여기서 인라인으로 이어 붙이면 같은 표가 중첩 깊이에 따라 블록/인라인으로 갈려
    `content_hash`가 위치 의존적이 되므로 fail-closed 합니다.
    """
    parts = [element.text or ""]
    for child in element:
        if _is_table(child.tag):
            raise ChunkPolicyError(ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED)
        parts.append(_render_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _render_table(table: ElementTree.Element) -> str:
    """caption을 첫 줄에 두고 나머지 행을 source order로 배치합니다."""
    lines: list[str] = []
    for child in table:
        if child.tag == _CAPTION_TAG:
            lines.append(_render_inline(child).strip())
    for row in table.iter(_ROW_TAG):
        cells = [_render_inline(cell).strip() for cell in row if cell.tag in _CELL_TAGS]
        lines.append(CELL_SEPARATOR.join(cells))
    return ROW_SEPARATOR.join(line for line in lines if line)


def _is_table(tag: str) -> bool:
    return tag.lower() == _TABLE_TAG
