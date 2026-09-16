"""#634 Task 2a: mfds-label-knowledge-chunk@1 순수 renderer/chunker 검증.

계약 `docs/contracts/proposed/post-mvp-1/knowledge-materialization-v1.md`의
"Byte-exact 렌더링 규약"과 golden vector G1~G8을 고정한다. reader, DB, receipt,
transaction을 사용하지 않는다.
"""

import hashlib
import unicodedata

import pytest

from ai_worker.tasks.rag.mfds_label_chunk_policy import (
    CHUNK_POLICY_VERSION,
    ChunkPolicyError,
    ChunkPolicyFailureReason,
    build_chunk_drafts,
    chunk_content_hash,
    normalize_chunk_text,
)
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    NN_ARTICLE_TITLES,
    parse_mfds_label_artifact,
)

_SECTION_TITLES = {"EE": "효능효과", "UD": "용법용량", "NB": "사용상의주의사항", "NN": "e약은요 정보"}


def _doc(section: str, inner: str) -> bytes:
    return f'<DOC type="{section}" title="{_SECTION_TITLES[section]}">{inner}</DOC>'.encode()


def _drafts(section: str, inner: str, *, policy: str = CHUNK_POLICY_VERSION):
    parsed = parse_mfds_label_artifact(_doc(section, inner), section)
    return build_chunk_drafts(parsed, chunk_policy_version=policy)


def _nn_inner(*, empty_index: int | None = None) -> str:
    return "".join(
        f'<ARTICLE title="{title}"><PARAGRAPH>{"" if index == empty_index else "합성 본문"}</PARAGRAPH></ARTICLE>'
        for index, title in enumerate(NN_ARTICLE_TITLES)
    )


_G1 = '<ARTICLE title="효능"><PARAGRAPH>본문 첫째 줄</PARAGRAPH></ARTICLE>'
_G2 = (
    '<ARTICLE title="성인"><PARAGRAPH>상위 본문</PARAGRAPH>'
    '<ARTICLE title="신기능 저하"><PARAGRAPH>하위 본문</PARAGRAPH></ARTICLE></ARTICLE>'
)
_G3 = '<ARTICLE title="효능"><PARAGRAPH>앞<b>강조</b>사이<i>기울임</i>뒤</PARAGRAPH></ARTICLE>'
_G4 = '<ARTICLE title="주의"><PARAGRAPH>첫 문단</PARAGRAPH><PARAGRAPH>둘째 문단</PARAGRAPH></ARTICLE>'
_G5 = (
    "<PARAGRAPH>머리말 1</PARAGRAPH><PARAGRAPH>머리말 2</PARAGRAPH>"
    '<ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE>'
)
_G6 = (
    '<ARTICLE title="용량"><PARAGRAPH>표 참조'
    "<table><caption>1일 용량</caption><tr><th>구분</th><th>용량</th></tr>"
    "<tr><td>단위</td><td>mg</td></tr><tr><td>성인</td><td>5</td></tr>"
    "<tr><td>비고</td><td>식후</td></tr></table></PARAGRAPH></ARTICLE>"
)
_G7 = '<ARTICLE title="효능만 있는 항목"></ARTICLE><ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE>'
_G8 = (
    '<ARTICLE title="효능"><PARAGRAPH>'
    + unicodedata.normalize("NFD", "효능")
    + "  \r\n\r\n\r\n둘째 줄  </PARAGRAPH></ARTICLE>"
)

