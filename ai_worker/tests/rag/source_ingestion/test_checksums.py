"""원본 바이트 checksum의 정확성과 청크 경계를 검증합니다."""

from ai_worker.tasks.rag.source_ingestion.checksums import raw_checksum


def test_raw_checksum_matches_known_sha256() -> None:
    assert raw_checksum([b"abc"]) == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_raw_checksum_is_independent_of_chunk_boundaries() -> None:
    expected = raw_checksum([b"abc"])

    assert raw_checksum([b"a", b"bc"]) == expected
    assert raw_checksum([b"ab", b"c"]) == expected
    assert raw_checksum(iter([b"a", b"", b"b", b"c"])) == expected


def test_raw_checksum_preserves_original_bytes() -> None:
    assert raw_checksum([b"abc"]) != raw_checksum([b"abc\n"])
    assert raw_checksum([b"a", b"bc"]) != raw_checksum([b"bc", b"a"])


def test_raw_checksum_supports_empty_input() -> None:
    assert raw_checksum([]) == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert raw_checksum([b""]) == raw_checksum([])
