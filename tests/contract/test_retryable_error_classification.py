"""문서의 retryable 판정과 Worker 코드의 RETRYABLE_FAILURE_CODES가 일치하는지 확인합니다.

ai_worker/core/retry.py는 재시도 여부를 코드로 판정하지만,
backend-error-response.md에는 그 기준이 문서화되어 있지 않아
클라이언트가 코드별 재시도 가능 여부를 알 수 없었습니다.

한쪽만 바꾸면 이 테스트가 실패하도록 두 정의를 묶습니다.
"""

import re
from pathlib import Path

from ai_worker.core.retry import ALL_FAILURE_CODES, RETRYABLE_FAILURE_CODES

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ERROR_DOC_PATH = PROJECT_ROOT / "docs" / "contracts" / "current" / "backend-error-response.md"
OCR_STATUS_DOC_PATH = PROJECT_ROOT / "docs" / "contracts" / "current" / "ocr-job-status.md"

_FAILURE_CODE_ROW = re.compile(
    r"^\|\s*`(?P<code>[A-Z_]+)`\s*\|\s*(?P<retryable>true|false)\s*\|",
    re.MULTILINE,
)
_PUBLIC_CODE_ROW = re.compile(
    r"^\|\s*`(?P<code>[A-Z_]+)`\s*\|\s*(?P<http>\d{3}|\(원본\))\s*\|\s*(?P<retryable>true|false)\s*\|",
    re.MULTILINE,
)


def _section(text: str, heading: str) -> str:
    """### 소제목 아래 다음 소제목 전까지의 본문을 돌려줍니다."""
    start = text.index(heading) + len(heading)
    remainder = text[start:]
    end = remainder.find("\n### ")
    return remainder if end == -1 else remainder[:end]


def _documented_failure_codes() -> dict[str, bool]:
    text = ERROR_DOC_PATH.read_text(encoding="utf-8")
    section = _section(text, "### Worker `FailureCode` 매핑")
    return {match.group("code"): match.group("retryable") == "true" for match in _FAILURE_CODE_ROW.finditer(section)}


def _documented_public_codes() -> dict[str, bool]:
    text = ERROR_DOC_PATH.read_text(encoding="utf-8")
    section = _section(text, "### 공개 오류 코드")
    return {match.group("code"): match.group("retryable") == "true" for match in _PUBLIC_CODE_ROW.finditer(section)}


def test_worker_failure_code_retryable_matches_implementation() -> None:
    documented = _documented_failure_codes()

    assert documented, "문서의 Worker FailureCode 매핑표를 파싱하지 못했습니다."

    documented_retryable = {code for code, retryable in documented.items() if retryable}

    assert documented_retryable == set(RETRYABLE_FAILURE_CODES), (
        "문서의 retryable FailureCode와 ai_worker/core/retry.py의 "
        "RETRYABLE_FAILURE_CODES가 다릅니다. 두 정의를 같은 PR에서 갱신하세요."
    )


def test_all_failure_codes_are_documented() -> None:
    documented = set(_documented_failure_codes())

    assert documented == set(ALL_FAILURE_CODES), (
        "Worker FailureCode 전체가 문서에 등록되어야 합니다. "
        f"문서에만 있음: {documented - set(ALL_FAILURE_CODES)}, "
        f"코드에만 있음: {set(ALL_FAILURE_CODES) - documented}"
    )


def test_ocr_job_status_failure_codes_match_public_classification() -> None:
    public_codes = _documented_public_codes()

    assert public_codes, "문서의 공개 오류 코드 판정표를 파싱하지 못했습니다."

    ocr_status_text = OCR_STATUS_DOC_PATH.read_text(encoding="utf-8")
    ocr_codes = {
        match.group("code"): match.group("retryable") == "true"
        for match in re.finditer(
            r"^\|\s*`(?P<code>OCR_[A-Z_]+)`\s*\|[^|]*\|\s*`\d{3}`\s*\|\s*(?P<retryable>true|false)\s*\|",
            ocr_status_text,
            re.MULTILINE,
        )
    }

    assert ocr_codes, "ocr-job-status.md의 실패 코드 표를 파싱하지 못했습니다."

    for code, retryable in ocr_codes.items():
        assert code in public_codes, f"{code}가 공개 오류 코드 판정표에 없습니다."
        assert public_codes[code] == retryable, (
            f"{code}의 retryable 판정이 두 문서에서 다릅니다: "
            f"backend-error-response={public_codes[code]}, ocr-job-status={retryable}"
        )
