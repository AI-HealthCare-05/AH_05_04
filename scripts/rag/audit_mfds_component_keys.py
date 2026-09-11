"""Read-only, bounded MFDS full-page audit. Never creates a verified Source Receipt."""

import argparse
import getpass
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from math import ceil
from urllib.error import HTTPError, URLError

from scripts.rag.probe_mfds_component_fields import KEY_FIELDS, fetch_payload, records_from


def present(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def count_value(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError("INVALID_PAGINATION_METADATA")


def serial_kind(value):
    if not present(value):
        return "missing"
    if not (value.isascii() and value.isdecimal()):
        return "non_ascii_digit_string"
    if len(value) > 1 and value.startswith("0"):
        return "leading_zero"
    return "ascii_digit_string"


class AuditError(ValueError):
    def __init__(self, reason, **counts):
        self.reason = reason
        self.counts = counts
        super().__init__(reason)


class Audit:
    def __init__(self):
        self.rows = 0
        self.hashes = []
        self.keys = Counter()
        self.key_hashes = defaultdict(set)
        self.missing = Counter()
        self.numeric_keys = defaultdict(set)
        self.serials = {field: Counter() for field in ("TAMT_SEQ", "MTRAL_SN")}
        self.materials = Counter()
        self.code_names = defaultdict(set)
        self.name_codes = defaultdict(set)
        self.blank_components = 0

    def add(self, row):
        self.rows += 1
        row_hash = digest(row)
        self.hashes.append(row_hash)
        missing = tuple(field for field in KEY_FIELDS if not present(row.get(field)))
        if missing:
            self.missing[missing] += 1
        else:
            key = tuple(row[field] for field in KEY_FIELDS)
            self.keys[key] += 1
            self.key_hashes[key].add(row_hash)
            if all(value.isascii() and value.isdecimal() for value in key[1:]):
                self.numeric_keys[(key[0], int(key[1]), int(key[2]))].add(key)
        for field, stats in self.serials.items():
            stats[serial_kind(row.get(field))] += 1
        if all(not present(row.get(field)) for field in ("MTRAL_CODE", "MTRAL_SN", "TAMT_SEQ", "QNT", "INGD_UNIT_CD")):
            self.blank_components += 1
        code, name, product = (row.get(f) for f in ("MTRAL_CODE", "MTRAL_NM", "ITEM_SEQ"))
        if present(code):
            if present(product):
                self.materials[(product, code)] += 1
            if present(name):
                self.code_names[code].add(name)
                self.name_codes[name].add(code)

    def report(self):
        return {
            "record_count": self.rows,
            "candidate_key_fields": list(KEY_FIELDS),
            "candidate_key_incomplete_rows": sum(self.missing.values()),
            "missing_key_patterns": [{"fields": list(k), "count": n} for k, n in sorted(self.missing.items())],
            "candidate_key_duplicate_groups": sum(n > 1 for n in self.keys.values()),
            "duplicate_key_different_payload_groups": sum(len(v) > 1 for v in self.key_hashes.values()),
            "observed_candidate_keys_complete_and_unique": bool(self.rows)
            and not self.missing
            and all(n == 1 for n in self.keys.values()),
            "all_component_fields_blank_rows": self.blank_components,
            "serial_statistics": self.serials,
            "numeric_conversion_collision_groups": sum(len(v) > 1 for v in self.numeric_keys.values()),
            "repeated_product_material_groups": sum(n > 1 for n in self.materials.values()),
            "material_codes_with_multiple_exact_names": sum(len(v) > 1 for v in self.code_names.values()),
            "exact_names_with_multiple_material_codes": sum(len(v) > 1 for v in self.name_codes.values()),
            "order_independent_record_hash": digest(sorted(self.hashes)),
        }


def scan(fetch, *, page_size, max_pages, progress=None):
    audit = Audit()
    total = None
    pages = None
    page = 1
    while pages is None or page <= pages:
        payload = fetch(page, page_size)
        rows = records_from(payload)
        body = payload["body"]
        observed_total = count_value(body.get("totalCount"))
        if count_value(body.get("pageNo")) != page or count_value(body.get("numOfRows")) != page_size:
            raise ValueError("PAGINATION_MISMATCH")
        if total is None:
            total = observed_total
            pages = ceil(total / page_size)
            if not total or pages > max_pages:
                raise AuditError(
                    "EMPTY_DATASET_OR_PAGE_LIMIT_EXCEEDED",
                    required_pages=pages,
                    advertised_total_count=total,
                    planned_requests=pages * 2,
                )
        if observed_total != total:
            raise ValueError("TOTAL_COUNT_CHANGED")
        if len(rows) != min(page_size, total - (page - 1) * page_size):
            raise ValueError("PAGE_RECORD_COUNT_MISMATCH")
        for row in rows:
            audit.add(row)
        if progress:
            progress(page, pages)
        page += 1
    return {"advertised_total_count": total, "pages_read": pages, **audit.report()}


def run_scans(key, args, results):
    def fetch(page, size):
        time.sleep(0.2)
        return fetch_payload(key, page_no=page, page_size=size)

    def progress(page, total):
        if page == 1 or page % 25 == 0 or page == total:
            print(f"Pass {len(results) + 1}/2: {page}/{total} pages", file=sys.stderr)

    for _ in range(2):
        results.append(scan(fetch, page_size=args.page_size, max_pages=args.max_pages, progress=progress))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-pages", type=int, default=1000, help="각 순회 최대 페이지 수. 초과 시 첫 페이지 후 중단")
    parser.add_argument("--page-size", type=int, choices=(50, 100), default=100)
    args = parser.parse_args()
    if not args.live or not sys.stdin.isatty() or not 1 <= args.max_pages <= 5000:
        print(json.dumps({"status": "NOT_RUN", "reason": "LIVE_TTY_AND_BOUNDED_PAGE_LIMIT_REQUIRED"}))
        return 1
    results = []
    try:
        key = getpass.getpass("MFDS Decoding key (숨김 입력·저장 안 함): ").strip()
        if not key:
            raise ValueError("EMPTY_KEY")

        run_scans(key, args, results)
    except AuditError as error:
        result = {"status": "FAILED", "reason": error.reason, **error.counts}
    except HTTPError as error:
        result = {"status": "FAILED", "reason": "HTTP_ERROR", "http_status": error.code}
    except (URLError, TimeoutError, OSError, ValueError):
        # Provider exceptions may contain authenticated URLs. Never print their text.
        result = {"status": "FAILED", "reason": "NETWORK_OR_PAGINATION_VALIDATION_FAILED"}
    except (KeyboardInterrupt, EOFError):
        result = {"status": "CANCELLED"}
    else:
        result = {
            "status": "FULL_PAGES_OBSERVED_NOT_VERIFIED",
            "same_records_on_repeat": results[0]["order_independent_record_hash"]
            == results[1]["order_independent_record_hash"],
            "official_ingredient_mapping": "NOT_VERIFIED",
            "source_order_semantics": "NOT_VERIFIED",
            "limitations": "페이지 순회는 동일 시점 Snapshot을 보장하지 않음. 장기 키 안정성·공식 Identity·순서 승인·Source Receipt 증빙이 아님.",
        }
    result.update(operation="getDrugPrdtMcpnDtlInq07", observed_at=datetime.now(UTC).isoformat(), passes=results)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "FULL_PAGES_OBSERVED_NOT_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
