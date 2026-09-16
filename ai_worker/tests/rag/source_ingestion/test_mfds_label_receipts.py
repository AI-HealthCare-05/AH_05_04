"""Synthetic XML receipt contract and private issuance regression tests."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from ai_worker.admin import mfds_label_receipts as receipts
from ai_worker.tasks.rag.source_ingestion.mfds_label import NN_ARTICLE_TITLES
from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes
from ai_worker.tasks.rag.source_ingestion.receipt_validation import calculate_endpoint_receipt_hash
from ai_worker.tests.rag.source_ingestion.test_mfds_label import _xml

NOW = "2026-09-16T11:00:00.000000Z"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))
    path.chmod(0o600)


def setup_case(tmp_path, item="198500321"):
    root = tmp_path.resolve()
    root.chmod(0o700)
    inputs, output = root / "inputs", root / "receipts"
    inputs.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    sections = ["EE", "UD", "NB"] + (["NN"] if item in receipts.NN_ITEMS else [])
    approval = dict(
        item_seq=item,
        official_product_name="합성 제품",
        document_types=sections,
        policy_approval_ref="policy:591:approved",
        technical_approval_ref="technical:591:approved",
        product_identity_evidence_ref="identity:591:verified",
        implementation_git_sha="0123456789abcdef0123456789abcdef01234567",
    )
    documents = []
    for section in sections:
        raw = (
            _xml(section)
            if section != "NN"
            else (
                '<DOC type="NN" title="e약은요 정보">'
                + "".join(f'<ARTICLE title="{title}"><PARAGRAPH></PARAGRAPH></ARTICLE>' for title in NN_ARTICLE_TITLES)
                + "</DOC>"
            ).encode()
        )
        path = inputs / (section + ".xml")
        path.write_bytes(raw)
        path.chmod(0o600)
        documents.append(
            dict(
                document_type=section,
                file_name=section + ".xml",
                source_url=f"https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{item}/{section}",
                raw_sha256=hashlib.sha256(raw).hexdigest(),
                byte_size=len(raw),
                content_type="application/download; charset=UTF-8",
                received_at=NOW,
                http_status=200,
            )
        )
    return dict(
        approval=approval,
        evidence=dict(item_seq=item, collected_at=NOW, documents=documents),
        input_dir=inputs,
        receipt_dir=output,
    )


def test_issue_verify_and_existing_snapshot_canonical_contract(tmp_path):
    args = setup_case(tmp_path)
    result = receipts.issue_bundle(**args, now=NOW)
    assert result["status"] == "XML_RECEIPTS_VERIFIED"
    assert not result["database_changed"] and not result["profile_ready_changed"]
    product = receipts.load_json(args["receipt_dir"] / "product-receipt.json")
    endpoint = receipts.load_json(args["receipt_dir"] / "endpoint-receipt.json")
    assert endpoint["receipt_hash"] == calculate_endpoint_receipt_hash(endpoint)
    assert result["manifest_hash"] != result["endpoint_receipt_hash"]
    assert (
        product["canonical_checksum"]
        == hashlib.sha256(
            canonical_json_bytes(
                {
                    "canonicalization_spec_version": receipts.CANONICALIZATION_SPEC_VERSION,
                    "item_seq": "198500321",
                    "documents": [receipts.canonicalize_label_xml(_xml(s), s)[0] for s in ["EE", "UD", "NB"]],
                }
            )
        ).hexdigest()
    )
    for path in [*args["receipt_dir"].iterdir(), args["input_dir"] / "acquisition-manifest.json"]:
        assert path.stat().st_mode & 0o777 == 0o600
    profile = {
        **endpoint["identity"],
        "item_seq": "198500321",
        "endpoint_receipt_hash": endpoint["receipt_hash"],
        "include_e_drug": False,
        "policy_approval_ref": args["approval"]["policy_approval_ref"],
        "technical_approval_ref": args["approval"]["technical_approval_ref"],
        "state": "PENDING_TECHNICAL_CONFIRMATION",
    }
    assert receipts.verify_bundle(**args, profile=profile) == result
    assert profile["state"] == "PENDING_TECHNICAL_CONFIRMATION"
    profile["endpoint_receipt_hash"] = product["manifest_hash"]
    with pytest.raises(ValueError):
        receipts.verify_bundle(**args, profile=profile)


@pytest.mark.parametrize("item", sorted(receipts.NN_ITEMS))
def test_approved_nn_official_blank_is_preserved(tmp_path, item):
    args = setup_case(tmp_path, item)
    receipts.issue_bundle(**args, now=NOW)
    product = receipts.load_json(args["receipt_dir"] / "product-receipt.json")
    assert len(product["documents"]) == 4
    assert product["documents"][-1]["content_status"] == "PARTIAL_OFFICIAL"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_item",
        "novasc",
        "unapproved_nn",
        "missing_nn",
        "placeholder",
        "missing_ref",
        "fake_sha",
        "duplicate_document",
        "reversed",
        "size_bool",
        "raw_hash",
        "content_type",
        "wrong_url",
        "time",
        "extra",
    ],
)
def test_invalid_input_fails_before_writes(tmp_path, mutation):
    args = setup_case(tmp_path, "200400463" if mutation == "missing_nn" else "198500321")
    a, e = args["approval"], args["evidence"]
    mutations = {
        "unknown_item": lambda: (a.update(item_seq="999999999"), e.update(item_seq="999999999")),
        "novasc": lambda: (a.update(item_seq="200610660"), e.update(item_seq="200610660")),
        "unapproved_nn": lambda: a["document_types"].append("NN"),
        "missing_nn": lambda: a["document_types"].pop(),
        "placeholder": lambda: a.update(technical_approval_ref="PENDING"),
        "missing_ref": lambda: a.pop("policy_approval_ref"),
        "fake_sha": lambda: a.update(implementation_git_sha="0" * 40),
        "duplicate_document": lambda: e["documents"].__setitem__(1, copy.deepcopy(e["documents"][0])),
        "reversed": lambda: e["documents"].reverse(),
        "size_bool": lambda: e["documents"][0].update(byte_size=True),
        "raw_hash": lambda: e["documents"][0].update(raw_sha256="a" * 64),
        "content_type": lambda: e["documents"][0].update(content_type="text/html"),
        "wrong_url": lambda: e["documents"][0].update(source_url="https://invalid.invalid/"),
        "time": lambda: e["documents"][0].update(received_at="2027-01-01T00:00:00Z"),
        "extra": lambda: e["documents"][0].update(unknown="value"),
    }
    mutations[mutation]()
    with pytest.raises(ValueError):
        receipts.issue_bundle(**args, now=NOW)
    assert not list(args["receipt_dir"].iterdir())
    assert not (args["input_dir"] / "acquisition-manifest.json").exists()


@pytest.mark.parametrize("target", ["product-receipt.json", "endpoint-receipt.json", "acquisition-manifest.json"])
def test_no_overwrite(tmp_path, target):
    args = setup_case(tmp_path)
    directory = args["input_dir"] if target.startswith("acquisition") else args["receipt_dir"]
    path = directory / target
    path.write_bytes(b"original")
    path.chmod(0o600)
    with pytest.raises(ValueError):
        receipts.issue_bundle(**args, now=NOW)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("kind", ["file_permissions", "dir_permissions", "symlink", "parent_symlink"])
def test_private_filesystem_boundary(tmp_path, kind):
    args = setup_case(tmp_path)
    path = args["input_dir"] / "EE.xml"
    if kind == "file_permissions":
        path.chmod(0o644)
    if kind == "dir_permissions":
        args["input_dir"].chmod(0o755)
    if kind == "symlink":
        raw = path.read_bytes()
        path.unlink()
        other = tmp_path.resolve() / "other.xml"
        other.write_bytes(raw)
        other.chmod(0o600)
        path.symlink_to(other)
    if kind == "parent_symlink":
        alias = tmp_path.resolve() / "alias"
        alias.symlink_to(args["input_dir"], target_is_directory=True)
        args["input_dir"] = alias
    with pytest.raises(ValueError):
        receipts.issue_bundle(**args, now=NOW)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("product-receipt.json", "official_product_name", "changed"),
        ("product-receipt.json", "manifest_hash", "a" * 64),
        ("endpoint-receipt.json", "receipt_hash", "b" * 64),
        ("endpoint-receipt.json", "scope", {"service_publication_authorized": True}),
        ("endpoint-receipt.json", "extra", "unexpected"),
        ("acquisition-manifest.json", "endpoint_receipt_hash", "c" * 64),
    ],
)
def test_tampered_receipts_fail_even_with_rehashed_endpoint(tmp_path, target, field, value):
    args = setup_case(tmp_path)
    receipts.issue_bundle(**args, now=NOW)
    directory = args["input_dir"] if target.startswith("acquisition") else args["receipt_dir"]
    path = directory / target
    payload = receipts.load_json(path)
    payload[field] = value
    if target == "endpoint-receipt.json" and field != "receipt_hash":
        payload["receipt_hash"] = calculate_endpoint_receipt_hash(payload)
    write(path, payload)
    with pytest.raises(ValueError):
        receipts.verify_bundle(**args)


def test_generated_at_excluded_but_validation_fields_hashed(tmp_path):
    args = setup_case(tmp_path)
    kwargs = {k: v for k, v in args.items() if k != "receipt_dir"}
    first = receipts.build_bundle(**kwargs, generated_at=NOW, validated_at=NOW)
    second = receipts.build_bundle(**kwargs, generated_at="2026-09-16T12:00:00Z", validated_at=NOW)
    assert first["product-receipt.json"]["manifest_hash"] == second["product-receipt.json"]["manifest_hash"]
    assert first["endpoint-receipt.json"]["receipt_hash"] == second["endpoint-receipt.json"]["receipt_hash"]
    second["endpoint-receipt.json"]["validated_at"] = "2026-09-16T12:00:00Z"
    assert (
        calculate_endpoint_receipt_hash(second["endpoint-receipt.json"])
        != first["endpoint-receipt.json"]["receipt_hash"]
    )


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1.5}', '{"a":null}'])
def test_invalid_json_rejected(tmp_path, raw):
    root = tmp_path.resolve()
    root.chmod(0o700)
    path = root / "input.json"
    path.write_text(raw)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        receipts.load_json(path)


def test_partial_write_failure_never_overwrites_on_retry(tmp_path, monkeypatch):
    args = setup_case(tmp_path)
    original_open = os.open

    def broken_open(path, flags, *rest):
        if Path(path).name == "endpoint-receipt.json" and flags & os.O_CREAT:
            raise OSError("synthetic disk failure")
        return original_open(path, flags, *rest)

    monkeypatch.setattr(os, "open", broken_open)
    with pytest.raises(OSError):
        receipts.issue_bundle(**args, now=NOW)
    assert (args["receipt_dir"] / "product-receipt.json").exists()
    with pytest.raises(ValueError):
        receipts.issue_bundle(**args, now=NOW)


def test_cli_failure_does_not_print_private_path_or_exception(tmp_path, monkeypatch, capsys):
    import sys

    secret_path = tmp_path.resolve() / "private-sensitive-path.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "receipts",
            "verify",
            "--approval",
            str(secret_path),
            "--evidence",
            str(secret_path),
            "--input-dir",
            str(tmp_path),
            "--receipt-dir",
            str(tmp_path),
        ],
    )
    assert receipts.main() == 1
    captured = capsys.readouterr()
    assert "private-sensitive" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out)["status"] == "XML_RECEIPT_STOP"


def test_same_size_raw_tamper_rejected(tmp_path):
    args = setup_case(tmp_path)
    receipts.issue_bundle(**args, now=NOW)
    path = args["input_dir"] / "EE.xml"
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"ignored", b"changed"))
    with pytest.raises(ValueError):
        receipts.verify_bundle(**args)


def test_endpoint_product_receipt_hash_substitution_rejected(tmp_path):
    args = setup_case(tmp_path)
    receipts.issue_bundle(**args, now=NOW)
    endpoint = receipts.load_json(args["receipt_dir"] / "endpoint-receipt.json")
    endpoint["product_receipt"]["manifest_hash"] = endpoint["receipt_hash"]
    endpoint["receipt_hash"] = calculate_endpoint_receipt_hash(endpoint)
    write(args["receipt_dir"] / "endpoint-receipt.json", endpoint)
    manifest_path = args["input_dir"] / "acquisition-manifest.json"
    manifest = receipts.load_json(manifest_path)
    manifest["endpoint_receipt_hash"] = endpoint["receipt_hash"]
    write(manifest_path, manifest)
    with pytest.raises(ValueError):
        receipts.verify_bundle(**args)
