import argparse
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

HASH_ALGORITHM = "sha256"
CANONICALIZATION = "sorted-keys-compact-utf8-excluding-generated_at-and-receipt_hash-v1"
EXCLUDED_TOP_LEVEL_FIELDS = frozenset(
    {
        "generated_at",
        "receipt_hash",
    }
)


def load_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(receipt, dict):
        raise ValueError("Receipt root must be a JSON object.")

    return receipt


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    canonical_payload = {key: value for key, value in receipt.items() if key not in EXCLUDED_TOP_LEVEL_FIELDS}

    return json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def calculate_receipt_hash(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest()


def receipt_hash_metadata(digest: str) -> dict[str, str]:
    return {
        "algorithm": HASH_ALGORITHM,
        "canonicalization": CANONICALIZATION,
        "value": digest,
    }


def write_receipt_hash(path: Path) -> str:
    receipt = load_receipt(path)
    digest = calculate_receipt_hash(receipt)
    receipt["receipt_hash"] = receipt_hash_metadata(digest)

    path.write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return digest


def verify_receipt_hash(path: Path) -> str:
    receipt = load_receipt(path)
    metadata = receipt.get("receipt_hash")

    if not isinstance(metadata, dict):
        raise ValueError("Receipt hash metadata is missing.")

    if metadata.get("algorithm") != HASH_ALGORITHM:
        raise ValueError("Unsupported Receipt hash algorithm.")

    if metadata.get("canonicalization") != CANONICALIZATION:
        raise ValueError("Unsupported Receipt canonicalization rule.")

    stored_digest = metadata.get("value")
    if not isinstance(stored_digest, str):
        raise ValueError("Receipt hash value is missing.")

    calculated_digest = calculate_receipt_hash(receipt)
    if not hmac.compare_digest(stored_digest, calculated_digest):
        raise ValueError("Receipt hash does not match canonical content.")

    return calculated_digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Calculate and store the canonical Receipt hash.",
    )
    args = parser.parse_args()

    digest = write_receipt_hash(args.receipt) if args.write else verify_receipt_hash(args.receipt)
    print(digest)


if __name__ == "__main__":
    main()