# 계약 "Byte-exact 렌더링 규약"의 golden vector 표와 1:1로 대응한다.
GOLDEN = [
    ("G1", "EE", _G1, 0, "# 효능\n\n본문 첫째 줄", "3fa3c97b576b011666974e758257c94362eb529c5224f1f4859a0e53c16db439"),
    (
        "G2",
        "UD",
        _G2,
        0,
        "# 성인\n\n상위 본문\n\n## 신기능 저하\n\n하위 본문",
        "25867d2ff87b6c698cdbbe62ecb485137d364a0153bc4dc1168795ec6ea31c8d",
    ),
    (
        "G3",
        "EE",
        _G3,
        0,
        "# 효능\n\n앞강조사이기울임뒤",
        "fb6230b1bfd854615f7906f7846211faa7345dacad4f12ccfc3b14c9bc9f91b0",
    ),
    (
        "G4",
        "NB",
        _G4,
        0,
        "# 주의\n\n첫 문단\n\n둘째 문단",
        "51a413105e8a9bff0668ad77839ed56be4beeeba163711f1401794f7a2f33856",
    ),
    ("G5", "EE", _G5, 0, "머리말 1\n\n머리말 2", "1d4f729c07f1b7d7f71e477d74efd742bf40dfca48514a940dee600b38a7de35"),
    ("G5", "EE", _G5, 1, "# 효능\n\n본문", "f73db0621f6d87acfe4ef930458d995cb2b430c07d61db1b0e0e71045a4cd473"),
    (
        "G6",
        "UD",
        _G6,
        0,
        "# 용량\n\n표 참조\n\n1일 용량\n구분\t용량\n단위\tmg\n성인\t5\n비고\t식후",
        "148e4531bfff30d04bdab89022762685e05710f7a13098d8019975624b554011",
    ),
    ("G7", "EE", _G7, 0, "# 효능만 있는 항목", "af1cff2ddbb08a042a407897df483ae946620ec1665d115063ea9b273560fc39"),
    ("G7", "EE", _G7, 1, "# 효능\n\n본문", "f73db0621f6d87acfe4ef930458d995cb2b430c07d61db1b0e0e71045a4cd473"),
    (
        "G8",
        "EE",
        _G8,
        0,
        "# 효능\n\n효능\n\n둘째 줄",
        "d1263414708b6f50578042a193e28dbfe761af5c8986848d6cc717f76ebb3ccd",
    ),
]


@pytest.mark.parametrize(("name", "section", "inner", "chunk_index", "expected_text", "expected_hash"), GOLDEN)
def test_golden_vector_text_and_hash(
    name: str,
    section: str,
    inner: str,
    chunk_index: int,
    expected_text: str,
    expected_hash: str,
) -> None:
    """chunk_text 전체 문자열과 SHA-256을 동시에 고정한다.

    hash만 비교하면 회귀 원인을 찾을 수 없고, 문자열만 비교하면 계약 값과의
    결속이 끊어진다.
    """
    draft = _drafts(section, inner)[chunk_index]
    assert draft.chunk_text == expected_text, name
    assert draft.content_hash == expected_hash, name
    assert draft.content_hash == hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
    assert draft.chunk_index == chunk_index
    assert draft.normalization_version == CHUNK_POLICY_VERSION


def test_golden_vectors_are_nfc() -> None:
    for name, _section, _inner, _index, expected_text, _hash in GOLDEN:
        assert unicodedata.normalize("NFC", expected_text) == expected_text, name


def test_golden_g5_and_g7_produce_expected_chunk_counts() -> None:
    assert len(_drafts("EE", _G5)) == 2
    assert len(_drafts("EE", _G7)) == 2


def test_nfd_and_nfc_inputs_produce_identical_chunk_text() -> None:
    decomposed = unicodedata.normalize("NFD", "효능")
    assert decomposed != unicodedata.normalize("NFC", decomposed)
    nfd = _drafts("EE", f'<ARTICLE title="효능"><PARAGRAPH>{decomposed}</PARAGRAPH></ARTICLE>')
    nfc = _drafts("EE", '<ARTICLE title="효능"><PARAGRAPH>효능</PARAGRAPH></ARTICLE>')
    assert nfd[0].chunk_text == nfc[0].chunk_text


def test_nfd_and_nfc_inputs_produce_identical_content_hash() -> None:
    decomposed = unicodedata.normalize("NFD", "효능")
    nfd = _drafts("EE", f'<ARTICLE title="효능"><PARAGRAPH>{decomposed}</PARAGRAPH></ARTICLE>')
    nfc = _drafts("EE", '<ARTICLE title="효능"><PARAGRAPH>효능</PARAGRAPH></ARTICLE>')
    assert nfd[0].content_hash == nfc[0].content_hash


