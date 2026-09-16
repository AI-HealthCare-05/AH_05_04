"""Offline XML Receipt issuance. Never registers catalog rows or marks profiles READY."""

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    ACQUISITION_EVIDENCE_VERSION,
    CANONICALIZATION_SPEC_VERSION,
    MAX_XML_BYTES,
    NORMALIZATION_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    canonicalize_label_xml,
    label_canonical_checksum,
    load_mfds_label_plan,
)
from ai_worker.tasks.rag.source_ingestion.receipt_validation import calculate_endpoint_receipt_hash

PRODUCT_VERSION = "mfds-label-product-receipt@1"
ENDPOINT_VERSION = "mfds-label-xml-endpoint-receipt@1"
# #591 approved expansion scope: NN is limited to these five products.
APPROVED_ITEMS = frozenset(
    {
        "198500321",
        "200410090",
        "196000008",
        "200400463",
        "202106092",
        "198900672",
        "199400883",
        "200610765",
        "200511904",
        "200703804",
        "200811814",
        "200710759",
        "201403744",
        "199600864",
        "200511256",
        "200410337",
    }
)
NN_ITEMS = frozenset({"200400463", "202106092", "198900672", "199400883", "200610765"})
VERSIONS = dict(
    schema_version=SCHEMA_VERSION,
    parser_version=PARSER_VERSION,
    normalization_version=NORMALIZATION_VERSION,
    canonicalization_spec_version=CANONICALIZATION_SPEC_VERSION,
)
SCOPE = dict(
    limited_collection=True,
    private_preservation=True,
    source_snapshot_ingestion=True,
    internal_consumer_handoff=True,
    rag_use_authorized=False,
    runtime_citation_publication_authorized=False,
    service_publication_authorized=False,
)
RESULTS = dict(
    required_document_set_matches=True,
    raw_size_sha256_matches=True,
    xml_structure_valid=True,
    product_receipt_hash_matches=True,
)
APPROVAL_KEYS = {
    "item_seq",
    "official_product_name",
    "document_types",
    "policy_approval_ref",
    "technical_approval_ref",
    "product_identity_evidence_ref",
    "implementation_git_sha",
}
EVIDENCE_KEYS = {"item_seq", "collected_at", "documents"}
DOCUMENT_KEYS = {
    "document_type",
    "file_name",
    "source_url",
    "raw_sha256",
    "byte_size",
    "content_type",
    "received_at",
    "http_status",
}
MANIFEST_DOCUMENT_KEYS = DOCUMENT_KEYS - {"received_at", "http_status"}


def fail() -> NoReturn:
    raise ValueError("XML_RECEIPT_VALIDATION_FAILED")


def exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail()
    return value


def canonical(value: Any) -> bytes:
    def validate(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    fail()
                validate(child)
        elif isinstance(node, list):
            for child in node:
                validate(child)
        elif node is None or type(node) not in (str, int, bool):
            fail()

    validate(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value: Any) -> str:
    if not isinstance(value, str):
        fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            fail()
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError):
        fail()
    raise AssertionError("unreachable")


def reference(value: Any) -> None:
    # Opaque restricted evidence ID or GitHub issue/comment URL, not a local path/secret.
    if not isinstance(value, str) or not 3 <= len(value) <= 500:
        fail()
    if re.search(r"placeholder|pending|todo|tbd|example|<|>", value, re.I):
        fail()
    if not (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:#@-]+", value)
        or re.fullmatch(r"https://github\.com/AI-HealthCare-05/AH_05_04/issues/591(?:#issuecomment-[0-9]+)?", value)
    ):
        fail()


