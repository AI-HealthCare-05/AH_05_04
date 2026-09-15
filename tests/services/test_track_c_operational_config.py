import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.models.track_c import SupportCode
from app.services import track_c_handler_config as handler_config
from app.services.track_c_handler_config import (
    ACTIVE_COPY_VERSION,
    ACTIVE_RULE_VERSION,
    APPROVED_COPY_VERSIONS,
    HandlerConfigError,
    load_active_handler_config,
    load_active_support_copy_catalog,
    load_support_copy_catalog,
    parse_support_copy_catalog,
)

COPY_DIR = Path("backend/app/config/track_c/support-copy")


@pytest.mark.parametrize("loader", [load_active_handler_config, load_active_support_copy_catalog])
@pytest.mark.parametrize("failure", ["missing", "invalid_json", "incomplete", "wrong_version"])
def test_active_loaders_reject_unusable_copy(loader, failure: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(handler_config, "_COPY_DIR", tmp_path)
    if failure != "missing":
        payload = active_copy_payload()
        if failure == "incomplete":
            payload["supports"].pop()
        elif failure == "wrong_version":
            payload["copy_version"] = "synthetic-other-copy"
        content = "{" if failure == "invalid_json" else json.dumps(payload)
        (tmp_path / f"{ACTIVE_COPY_VERSION}.json").write_text(content, encoding="utf-8")
    with pytest.raises(HandlerConfigError):
        loader()


@pytest.mark.parametrize("loader", [load_active_handler_config, load_active_support_copy_catalog])
def test_active_loaders_reject_approved_but_mismatched_copy(loader, tmp_path: Path, monkeypatch) -> None:
    other_version = "synthetic-other-copy"
    payload = active_copy_payload()
    payload["copy_version"] = other_version
    (tmp_path / f"{other_version}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(handler_config, "_COPY_DIR", tmp_path)
    monkeypatch.setattr(handler_config, "ACTIVE_COPY_VERSION", other_version)
    monkeypatch.setattr(handler_config, "APPROVED_COPY_VERSIONS", APPROVED_COPY_VERSIONS | {other_version})
    with pytest.raises(HandlerConfigError, match="active rule and copy versions do not match"):
        loader()


def active_copy_payload() -> dict:
    path = COPY_DIR / f"{ACTIVE_COPY_VERSION}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_active_rule_and_copy_catalog_are_complete_and_linked() -> None:
    config = load_active_handler_config()
    catalog = load_active_support_copy_catalog()

    assert config.rule_version == ACTIVE_RULE_VERSION
    assert catalog.copy_version == ACTIVE_COPY_VERSION
    assert catalog.locale == "ko-KR"
    assert set(config.supports) == set(SupportCode)
    assert set(catalog.supports) == set(SupportCode)
    assert {rule.copy_version for rule in config.supports.values()} == {catalog.copy_version}


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(schema_version="future"),
        lambda data: data.update(copy_version="missing"),
        lambda data: data.update(locale="en-US"),
        lambda data: data["supports"].pop(),
        lambda data: data["supports"].append(deepcopy(data["supports"][0])),
        lambda data: data["supports"][0].update(extra="value"),
        lambda data: data["supports"][0].update(title=""),
        lambda data: data["supports"][0].update(body=" leading"),
        lambda data: data["supports"][0]["confirmation"].pop("primary_label"),
        lambda data: data["supports"][0]["confirmation"].update(secondary_label=1),
    ],
)
def test_invalid_or_unapproved_copy_catalog_is_blocked(change) -> None:
    data = active_copy_payload()
    change(data)
    with pytest.raises(HandlerConfigError):
        parse_support_copy_catalog(data, approved_copy_versions=APPROVED_COPY_VERSIONS)


def test_copy_file_requires_explicit_approval_and_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / f"{ACTIVE_COPY_VERSION}.json"
    path.write_text(json.dumps(active_copy_payload()), encoding="utf-8")
    assert (
        load_support_copy_catalog(
            tmp_path,
            ACTIVE_COPY_VERSION,
            approved_copy_versions=APPROVED_COPY_VERSIONS,
        ).copy_version
        == ACTIVE_COPY_VERSION
    )
    with pytest.raises(HandlerConfigError, match="unapproved copy file"):
        load_support_copy_catalog(tmp_path, ACTIVE_COPY_VERSION, approved_copy_versions=frozenset())
    with pytest.raises(HandlerConfigError, match="unapproved copy file"):
        load_support_copy_catalog(COPY_DIR, f"../{ACTIVE_COPY_VERSION}", approved_copy_versions=APPROVED_COPY_VERSIONS)
    path.write_text('{"schema_version":"track-c-support-copy-v1","schema_version":"other"}', encoding="utf-8")
    with pytest.raises(HandlerConfigError, match="duplicate JSON field"):
        load_support_copy_catalog(
            tmp_path,
            ACTIVE_COPY_VERSION,
            approved_copy_versions=APPROVED_COPY_VERSIONS,
        )