def test_newline_representation_does_not_change_chunk_text() -> None:
    spaced = _drafts("EE", '<ARTICLE title="효능"><PARAGRAPH>첫 줄  \r\n\r\n\r\n둘째 줄  </PARAGRAPH></ARTICLE>')
    plain = _drafts("EE", '<ARTICLE title="효능"><PARAGRAPH>첫 줄\n\n둘째 줄</PARAGRAPH></ARTICLE>')
    assert spaced[0].chunk_text == plain[0].chunk_text
    assert spaced[0].content_hash == plain[0].content_hash


def test_normalization_step_order_is_fixed() -> None:
    """빈 줄 축약이 줄별 trailing 제거보다 뒤에 와야 G8 결과가 나온다."""
    raw = "# 효능\n\n효능  \r\n   \r\n\r\n둘째 줄  "
    assert normalize_chunk_text(raw) == "# 효능\n\n효능\n\n둘째 줄"


def test_parser_seam_output_is_not_normalized() -> None:
    """정규화는 이 계층의 책임이고 parser seam은 원문 구조를 보존한다."""
    decomposed = unicodedata.normalize("NFD", "효능")
    parsed = parse_mfds_label_artifact(
        _doc("EE", f'<ARTICLE title="효능"><PARAGRAPH>{decomposed}  </PARAGRAPH></ARTICLE>'), "EE"
    )
    seam_text = "".join(next(iter(parsed.root.iter("PARAGRAPH"))).itertext())
    assert decomposed in seam_text
    assert seam_text.endswith("  ")

    drafts = build_chunk_drafts(parsed, chunk_policy_version=CHUNK_POLICY_VERSION)
    assert unicodedata.normalize("NFC", drafts[0].chunk_text) == drafts[0].chunk_text
    assert not drafts[0].chunk_text.endswith(" ")


def test_content_hash_uses_normalized_chunk_text() -> None:
    draft = _drafts("EE", _G8)[0]
    assert draft.content_hash == chunk_content_hash(draft.chunk_text)


def test_unsupported_chunk_policy_version_rejected() -> None:
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("EE", _G1, policy="mfds-label-knowledge-chunk@2")
    assert error.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED


def test_chunk_renderer_empty_text_rejected() -> None:
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("EE", '<ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE><ARTICLE title=""> </ARTICLE>')
    assert error.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED


def test_nn_official_empty_article_rejected() -> None:
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("NN", _nn_inner(empty_index=2))
    assert error.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED


def test_nn_complete_document_is_accepted() -> None:
    drafts = _drafts("NN", _nn_inner())
    assert len(drafts) == len(NN_ARTICLE_TITLES)
    assert drafts[0].chunk_text.startswith(f"# {NN_ARTICLE_TITLES[0]}")


def test_error_exposes_only_reason_value() -> None:
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("NN", _nn_inner(empty_index=0))
    assert str(error.value) == "CHUNK_POLICY_UNSUPPORTED"


def test_no_synthetic_text_added() -> None:
    drafts = _drafts("EE", _G7)
    joined = "\n".join(draft.chunk_text for draft in drafts)
    for marker in ("내용 없음", "요약", "생략", "section", "EE"):
        assert marker not in joined


def test_repeated_execution_yields_identical_chunk_drafts() -> None:
    """draft에는 is_exact_replay가 없으므로 전 필드 동일성을 요구할 수 있다."""
    assert _drafts("UD", _G6) == _drafts("UD", _G6)


def test_chunk_index_is_zero_based_sequential() -> None:
    drafts = _drafts("NN", _nn_inner())
    assert [draft.chunk_index for draft in drafts] == list(range(len(drafts)))


