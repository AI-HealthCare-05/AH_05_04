import pytest

from scripts.rag.probe_mfds_component_fields import records_from, summarize


def test_candidate_key_requires_total_amount_and_component_serial():
    first = {"ITEM_SEQ": "synthetic-item", "TAMT_SEQ": "1", "MTRAL_SN": "01", "MTRAL_CODE": "synthetic-material"}
    second = dict(first, TAMT_SEQ="2")
    third = dict(first)
    report = summarize([first, second, third, {"ITEM_SEQ": "synthetic-item"}])
    assert report["candidate_key_duplicate_groups"] == 1
    assert report["candidate_key_incomplete_rows"] == 1
    assert "synthetic-item" not in str(report)
    assert "synthetic-material" not in str(report)
    assert report == summarize([third, first, second, {"ITEM_SEQ": "synthetic-item"}])


@pytest.mark.parametrize("items", [[{"ITEM_SEQ": "synthetic"}], {"item": [{"ITEM_SEQ": "synthetic"}]}])
def test_probe_reads_documented_envelope_without_synthesizing_fields(items):
    assert records_from({"header": {"resultCode": "00"}, "body": {"items": items}}) == [{"ITEM_SEQ": "synthetic"}]


def test_provider_error_message_is_not_propagated():
    with pytest.raises(ValueError, match="^PROVIDER_REJECTED_REQUEST$"):
        records_from({"header": {"resultCode": "30", "resultMsg": "do-not-echo-serviceKey"}})


def test_live_failure_never_prints_key_or_authenticated_url(monkeypatch, capsys):
    import sys
    from urllib.error import HTTPError

    from scripts.rag import probe_mfds_component_fields as probe

    monkeypatch.setattr(sys, "argv", ["probe", "--live"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.getpass, "getpass", lambda prompt: "private-key-not-for-output")

    def fail(key):
        raise HTTPError("https://example.invalid/?serviceKey=" + key, 403, key, {}, None)

    monkeypatch.setattr(probe, "fetch_sample", fail)
    assert probe.main() == 1
    output = capsys.readouterr()
    assert "private-key-not-for-output" not in output.out + output.err
    assert "serviceKey" not in output.out + output.err
    assert '"http_status": 403' in output.out