def private_directory(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        fail()
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        fail()


def read_private(path: Path, limit: int) -> bytes:
    private_directory(path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ValueError("XML_RECEIPT_INPUT_INVALID") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            fail()
        raw = stream.read(limit + 1)
        if not raw or len(raw) > limit:
            fail()
        return raw


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail()
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_private(path, 256 * 1024), object_pairs_hook=unique)
        canonical(value)
        if not isinstance(value, dict):
            fail()
        return value
    except (OSError, ValueError, UnicodeError):
        raise ValueError("XML_RECEIPT_INPUT_INVALID") from None


def _verified_documents(
    item: str, sections: list[str], entries: list[Any], input_dir: Path, collected: str
) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
    documents = []
    structures = []
    for section, entry in zip(sections, entries, strict=True):
        exact(entry, DOCUMENT_KEYS)
        url = f"https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{item}/{section}"
        if entry["document_type"] != section or entry["file_name"] != section + ".xml" or entry["source_url"] != url:
            fail()
        if type(entry["http_status"]) is not int or entry["http_status"] != 200:
            fail()
        if type(entry["byte_size"]) is not int or not 0 < entry["byte_size"] <= MAX_XML_BYTES:
            fail()
        content_type = entry["content_type"]
        if (
            not isinstance(content_type, str)
            or len(content_type) > 255
            or content_type.partition(";")[0].strip().lower() != "application/download"
        ):
            fail()
        received = timestamp(entry["received_at"])
        if received > collected:
            fail()
        raw = read_private(input_dir / (section + ".xml"), MAX_XML_BYTES)
        if len(raw) != entry["byte_size"] or hashlib.sha256(raw).hexdigest() != entry["raw_sha256"]:
            fail()
        structure, body = canonicalize_label_xml(raw, section)
        structures.append(structure)
        document_payload = {
            "canonicalization_spec_version": CANONICALIZATION_SPEC_VERSION,
            "item_seq": item,
            **structure,
        }
        documents.append(
            {
                **entry,
                "received_at": received,
                "artifact_key": f"mfds-label/{item}/{section}.xml",
                "canonical_sha256": sha(document_payload),
                "content_status": body["content_status"],
                "empty_article_titles": body["empty_article_titles"],
            }
        )
    if max(d["received_at"] for d in documents) != collected:
        fail()
    return documents, structures


def build_bundle(
    *, approval: dict[str, Any], evidence: dict[str, Any], input_dir: Path, generated_at: str, validated_at: str
) -> dict[str, dict[str, Any]]:
    """Recompute every assertion from private bytes and explicitly supplied approval evidence."""
    exact(approval, APPROVAL_KEYS)
    exact(evidence, EVIDENCE_KEYS)
    canonical(approval)
    canonical(evidence)
    item = approval["item_seq"]
    if not isinstance(item, str) or item not in APPROVED_ITEMS or evidence["item_seq"] != item:
        fail()
    sections = ["EE", "UD", "NB"] + (["NN"] if item in NN_ITEMS else [])
    if approval["document_types"] != sections:
        fail()
    name = approval["official_product_name"]
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 300
        or re.search(r"placeholder|pending|todo|tbd|<|>", name, re.I)
    ):
        fail()
    for key in ("policy_approval_ref", "technical_approval_ref", "product_identity_evidence_ref"):
        reference(approval[key])
    if (
        not isinstance(approval["implementation_git_sha"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", approval["implementation_git_sha"])
        or len(set(approval["implementation_git_sha"])) == 1
    ):
        fail()
    generated = timestamp(generated_at)
    validated = timestamp(validated_at)
    collected = timestamp(evidence["collected_at"])
    if not collected <= validated <= generated:
        fail()
    private_directory(input_dir)
    names = {p.name for p in input_dir.iterdir()}
    if names - {s + ".xml" for s in sections} - {"acquisition-manifest.json"}:
        fail()
    entries = evidence["documents"]
    if not isinstance(entries, list) or len(entries) != len(sections):
        fail()
    documents, structures = _verified_documents(item, sections, entries, input_dir, collected)
    identity = dict(
        source_code="MFDS_PRODUCT_LABEL",
        endpoint_code="MFDS_NEDRUG_LABEL_XML",
        operation_code=f"COLLECT_MFDS_{item}_LABEL_XML",
    )
    product = {
        **approval,
        "schema_version": PRODUCT_VERSION,
        "identity": identity,
        "collected_at": collected,
        "versions": VERSIONS,
        "documents": documents,
        "canonical_checksum": label_canonical_checksum(item, structures),
        "generated_at": generated,
    }
    product["manifest_hash"] = sha({k: v for k, v in product.items() if k != "generated_at"})
    endpoint = dict(
        receipt_version=ENDPOINT_VERSION,
        identity=identity,
        item_seq=item,
        official_product_name=name,
        document_types=sections,
        collected_at=collected,
        product_receipt=dict(schema_version=PRODUCT_VERSION, manifest_hash=product["manifest_hash"]),
        verified_http_method="GET",
        verified_scheme="https",
        verified_host="nedrug.mfds.go.kr",
        verified_path_template="/pbp/cmn/xml/drb/{ITEM_SEQ}/{document_type}",
        encoding="UTF-8",
        verification_results=RESULTS,
        policy_approval_ref=approval["policy_approval_ref"],
        technical_approval_ref=approval["technical_approval_ref"],
        scope=SCOPE,
        implementation_git_sha=approval["implementation_git_sha"],
        validated_at=validated,
        generated_at=generated,
    )
    endpoint["receipt_hash"] = calculate_endpoint_receipt_hash(endpoint)
    manifest = dict(
        schema_version=ACQUISITION_EVIDENCE_VERSION,
        item_seq=item,
        collected_at=collected,
        endpoint_receipt_hash=endpoint["receipt_hash"],
        documents=[{k: d[k] for k in MANIFEST_DOCUMENT_KEYS} for d in documents],
    )
    return {"product-receipt.json": product, "endpoint-receipt.json": endpoint, "acquisition-manifest.json": manifest}


def verify_bundle(
    *,
    approval: dict[str, Any],
    evidence: dict[str, Any],
    input_dir: Path,
    receipt_dir: Path,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product = load_json(receipt_dir / "product-receipt.json")
    endpoint = load_json(receipt_dir / "endpoint-receipt.json")
    observed = {
        "product-receipt.json": product,
        "endpoint-receipt.json": endpoint,
        "acquisition-manifest.json": load_json(input_dir / "acquisition-manifest.json"),
    }
    expected = build_bundle(
        approval=approval,
        evidence=evidence,
        input_dir=input_dir,
        generated_at=endpoint.get("generated_at", ""),
        validated_at=endpoint.get("validated_at", ""),
    )
    # generated_at is not hashed, but must be a valid time after validation.
    expected["product-receipt.json"]["generated_at"] = product.get("generated_at")
    if timestamp(product.get("generated_at")) < timestamp(endpoint.get("validated_at")):
        fail()
    if canonical(observed) != canonical(expected):
        fail()
    identity = SourceOperationIdentity(**endpoint["identity"])
    plan = load_mfds_label_plan(
        item_seq=approval["item_seq"],
        input_dir=input_dir,
        identity=identity,
        endpoint_receipt_hash=endpoint["receipt_hash"],
        collected_at=datetime.fromisoformat(endpoint["collected_at"].replace("Z", "+00:00")),
        include_e_drug="NN" in approval["document_types"],
    )
    if plan.ingestion.canonical_checksum != product["canonical_checksum"]:
        fail()
    if profile is not None:
        expected_profile = {
            **endpoint["identity"],
            "item_seq": approval["item_seq"],
            "endpoint_receipt_hash": endpoint["receipt_hash"],
            "include_e_drug": "NN" in approval["document_types"],
        }
        for key, value in expected_profile.items():
            if type(profile.get(key)) is not type(value) or profile.get(key) != value:
                fail()
        for key in ("technical_approval_ref", "policy_approval_ref"):
            if profile.get(key) != approval[key]:
                fail()
    return {
        "status": "XML_RECEIPTS_VERIFIED",
        "item_seq": approval["item_seq"],
        "endpoint_receipt_hash": endpoint["receipt_hash"],
        "manifest_hash": product["manifest_hash"],
        "database_changed": False,
        "profile_ready_changed": False,
    }


def issue_bundle(
    *, approval: dict[str, Any], evidence: dict[str, Any], input_dir: Path, receipt_dir: Path, now: str
) -> dict[str, Any]:
    private_directory(receipt_dir)
    if input_dir == receipt_dir:
        fail()
    bundle = build_bundle(approval=approval, evidence=evidence, input_dir=input_dir, generated_at=now, validated_at=now)
    targets = [(input_dir if name == "acquisition-manifest.json" else receipt_dir) / name for name in bundle]
    if any(p.exists() or p.is_symlink() for p in targets):
        fail()
    # Do not delete partial evidence on failure. Exclusive creation makes retry fail closed.
    for path in targets:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(
                json.dumps(bundle[path.name], ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
    return verify_bundle(approval=approval, evidence=evidence, input_dir=input_dir, receipt_dir=receipt_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("issue", "verify"))
    for name in ("approval", "evidence", "input-dir", "receipt-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    try:
        approval, evidence = load_json(args.approval), load_json(args.evidence)
        if args.mode == "issue":
            if args.profile is not None:
                fail()
            result = issue_bundle(
                approval=approval,
                evidence=evidence,
                input_dir=args.input_dir,
                receipt_dir=args.receipt_dir,
                now=datetime.now(UTC).isoformat(),
            )
        else:
            result = verify_bundle(
                approval=approval,
                evidence=evidence,
                input_dir=args.input_dir,
                receipt_dir=args.receipt_dir,
                profile=load_json(args.profile) if args.profile else None,
            )
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        print('{"status":"XML_RECEIPT_STOP","database_changed":false,"profile_ready_changed":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
