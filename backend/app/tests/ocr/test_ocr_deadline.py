"""OCR 동기 경로의 전체 deadline과 호출 직전 예산 재계산을 검증합니다.

asyncio.timeout 취소로 deadline을 강제하면 CancelledError가 BaseException이라
execute_ocr의 except Exception에 잡히지 않아 OCR Job이 PROCESSING으로 영구 잔존합니다.
그래서 예산을 명시적으로 검사하고 FAILED로 전이하는지 확인합니다.
"""

from app.services.ocr_engine import OcrDeadline


def test_deadline_excludes_response_margin_from_provider_budget() -> None:
    deadline = OcrDeadline.start(total_seconds=60.0, response_margin_seconds=5.0)

    # Provider 경로 예산은 D - M을 넘지 않습니다.
    assert 0 < deadline.remaining() <= 55.0


def test_timeout_for_returns_provider_limit_when_budget_is_sufficient() -> None:
    deadline = OcrDeadline.start(total_seconds=60.0, response_margin_seconds=5.0)

    assert deadline.timeout_for(20.0) == 20.0


def test_timeout_for_is_capped_by_remaining_budget() -> None:
    # 남은 예산이 개별 상한보다 작으면 남은 예산을 사용합니다.
    deadline = OcrDeadline.start(total_seconds=10.0, response_margin_seconds=5.0)

    assert deadline.timeout_for(20.0) < 20.0


def test_exhausted_deadline_has_no_remaining_budget() -> None:
    deadline = OcrDeadline.start(total_seconds=5.0, response_margin_seconds=5.0)

    assert deadline.remaining() == 0.0
    assert deadline.timeout_for(20.0) == 0.0
