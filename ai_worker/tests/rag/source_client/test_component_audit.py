import pytest

from scripts.rag.audit_mfds_component_keys import Audit, AuditError, scan


def row(serial, **changes):
    return dict(
        ITEM_SEQ="synthetic",
        TAMT_SEQ="1",
        MTRAL_SN=serial,
        MTRAL_CODE="synthetic-material",
        MTRAL_NM="synthetic-name",
        **changes,
    )


def pages(records, *, total=None):
    def fetch(page, size):
        return {
            "header": {"resultCode": "00"},
            "body": {
                "pageNo": page,
                "numOfRows": size,
                "totalCount": len(records) if total is None else total,
                "items": records[(page - 1) * size : page * size],
            },
        }

    return fetch


def test_full_scan_detects_duplicate_keys_across_pages_without_deduping():
    result = scan(pages([row("1"), row("2"), row("1", QNT="20")]), page_size=2, max_pages=3)
    assert result["record_count"] == 3
    assert result["candidate_key_duplicate_groups"] == 1
    assert result["duplicate_key_different_payload_groups"] == 1
    assert not result["observed_candidate_keys_complete_and_unique"]
    assert "synthetic-name" not in str(result)


def test_order_independent_hash_preserves_multiplicity():
    records = [row("1"), row("2"), row("3")]
    first = scan(pages(records), page_size=2, max_pages=3)
    second = scan(pages(records[::-1]), page_size=2, max_pages=3)
    assert first == second
    assert first["observed_candidate_keys_complete_and_unique"]
    third = scan(pages(records + [records[0]]), page_size=2, max_pages=3)
    assert first["order_independent_record_hash"] != third["order_independent_record_hash"]


def test_missing_keys_and_numeric_conversion_collisions_are_separate():
    audit = Audit()
    for record in (row("01"), row("1"), {"ITEM_SEQ": "synthetic"}):
        audit.add(record)
    result = audit.report()
    assert result["candidate_key_incomplete_rows"] == 1
    assert result["candidate_key_duplicate_groups"] == 0
    assert result["numeric_conversion_collision_groups"] == 1
    assert result["all_component_fields_blank_rows"] == 1


@pytest.mark.parametrize("change", ["total", "page", "size", "short"])
def test_pagination_drift_and_truncation_never_return_complete_report(change):
    base = pages([row(str(i)) for i in range(4)])

    def fetch(page, size):
        payload = base(page, size)
        if page == 2:
            body = payload["body"]
            if change == "short":
                body["items"] = body["items"][:1]
            else:
                body[{"total": "totalCount", "page": "pageNo", "size": "numOfRows"}[change]] += 1
        return payload

    with pytest.raises(ValueError):
        scan(fetch, page_size=2, max_pages=3)


def test_page_limit_is_checked_before_second_request():
    calls = []

    def fetch(page, size):
        calls.append(page)
        return pages([row("1"), row("2")], total=100)(page, size)

    with pytest.raises(AuditError) as caught:
        scan(fetch, page_size=2, max_pages=3)
    assert caught.value.counts["required_pages"] == 50
    assert calls == [1]


def test_name_code_ambiguity_is_reported_not_resolved():
    audit = Audit()
    audit.add(row("1"))
    audit.add(dict(row("2"), MTRAL_CODE="different-code"))
    audit.add(dict(row("3"), MTRAL_NM="different-name"))
    result = audit.report()
    assert result["material_codes_with_multiple_exact_names"] == 1
    assert result["exact_names_with_multiple_material_codes"] == 1


def test_live_audit_failure_does_not_expose_authenticated_url(monkeypatch, capsys):
    import sys
    from urllib.error import HTTPError

    from scripts.rag import audit_mfds_component_keys as module

    monkeypatch.setattr(sys, "argv", ["audit", "--live"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "synthetic-private-key")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    def fail(key, **kwargs):
        raise HTTPError("https://example.invalid/?serviceKey=" + key, 403, key, {}, None)

    monkeypatch.setattr(module, "fetch_payload", fail)
    assert module.main() == 1
    output = capsys.readouterr()
    assert "synthetic-private-key" not in output.out + output.err
    assert "serviceKey" not in output.out + output.err
    assert '"http_status": 403' in output.out
