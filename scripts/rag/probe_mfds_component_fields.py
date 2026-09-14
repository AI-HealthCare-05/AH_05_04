"""D-04 소량 실측: 키는 숨김 입력, 응답 원문 대신 필드/후보키 통계만 출력한다."""

import argparse
import getpass
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

URL = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtMcpnDtlInq07"
FIELDS = ("ITEM_SEQ", "MTRAL_CODE", "MTRAL_SN", "TAMT_SEQ", "QNT", "INGD_UNIT_CD", "CPNT_CTNT_CONT")
KEY_FIELDS = ("ITEM_SEQ", "TAMT_SEQ", "MTRAL_SN")
MAX_BYTES = 1_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def records_from(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("UNEXPECTED_RESPONSE_SHAPE")
    header = payload.get("header")
    body = payload.get("body")
    if not isinstance(header, dict) or header.get("resultCode") != "00":
        raise ValueError("PROVIDER_REJECTED_REQUEST")
    if not isinstance(body, dict):
        raise ValueError("UNEXPECTED_RESPONSE_SHAPE")
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
        raise ValueError("EMPTY_OR_INVALID_SAMPLE")
    return items


def summarize(records: list[dict]) -> dict:
    def present(value):
        return isinstance(value, str) and bool(value.strip())

    complete = [tuple(row.get(field) for field in KEY_FIELDS) for row in records]
    usable = [key for key in complete if all(present(value) for value in key)]
    counts = Counter(usable)
    return {
        "record_count": len(records),
        "field_statistics": {
            field: {
                "present_nonblank_string": sum(present(row.get(field)) for row in records),
                "types": sorted({type(row.get(field)).__name__ for row in records}),
            }
            for field in FIELDS
        },
        "candidate_key_fields": list(KEY_FIELDS),
        "candidate_key_incomplete_rows": len(records) - len(usable),
        "candidate_key_duplicate_groups": sum(count > 1 for count in counts.values()),
        "sample_order_independent_sha256": hashlib.sha256(
            json.dumps(
                sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in records),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    }


def fetch_payload(key: str, *, page_no: int = 1, page_size: int = 50) -> dict:
    parameters = {"serviceKey": key, "type": "json", "pageNo": page_no, "numOfRows": page_size}
    request = Request(
        f"{URL}?{urlencode(parameters)}", headers={"Accept": "application/json", "Accept-Encoding": "identity"}
    )
    # 인증키를 redirect 대상이나 환경 proxy에 넘기지 않는다.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("UNEXPECTED_CONTENT_TYPE")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("RESPONSE_TOO_LARGE")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("UNEXPECTED_RESPONSE_SHAPE")
    return payload


def fetch_sample(key: str) -> dict:
    return summarize(records_from(fetch_payload(key)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="첫 페이지 50건을 두 번 조회한다. 전체 검증이 아니다.")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"status": "NOT_RUN", "reason": "LIVE_OPT_IN_REQUIRED"}))
        return 0
    if not sys.stdin.isatty():
        print(json.dumps({"status": "NOT_RUN", "reason": "HIDDEN_TTY_INPUT_REQUIRED"}))
        return 1
    try:
        key = getpass.getpass("MFDS Decoding key (화면·파일에 기록하지 않음): ").strip()
        if not key:
            raise ValueError("EMPTY_KEY")
        first = fetch_sample(key)
        second = fetch_sample(key)
    except HTTPError as error:
        result = {"status": "FAILED", "reason": "HTTP_ERROR", "http_status": error.code}
    except (URLError, TimeoutError, OSError, ValueError):
        # 예외 문자열/URL/응답 메시지에는 키가 포함될 수 있으므로 출력하지 않는다.
        result = {"status": "FAILED", "reason": "NETWORK_OR_RESPONSE_VALIDATION_FAILED"}
    except (KeyboardInterrupt, EOFError):
        result = {"status": "CANCELLED"}
    else:
        result = {
            "status": "SAMPLED_NOT_VERIFIED",
            "operation": "getDrugPrdtMcpnDtlInq07",
            "observed_at": datetime.now(UTC).isoformat(),
            "scope": "pageNo=1,numOfRows=50,two_requests",
            "first": first,
            "second": second,
            "same_sample_on_repeat": first["sample_order_independent_sha256"]
            == second["sample_order_independent_sha256"],
            "limitations": "전체 고유성·재수집 안정성·공식 Ingredient Identity 승인 증빙이 아님",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "SAMPLED_NOT_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
