from app.core.utils.idempotency import compute_key_hmac, compute_key_hmac_candidates


def test_compute_key_hmac_changes_when_hmac_key_rotates() -> None:
    raw_key = "client-generated-idempotency-key-12345"

    digest_before_rotation = compute_key_hmac(raw_key, hmac_key="old-production-secret")
    digest_after_rotation = compute_key_hmac(raw_key, hmac_key="new-production-secret")

    assert digest_before_rotation != digest_after_rotation


def test_compute_key_hmac_candidates_keeps_active_first_and_retained_versions() -> None:
    raw_key = "client-generated-idempotency-key-12345"

    candidates = compute_key_hmac_candidates(
        raw_key,
        active_hmac_key="new-production-secret",
        active_key_version="v2",
        retained_hmac_keys={"v1": "old-production-secret"},
    )

    assert [candidate.key_hmac_version for candidate in candidates] == ["v2", "v1"]
    assert candidates[0].key_hmac == compute_key_hmac(raw_key, hmac_key="new-production-secret")
    assert candidates[1].key_hmac == compute_key_hmac(raw_key, hmac_key="old-production-secret")