def test_chunk_text_is_hidden_from_repr() -> None:
    draft = _drafts("EE", _G1)[0]
    rendered = repr(draft)
    assert "본문 첫째 줄" not in rendered
    assert "chunk_text" not in rendered
    assert "chunk_index=0" in rendered


_TABLE = "<table><tr><td>a</td><td>b</td></tr></table>"


def test_table_renders_as_block_regardless_of_block_position() -> None:
    """ARTICLE 직하와 PARAGRAPH 내부의 표가 같은 chunk_text를 만든다.

    위치에 따라 `\n` / `\n\n`으로 갈리면 같은 표의 content_hash가 달라진다.
    """
    under_article = _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>x</PARAGRAPH>{_TABLE}</ARTICLE>')
    in_paragraph = _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>x{_TABLE}</PARAGRAPH></ARTICLE>')
    assert under_article[0].chunk_text == "# T\n\nx\n\na\tb"
    assert under_article[0].chunk_text == in_paragraph[0].chunk_text
    assert under_article[0].content_hash == in_paragraph[0].content_hash


@pytest.mark.parametrize(
    "inner",
    [
        f'<ARTICLE title="T"><PARAGRAPH>x<b>y{_TABLE}z</b></PARAGRAPH></ARTICLE>',
        f'<ARTICLE title="T"><PARAGRAPH>x<b><i>{_TABLE}</i></b></PARAGRAPH></ARTICLE>',
        f'<ARTICLE title="T"><PARAGRAPH>x<table><tr><td>{_TABLE}</td></tr></table></PARAGRAPH></ARTICLE>',
    ],
)
def test_table_in_inline_position_is_rejected(inner: str) -> None:
    """인라인 위치의 표는 블록으로 표현할 수 없으므로 fail-closed 한다.

    인라인으로 이어 붙이면 중첩 깊이에 따라 같은 표의 content_hash가 달라진다.
    """
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("EE", inner)
    assert error.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED


def test_table_uppercase_and_mixed_case_preserves_content() -> None:
    uppercase_table = "<TABLE><TR><TD>A</TD><TD>B</TD></TR></TABLE>"
    mixed_table = "<Table><Tr><Td>A</Td><Th>B</Th></Tr></Table>"
    lower_table = "<table><tr><td>A</td><td>B</td></tr></table>"

    draft_upper = _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>{uppercase_table}</PARAGRAPH></ARTICLE>')[0]
    draft_mixed = _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>{mixed_table}</PARAGRAPH></ARTICLE>')[0]
    draft_lower = _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>{lower_table}</PARAGRAPH></ARTICLE>')[0]

    assert draft_upper.chunk_text == "# T\n\nA\tB"
    assert draft_mixed.chunk_text == "# T\n\nA\tB"
    assert draft_upper.chunk_text == draft_lower.chunk_text
    assert draft_upper.content_hash == draft_lower.content_hash
    assert draft_mixed.content_hash == draft_lower.content_hash


@pytest.mark.parametrize(
    "invalid_table_inner",
    [
        "<table>주석<tr><td>A</td><td>B</td></tr></table>",  # table direct text
        "<table><tr><td>A</td><td>B</td></tr>주석</table>",  # row tail text
        "<table><caption>설명</caption>주석<tr><td>A</td><td>B</td></tr></table>",  # caption tail text
        "<table><tr>셀밖텍스트<td>A</td><td>B</td></tr></table>",  # row direct text
        "<table><tr><td>A</td>셀밖tail<td>B</td></tr></table>",  # cell tail text
        "<table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>",  # unsupported wrapper tbody
        "<table><thead><tr><th>A</th><th>B</th></tr></thead></table>",  # unsupported wrapper thead
        "<table><tr><td>A</td><p>unsupported</p></tr></table>",  # unsupported child in row
    ],
)
def test_table_silent_omission_is_rejected(invalid_table_inner: str) -> None:
    with pytest.raises(ChunkPolicyError) as error:
        _drafts("EE", f'<ARTICLE title="T"><PARAGRAPH>{invalid_table_inner}</PARAGRAPH></ARTICLE>')
    assert error.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED
