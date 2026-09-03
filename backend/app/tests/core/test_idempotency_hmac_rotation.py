from app.core.utils.idempotency import compute_key_hmac


def test_compute_key_hmac_changes_when_hmac_key_rotates() -> None:
    """PR #215 리뷰(권가빈)에서 지적된 rotation 경계를 고정하는 회귀 테스트입니다.

    `find_async_idempotency_record()`는 현재 active `IDEMPOTENCY_HMAC_KEY` 하나로 계산한
    digest로만 조회합니다(key_hmac_version별 조회 미지원, 후속 #235). 그래서 이 키를
    `IDEMPOTENCY_RECORD_TTL_DAYS`(기본 7일)가 지나기 전에 교체하면, 교체 이전 레코드에 대한
    재시도가 새 digest와 맞지 않아 중복으로 인식되지 못합니다. 이 테스트는 그 전제인 "같은
    원문 key라도 hmac_key가 바뀌면 digest가 달라진다"를 고정해서, 향후 누군가 실수로 여러
    key를 같은 digest로 매핑하는 회귀를 만들지 않도록 합니다.
    """
    raw_key = "client-generated-idempotency-key-12345"

    digest_before_rotation = compute_key_hmac(raw_key, hmac_key="old-production-secret")
    digest_after_rotation = compute_key_hmac(raw_key, hmac_key="new-production-secret")

    assert digest_before_rotation != digest_after_rotation
