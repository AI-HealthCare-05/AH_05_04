"""Protected PostgreSQL backup, isolated restore verification, and credential rotation.

This module intentionally emits only one allowlisted JSON receipt on stdout. Operational
diagnostics are fixed, non-sensitive reason codes written to stderr by ``main``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote_plus

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.protected_retrieval import (
    AuthorizationAuditEntry,
    ControlCommandAuditEntry,
    OperationAuditEntry,
    ProtectedAuditEventKind,
)
from ai_worker.tasks.evaluation.protected_retrieval import (
    audit_entry_sha256 as protected_audit_entry_sha256,
)
from infra.python.protected_retrieval_role_policy import (
    PROTECTED_BACKUP_RELATIONS,
    quoted_identifier,
    validate_protected_backup_connection,
    validate_protected_control_connection,
    validate_protected_data_connection,
)

EXPECTED_RELATIONS = PROTECTED_BACKUP_RELATIONS
BACKUP_RELATIONS = PROTECTED_BACKUP_RELATIONS
BACKUP_ALLOWED_PRIVILEGES = frozenset({"SELECT"})
EXPECTED_DOMAINS = frozenset({"sha256_hex"})
RESTORE_DATABASE_ALIAS = "protected-retrieval-restore-db"
PROTECTED_ALEMBIC_HEAD = "368000000002"
BACKUP_REASONS = ("DAILY", "FREEZE", "VERSION_CHANGE", "EVIDENCE_REHEARSAL")

BACKUP_RECEIPT_FIELDS = (
    "operation",
    "result",
    "backup_id",
    "recorded_at",
    "reason",
    "scope_ref",
    "encrypted_artifact_sha256",
    "encryption_key_version",
    "acl_policy_ref",
    "relation_count",
)
RESTORE_RECEIPT_FIELDS = (
    "operation",
    "result",
    "backup_id",
    "isolated_target",
    "production_target_guard",
    "alembic_head",
    "relation_validation",
    "audit_integrity",
    "acl_validation",
    "target_cleanup",
)
ROTATION_RECEIPT_FIELDS = (
    "operation",
    "result",
    "data_new_validation",
    "control_new_validation",
    "data_old_rejection",
    "control_old_rejection",
    "rollback_readiness",
    "recorded_at",
)

_RECEIPT_FIELDS = {
    "BACKUP": frozenset(BACKUP_RECEIPT_FIELDS),
    "RESTORE_VERIFY": frozenset(RESTORE_RECEIPT_FIELDS),
    "DB_CREDENTIAL_ROTATION": frozenset(ROTATION_RECEIPT_FIELDS),
}
_FORBIDDEN_RECEIPT_TERMS = (
    "path",
    "host",
    "username",
    "password",
    "secret",
    "key_material",
    "nonce",
    "tag",
    "content",
    "body",
)
_BACKUP_ID = re.compile(r"^prb-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_LOGICAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_MAGIC = b"AHPRB001"
_HEADER_LENGTH_BYTES = 4
_TAG_BYTES = 16
_CHUNK_SIZE = 1024 * 1024


class ProtectedBackupOpsError(RuntimeError):
    """A deliberately non-sensitive operational failure."""


class RotationDriver(Protocol):
    def validate_current(self) -> None: ...

    def apply_next(self) -> None: ...

    def validate_next(self) -> None: ...

    def validate_old_rejected(self) -> None: ...

    def rollback(self) -> None: ...

    def validate_rollback(self) -> None: ...


class BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class SeekableBinaryReader(BinaryReader, Protocol):
    def seek(self, offset: int, whence: int = 0) -> int: ...

    def tell(self) -> int: ...


class BinaryWriter(Protocol):
    def write(self, data: bytes) -> int: ...


def _utc(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ProtectedBackupOpsError("RECEIPT_CONTRACT_VIOLATION")
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProtectedBackupOpsError("BACKUP_ENCRYPTION_KEY_INVALID") from error
    if len(key) != 32:
        raise ProtectedBackupOpsError("BACKUP_ENCRYPTION_KEY_INVALID")
    return key


def _read_envelope_header(source: BinaryReader) -> tuple[dict[str, str], int]:
    if source.read(len(_MAGIC)) != _MAGIC:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED")
    raw_length = source.read(_HEADER_LENGTH_BYTES)
    if len(raw_length) != _HEADER_LENGTH_BYTES:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED")
    length = int.from_bytes(raw_length, "big")
    if length <= 0 or length > 4096:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED")
    raw_header = source.read(length)
    try:
        header = json.loads(raw_header)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED") from error
    if set(header) != {"format", "version", "key_version", "nonce"}:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED")
    if header["format"] != "AH_PROTECTED_PG_DUMP" or header["version"] != "1":
        raise ProtectedBackupOpsError("BACKUP_FORMAT_UNSUPPORTED")
    return header, len(_MAGIC) + _HEADER_LENGTH_BYTES + length


def encrypt_stream(
    source: BinaryReader,
    destination: BinaryWriter,
    *,
    encoded_key: str,
    key_version: str,
) -> None:
    """Encrypt a byte stream directly into the authenticated artifact envelope."""

    key = _decode_key(encoded_key)
    if not _LOGICAL_REF.fullmatch(key_version):
        raise ProtectedBackupOpsError("BACKUP_KEY_VERSION_INVALID")
    nonce = secrets.token_bytes(12)
    header = {
        "format": "AH_PROTECTED_PG_DUMP",
        "version": "1",
        "key_version": key_version,
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }
    raw_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    destination.write(_MAGIC)
    destination.write(len(raw_header).to_bytes(_HEADER_LENGTH_BYTES, "big"))
    destination.write(raw_header)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(raw_header)
    while chunk := source.read(_CHUNK_SIZE):
        destination.write(encryptor.update(chunk))
    destination.write(encryptor.finalize())
    destination.write(encryptor.tag)


def decrypt_stream(
    source: SeekableBinaryReader,
    destination: BinaryWriter,
    *,
    encoded_key: str,
    expected_key_version: str,
) -> None:
    """Authenticate and decrypt an artifact without materializing plaintext on disk."""

    key = _decode_key(encoded_key)
    header, body_offset = _read_envelope_header(source)
    if header["key_version"] != expected_key_version:
        raise ProtectedBackupOpsError("BACKUP_KEY_VERSION_MISMATCH")
    try:
        nonce = base64.b64decode(header["nonce"], validate=True)
        source.seek(0, os.SEEK_END)
        artifact_size = source.tell()
        ciphertext_length = artifact_size - body_offset - _TAG_BYTES
        if len(nonce) != 12 or ciphertext_length < 0:
            raise ValueError
        source.seek(artifact_size - _TAG_BYTES)
        tag = source.read(_TAG_BYTES)
        source.seek(body_offset)
        raw_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(raw_header)
        remaining = ciphertext_length
        while remaining:
            chunk = source.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                raise ValueError
            remaining -= len(chunk)
            destination.write(decryptor.update(chunk))
        destination.write(decryptor.finalize())
    except (InvalidTag, ValueError, binascii.Error) as error:
        raise ProtectedBackupOpsError("BACKUP_AUTHENTICATION_FAILED") from error


def validate_protected_scope(
    relations: set[str], domains: set[str], unexpected_objects: set[str] | None = None
) -> None:
    expected = set(EXPECTED_RELATIONS)
    if relations - expected or domains - set(EXPECTED_DOMAINS) or unexpected_objects:
        raise ProtectedBackupOpsError("BACKUP_SCOPE_DRIFT")
    if expected - relations or set(EXPECTED_DOMAINS) - domains:
        raise ProtectedBackupOpsError("BACKUP_SCOPE_MISSING")


def validate_backup_identity_distinct(backup_login: str, other_roles: Iterable[str]) -> None:
    if not backup_login or backup_login in {role for role in other_roles if role}:
        raise ProtectedBackupOpsError("BACKUP_IDENTITY_COLLISION")


def validate_restore_target(host: str) -> None:
    if host != RESTORE_DATABASE_ALIAS:
        raise ProtectedBackupOpsError("RESTORE_TARGET_FORBIDDEN")


def artifact_filename(backup_id: str) -> str:
    if not _BACKUP_ID.fullmatch(backup_id):
        raise ProtectedBackupOpsError("BACKUP_ID_INVALID")
    return f"{backup_id}.prb"


def _validate_logical_ref(value: str, code: str) -> str:
    if not _LOGICAL_REF.fullmatch(value):
        raise ProtectedBackupOpsError(code)
    return value


def _walk_receipt(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key), nested
            yield from _walk_receipt(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_receipt(nested)


def serialize_public_receipt(receipt: Mapping[str, Any]) -> str:
    operation = receipt.get("operation")
    allowed = _RECEIPT_FIELDS.get(str(operation))
    if allowed is None or set(receipt) != allowed:
        raise ProtectedBackupOpsError("RECEIPT_CONTRACT_VIOLATION")
    for key, value in _walk_receipt(receipt):
        lowered = key.lower()
        if any(term in lowered for term in _FORBIDDEN_RECEIPT_TERMS):
            raise ProtectedBackupOpsError("RECEIPT_CONTRACT_VIOLATION")
        if isinstance(value, str) and any(
            marker in value.lower()
            for marker in ("/protected-backups", "postgresql://", "localhost", "127.0.0.1", "private key")
        ):
            raise ProtectedBackupOpsError("RECEIPT_CONTRACT_VIOLATION")
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"))


def build_backup_receipt(
    *,
    backup_id: str,
    recorded_at: datetime,
    reason: str,
    scope_ref: str,
    encrypted_artifact_sha256: str,
    encryption_key_version: str,
    acl_policy_ref: str,
) -> dict[str, Any]:
    artifact_filename(backup_id)
    if reason not in BACKUP_REASONS or not re.fullmatch(r"[0-9a-f]{64}", encrypted_artifact_sha256):
        raise ProtectedBackupOpsError("RECEIPT_CONTRACT_VIOLATION")
    receipt = dict.fromkeys(BACKUP_RECEIPT_FIELDS)
    receipt.update(
        operation="BACKUP",
        result="PASS",
        backup_id=backup_id,
        recorded_at=_utc(recorded_at),
        reason=reason,
        scope_ref=_validate_logical_ref(scope_ref, "BACKUP_SCOPE_REF_INVALID"),
        encrypted_artifact_sha256=encrypted_artifact_sha256,
        encryption_key_version=_validate_logical_ref(encryption_key_version, "BACKUP_KEY_VERSION_INVALID"),
        acl_policy_ref=_validate_logical_ref(acl_policy_ref, "BACKUP_ACL_POLICY_REF_INVALID"),
        relation_count=len(EXPECTED_RELATIONS),
    )
    return receipt


def build_restore_receipt(backup_id: str) -> dict[str, str]:
    artifact_filename(backup_id)
    receipt = dict.fromkeys(RESTORE_RECEIPT_FIELDS, "PASS")
    receipt.update(
        operation="RESTORE_VERIFY",
        backup_id=backup_id,
        isolated_target="DISPOSABLE_POSTGRES",
    )
    return receipt


def audit_entry_sha256(entry_body: Mapping[str, Any]) -> str:
    """Apply the canonical audit hash algorithm to a stored audit body."""

    payload = dict(entry_body)
    payload.pop("entry_sha256", None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def validate_audit_chain(entries: Iterable[Mapping[str, Any]], head: Mapping[str, Any]) -> None:
    previous: str | None = None
    count = 0
    for count, row in enumerate(entries, start=1):
        body = row.get("entry_body")
        computed_hash: str | None = None
        storage_fields = {"event_id", "event_kind", "operation_key", "recorded_at"}
        if isinstance(body, Mapping) and storage_fields <= set(row):
            entry: AuthorizationAuditEntry | ControlCommandAuditEntry | OperationAuditEntry
            try:
                event_kind = str(body.get("event_kind"))
                if event_kind == ProtectedAuditEventKind.AUTHORIZATION.value:
                    entry = AuthorizationAuditEntry.model_validate(dict(body))
                elif event_kind == ProtectedAuditEventKind.CONTROL.value:
                    entry = ControlCommandAuditEntry.model_validate(dict(body))
                elif event_kind == ProtectedAuditEventKind.OPERATION.value:
                    entry = OperationAuditEntry.model_validate(dict(body))
                else:
                    raise ValueError
            except Exception as error:
                raise ProtectedBackupOpsError("RESTORE_AUDIT_INTEGRITY_FAILED") from error
            if (
                entry.event_id != str(row.get("event_id"))
                or entry.event_kind.value != row.get("event_kind")
                or getattr(entry, "operation_key", None) != row.get("operation_key")
                or entry.recorded_at != row.get("recorded_at")
            ):
                raise ProtectedBackupOpsError("RESTORE_AUDIT_INTEGRITY_FAILED")
            computed_hash = protected_audit_entry_sha256(entry)
        if (
            row.get("sequence") != count
            or not isinstance(body, Mapping)
            or body.get("sequence") != row.get("sequence")
            or body.get("previous_entry_sha256") != row.get("previous_entry_sha256")
            or body.get("entry_sha256") != row.get("entry_sha256")
            or row.get("previous_entry_sha256") != previous
            or (computed_hash or audit_entry_sha256(body)) != row.get("entry_sha256")
        ):
            raise ProtectedBackupOpsError("RESTORE_AUDIT_INTEGRITY_FAILED")
        previous = str(row["entry_sha256"])
    if head.get("sequence") != count or head.get("entry_sha256") != previous:
        raise ProtectedBackupOpsError("RESTORE_AUDIT_INTEGRITY_FAILED")


def rotate_database_credentials(driver: RotationDriver, *, recorded_at: datetime | None = None) -> dict[str, str]:
    """Run the fail-closed rotation sequence and validate rollback on any mutation failure."""

    mutated = False
    try:
        driver.validate_current()
        mutated = True
        driver.apply_next()
        driver.validate_next()
        driver.validate_old_rejected()
    except Exception as error:
        if mutated:
            try:
                driver.rollback()
                driver.validate_rollback()
            except Exception as rollback_error:
                raise ProtectedBackupOpsError("DB_ROTATION_ROLLBACK_FAILED") from rollback_error
        raise ProtectedBackupOpsError("DB_ROTATION_FAILED") from error
    receipt = dict.fromkeys(ROTATION_RECEIPT_FIELDS, "PASS")
    receipt["operation"] = "DB_CREDENTIAL_ROTATION"
    receipt["recorded_at"] = _utc(recorded_at)
    return receipt


def _is_invalid_password_error(error: BaseException) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.__class__.__name__ == "InvalidPasswordError" and current.__class__.__module__.startswith("asyncpg"):
            return True
        for related in (current.__cause__, current.__context__, getattr(current, "orig", None)):
            if isinstance(related, BaseException):
                pending.append(related)
    return False


class PostgresqlRotationDriver:
    """DATA/CONTROL password rotation against the protected PostgreSQL boundary."""

    def __init__(self, config: Mapping[str, str]) -> None:
        self.config = dict(config)
        validate_backup_identity_distinct(
            self.config["data_user"],
            [self.config["control_user"], self.config["admin_user"]],
        )
        for name in ("data_user", "control_user", "access_role", "control_role"):
            quoted_identifier(self.config[name])

    def _url(self, user: str, password: str) -> str:
        return _database_url(
            host=self.config["host"],
            port=self.config["port"],
            database=self.config["database"],
            user=user,
            password=password,
        )

    async def _validate_pair(self, data_password: str, control_password: str) -> None:
        data_engine = create_async_engine(self._url(self.config["data_user"], data_password))
        try:
            async with data_engine.connect() as connection:
                await validate_protected_data_connection(
                    connection,
                    schema=self.config["schema"],
                    data_access=self.config["access_role"],
                    control=self.config["control_role"],
                )
        finally:
            await data_engine.dispose()
        control_engine = create_async_engine(self._url(self.config["control_user"], control_password))
        try:
            async with control_engine.connect() as connection:
                await validate_protected_control_connection(
                    connection,
                    schema=self.config["schema"],
                    data_access=self.config["access_role"],
                    control=self.config["control_role"],
                )
        finally:
            await control_engine.dispose()

    async def _set_passwords(self, data_password: str, control_password: str) -> None:
        admin = create_async_engine(self._url(self.config["admin_user"], self.config["admin_password"]))
        data_literal = data_password.replace("'", "''")
        control_literal = control_password.replace("'", "''")
        try:
            async with admin.begin() as connection:
                await connection.exec_driver_sql(
                    f"ALTER ROLE {quoted_identifier(self.config['data_user'])} PASSWORD '{data_literal}'"
                )
                await connection.exec_driver_sql(
                    f"ALTER ROLE {quoted_identifier(self.config['control_user'])} PASSWORD '{control_literal}'"
                )
        finally:
            await admin.dispose()

    async def _assert_rejected(self, user: str, password: str) -> None:
        engine = create_async_engine(self._url(user, password))
        try:
            try:
                async with engine.connect():
                    pass
            except Exception as error:
                if _is_invalid_password_error(error):
                    return
                raise ProtectedBackupOpsError("DB_OLD_REJECTION_UNVERIFIED") from error
            raise ProtectedBackupOpsError("DB_OLD_CREDENTIAL_ACCEPTED")
        finally:
            await engine.dispose()

    def validate_current(self) -> None:
        asyncio.run(self._validate_pair(self.config["data_password"], self.config["control_password"]))

    def apply_next(self) -> None:
        asyncio.run(self._set_passwords(self.config["data_password_next"], self.config["control_password_next"]))

    def validate_next(self) -> None:
        asyncio.run(self._validate_pair(self.config["data_password_next"], self.config["control_password_next"]))

    def validate_old_rejected(self) -> None:
        asyncio.run(self._assert_rejected(self.config["data_user"], self.config["data_password"]))
        asyncio.run(self._assert_rejected(self.config["control_user"], self.config["control_password"]))

    def rollback(self) -> None:
        asyncio.run(self._set_passwords(self.config["data_password"], self.config["control_password"]))

    def validate_rollback(self) -> None:
        self.validate_current()


def _rotation_driver_from_environment() -> PostgresqlRotationDriver:
    names = {
        "host": "PROTECTED_DB_HOST",
        "port": "PROTECTED_DB_PORT",
        "database": "PROTECTED_DB_NAME",
        "schema": "PROTECTED_DB_SCHEMA",
        "access_role": "PROTECTED_DB_ACCESS_ROLE",
        "control_role": "PROTECTED_DB_CONTROL_ROLE",
        "data_user": "PROTECTED_DB_USER",
        "data_password": "PROTECTED_DB_PASSWORD",
        "data_password_next": "PROTECTED_DB_PASSWORD_NEXT",
        "control_user": "PROTECTED_DB_CONTROL_USER",
        "control_password": "PROTECTED_DB_CONTROL_PASSWORD",
        "control_password_next": "PROTECTED_DB_CONTROL_PASSWORD_NEXT",
        "admin_user": "DB_ADMIN_USER",
        "admin_password": "DB_ADMIN_PASSWORD",
    }
    config = {key: os.getenv(environment_name, "").strip() for key, environment_name in names.items()}
    config["port"] = config["port"] or "5432"
    if any(not value for value in config.values()):
        raise ProtectedBackupOpsError("CONFIG_REQUIRED")
    return PostgresqlRotationDriver(config)


def _require_storage_root(root: Path) -> Path:
    try:
        metadata = root.lstat()
    except OSError as error:
        raise ProtectedBackupOpsError("BACKUP_STORAGE_INVALID") from error
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ProtectedBackupOpsError("BACKUP_STORAGE_INVALID")
    return root


def _artifact_path(root: Path, backup_id: str, *, must_exist: bool) -> Path:
    checked_root = _require_storage_root(root)
    target = checked_root / artifact_filename(backup_id)
    if target.parent != checked_root or target.is_symlink() or target.exists() is not must_exist:
        raise ProtectedBackupOpsError("BACKUP_ARTIFACT_STATE_INVALID")
    if must_exist and stat.S_IMODE(target.stat().st_mode) != 0o600:
        raise ProtectedBackupOpsError("BACKUP_ARTIFACT_MODE_INVALID")
    return target


def _pg_env(password: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    return environment


def _pg_connection_args(*, host: str, port: str, database: str, user: str) -> list[str]:
    return ["--host", host, "--port", port, "--username", user, "--dbname", database]


async def _catalog_scope(connection: AsyncConnection, schema: str) -> tuple[set[str], set[str], set[str]]:
    relations = set(
        await connection.scalars(
            text(
                "SELECT class.relname FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = :schema "
                "AND class.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'c')"
            ),
            {"schema": schema},
        )
    )
    domains = set(
        await connection.scalars(
            text("SELECT domain_name FROM information_schema.domains WHERE domain_schema = :schema"),
            {"schema": schema},
        )
    )
    unexpected_objects = set(
        await connection.scalars(
            text(
                "SELECT object_kind || ':' || object_name FROM ("
                "SELECT 'routine' AS object_kind, procedure.proname AS object_name "
                "FROM pg_proc procedure JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'type', type.typname FROM pg_type type "
                "JOIN pg_namespace namespace ON namespace.oid = type.typnamespace "
                "WHERE namespace.nspname = :schema AND type.typtype IN ('c','e','m','r') AND type.typrelid = 0 "
                "UNION ALL SELECT 'trigger', trigger.tgname FROM pg_trigger trigger "
                "JOIN pg_class class ON class.oid = trigger.tgrelid "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = :schema AND NOT trigger.tgisinternal "
                "UNION ALL SELECT 'policy', policy.polname FROM pg_policy policy "
                "JOIN pg_class class ON class.oid = policy.polrelid "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'rule', rewrite.rulename FROM pg_rewrite rewrite "
                "JOIN pg_class class ON class.oid = rewrite.ev_class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = :schema AND rewrite.rulename <> '_RETURN' "
                "UNION ALL SELECT 'collation', collation.collname FROM pg_collation collation "
                "JOIN pg_namespace namespace ON namespace.oid = collation.collnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'conversion', conversion.conname FROM pg_conversion conversion "
                "JOIN pg_namespace namespace ON namespace.oid = conversion.connamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'operator', operator.oprname FROM pg_operator operator "
                "JOIN pg_namespace namespace ON namespace.oid = operator.oprnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'operator_class', operator_class.opcname FROM pg_opclass operator_class "
                "JOIN pg_namespace namespace ON namespace.oid = operator_class.opcnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'operator_family', operator_family.opfname FROM pg_opfamily operator_family "
                "JOIN pg_namespace namespace ON namespace.oid = operator_family.opfnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'text_search_config', config.cfgname FROM pg_ts_config config "
                "JOIN pg_namespace namespace ON namespace.oid = config.cfgnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'text_search_dictionary', dictionary.dictname FROM pg_ts_dict dictionary "
                "JOIN pg_namespace namespace ON namespace.oid = dictionary.dictnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'text_search_parser', parser.prsname FROM pg_ts_parser parser "
                "JOIN pg_namespace namespace ON namespace.oid = parser.prsnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'text_search_template', template.tmplname FROM pg_ts_template template "
                "JOIN pg_namespace namespace ON namespace.oid = template.tmplnamespace "
                "WHERE namespace.nspname = :schema "
                "UNION ALL SELECT 'statistics', statistics.stxname FROM pg_statistic_ext statistics "
                "JOIN pg_namespace namespace ON namespace.oid = statistics.stxnamespace "
                "WHERE namespace.nspname = :schema"
                ") protected_objects"
            ),
            {"schema": schema},
        )
    )
    return relations, domains, unexpected_objects


def _database_url(*, host: str, port: str, database: str, user: str, password: str) -> str:
    return f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"


async def _validate_backup_database(config: Mapping[str, str]) -> None:
    engine = create_async_engine(
        _database_url(
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
        )
    )
    try:
        async with engine.begin() as connection:
            await validate_protected_backup_connection(connection, schema=config["schema"])
            relations, domains, unexpected_objects = await _catalog_scope(connection, config["schema"])
            validate_protected_scope(relations, domains, unexpected_objects)
    finally:
        await engine.dispose()


def _new_backup_id(recorded_at: datetime) -> str:
    return f"prb-{recorded_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(6)}"


def run_backup(*, reason: str, scope_ref: str) -> dict[str, Any]:
    if reason not in BACKUP_REASONS:
        raise ProtectedBackupOpsError("BACKUP_REASON_INVALID")
    _validate_logical_ref(scope_ref, "BACKUP_SCOPE_REF_INVALID")
    _validate_logical_ref(os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY_VERSION"], "BACKUP_KEY_VERSION_INVALID")
    _validate_logical_ref(os.environ["PROTECTED_BACKUP_ACL_POLICY_ID"], "BACKUP_ACL_POLICY_REF_INVALID")
    now = datetime.now(UTC)
    config = {
        "host": os.environ["PROTECTED_DB_HOST"],
        "port": os.getenv("PROTECTED_DB_PORT", "5432"),
        "database": os.environ["PROTECTED_DB_NAME"],
        "schema": os.environ["PROTECTED_DB_SCHEMA"],
        "user": os.environ["PROTECTED_DB_BACKUP_USER"],
        "password": os.environ["PROTECTED_DB_BACKUP_PASSWORD"],
    }
    asyncio.run(_validate_backup_database(config))
    backup_id = _new_backup_id(now)
    root = Path(os.environ["PROTECTED_BACKUP_HOST_ROOT"])
    destination = _artifact_path(root, backup_id, must_exist=False)
    temporary = root / f".{backup_id}.{secrets.token_hex(8)}.tmp"
    command = [
        "pg_dump",
        *_pg_connection_args(
            host=config["host"], port=config["port"], database=config["database"], user=config["user"]
        ),
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--schema",
        config["schema"],
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_pg_env(config["password"])
    )
    published = False
    try:
        if process.stdout is None:
            raise ProtectedBackupOpsError("BACKUP_DUMP_FAILED")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as encrypted:
            encrypt_stream(
                process.stdout,
                encrypted,
                encoded_key=os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY"],
                key_version=os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY_VERSION"],
            )
            encrypted.flush()
            os.fsync(encrypted.fileno())
        if process.wait() != 0:
            raise ProtectedBackupOpsError("BACKUP_DUMP_FAILED")
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise ProtectedBackupOpsError("BACKUP_ARTIFACT_STATE_INVALID") from error
        temporary.unlink()
        published = True
        directory_descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        digest = sha256()
        with destination.open("rb") as artifact:
            while chunk := artifact.read(_CHUNK_SIZE):
                digest.update(chunk)
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise
    return build_backup_receipt(
        backup_id=backup_id,
        recorded_at=now,
        reason=reason,
        scope_ref=scope_ref,
        encrypted_artifact_sha256=digest.hexdigest(),
        encryption_key_version=os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY_VERSION"],
        acl_policy_ref=os.environ["PROTECTED_BACKUP_ACL_POLICY_ID"],
    )


class _NullWriter:
    def write(self, data: bytes) -> int:
        return len(data)


async def _verify_restored_database(config: Mapping[str, str]) -> None:
    validate_restore_target(config["host"])
    schema_sql = quoted_identifier(config["schema"])
    engine = create_async_engine(
        _database_url(
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
        )
    )
    try:
        async with engine.begin() as connection:
            relations, domains, unexpected_objects = await _catalog_scope(connection, config["schema"])
            validate_protected_scope(relations, domains, unexpected_objects)
            head = await connection.scalar(text(f"SELECT version_num FROM {schema_sql}.alembic_version"))
            if head != PROTECTED_ALEMBIC_HEAD:
                raise ProtectedBackupOpsError("RESTORE_ALEMBIC_HEAD_MISMATCH")
            entry_rows = (
                await connection.execute(
                    text(
                        f"SELECT sequence, event_id, event_kind, operation_key, entry_body, "
                        f"previous_entry_sha256, entry_sha256, recorded_at "
                        f"FROM {schema_sql}.audit_entry ORDER BY sequence"
                    )
                )
            ).mappings()
            audit_head = (
                (
                    await connection.execute(
                        text(f"SELECT sequence, entry_sha256 FROM {schema_sql}.audit_head WHERE singleton")
                    )
                )
                .mappings()
                .one()
            )
            validate_audit_chain((dict(row) for row in entry_rows), dict(audit_head))
            artifacts = await connection.execute(
                text(
                    f"SELECT artifact.envelope, artifact.envelope_sha256, artifact.hmac_key_version, "
                    f"dataset.protected_artifact_sha256, dataset.hmac_key_version AS dataset_key_version "
                    f"FROM {schema_sql}.protected_artifact artifact "
                    f"JOIN {schema_sql}.protected_dataset dataset "
                    "ON dataset.dataset_id = artifact.dataset_id "
                    "AND dataset.dataset_version = artifact.dataset_version"
                )
            )
            for artifact in artifacts:
                if (
                    sha256(artifact.envelope).hexdigest() != artifact.envelope_sha256
                    or artifact.envelope_sha256 != artifact.protected_artifact_sha256
                    or artifact.hmac_key_version != artifact.dataset_key_version
                ):
                    raise ProtectedBackupOpsError("RESTORE_ARTIFACT_DIGEST_MISMATCH")
            for relation in EXPECTED_RELATIONS:
                await connection.scalar(text(f"SELECT count(*) FROM {schema_sql}.{quoted_identifier(relation)}"))
            await connection.execute(text(f"REVOKE ALL ON SCHEMA {schema_sql} FROM PUBLIC"))
            await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema_sql} FROM PUBLIC"))
            await connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema_sql} FROM PUBLIC"))
            await connection.execute(text(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema_sql} FROM PUBLIC"))
            await connection.execute(text(f"REVOKE ALL ON DOMAIN {schema_sql}.sha256_hex FROM PUBLIC"))
            unexpected_acl_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM ("
                    "SELECT namespace.nspowner AS owner_oid, acl.grantee "
                    "FROM pg_namespace namespace "
                    "CROSS JOIN LATERAL aclexplode(COALESCE(namespace.nspacl, "
                    "acldefault('n', namespace.nspowner))) acl "
                    "WHERE namespace.nspname = :schema "
                    "UNION ALL SELECT class.relowner, acl.grantee FROM pg_class class "
                    "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                    "CROSS JOIN LATERAL aclexplode(COALESCE(class.relacl, acldefault("
                    "CASE WHEN class.relkind = 'S' THEN 'S'::\"char\" ELSE 'r'::\"char\" END, "
                    "class.relowner))) acl "
                    "WHERE namespace.nspname = :schema "
                    "AND class.relkind IN ('r','p','v','m','S','f') "
                    "UNION ALL SELECT type.typowner, acl.grantee FROM pg_type type "
                    "JOIN pg_namespace namespace ON namespace.oid = type.typnamespace "
                    "CROSS JOIN LATERAL aclexplode(COALESCE(type.typacl, "
                    "acldefault('T', type.typowner))) acl "
                    "WHERE namespace.nspname = :schema AND type.typtype = 'd' "
                    "UNION ALL SELECT procedure.proowner, acl.grantee FROM pg_proc procedure "
                    "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                    "CROSS JOIN LATERAL aclexplode(COALESCE(procedure.proacl, "
                    "acldefault('f', procedure.proowner))) acl "
                    "WHERE namespace.nspname = :schema"
                    ") grants WHERE grants.grantee = 0 OR grants.grantee <> grants.owner_oid"
                ),
                {"schema": config["schema"]},
            )
            if unexpected_acl_count:
                raise ProtectedBackupOpsError("RESTORE_ACL_VALIDATION_FAILED")
    finally:
        await engine.dispose()


def run_restore_verify(*, backup_id: str) -> dict[str, str]:
    host = os.environ["PROTECTED_RESTORE_DB_HOST"]
    validate_restore_target(host)
    artifact = _artifact_path(Path(os.environ["PROTECTED_BACKUP_HOST_ROOT"]), backup_id, must_exist=True)
    key = os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY"]
    key_version = os.environ["PROTECTED_BACKUP_ENCRYPTION_KEY_VERSION"]
    with artifact.open("rb") as encrypted:
        decrypt_stream(encrypted, _NullWriter(), encoded_key=key, expected_key_version=key_version)

    restore = {
        "host": host,
        "port": os.getenv("PROTECTED_RESTORE_DB_PORT", "5432"),
        "database": os.environ["PROTECTED_RESTORE_DB_NAME"],
        "schema": os.environ["PROTECTED_DB_SCHEMA"],
        "user": os.environ["PROTECTED_RESTORE_DB_USER"],
        "password": os.environ["PROTECTED_RESTORE_DB_PASSWORD"],
    }
    command = [
        "pg_restore",
        *_pg_connection_args(
            host=restore["host"], port=restore["port"], database=restore["database"], user=restore["user"]
        ),
        "--exit-on-error",
        "--no-owner",
        "--no-acl",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_pg_env(restore["password"]),
    )
    try:
        if process.stdin is None:
            raise ProtectedBackupOpsError("RESTORE_FAILED")
        with artifact.open("rb") as encrypted:
            decrypt_stream(encrypted, process.stdin, encoded_key=key, expected_key_version=key_version)
        process.stdin.close()
        if process.wait() != 0:
            raise ProtectedBackupOpsError("RESTORE_FAILED")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    asyncio.run(_verify_restored_database(restore))
    return build_restore_receipt(backup_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    backup = subcommands.add_parser("backup")
    backup.add_argument("--reason", required=True, choices=BACKUP_REASONS)
    backup.add_argument("--scope-ref", required=True)
    restore = subcommands.add_parser("restore-verify")
    restore.add_argument("--backup-id", required=True)
    subcommands.add_parser("rotate-db")
    return parser.parse_args()


def main() -> None:
    try:
        args = parse_args()
        if args.operation == "backup":
            receipt = run_backup(reason=args.reason, scope_ref=args.scope_ref)
        elif args.operation == "restore-verify":
            receipt = run_restore_verify(backup_id=args.backup_id)
        else:
            receipt = rotate_database_credentials(_rotation_driver_from_environment())
        sys.stdout.write(serialize_public_receipt(receipt) + "\n")
    except (KeyError, ProtectedBackupOpsError) as error:
        code = str(error) if isinstance(error, ProtectedBackupOpsError) else "CONFIG_REQUIRED"
        sys.stderr.write(code + "\n")
        raise SystemExit(1) from None
    except Exception:
        sys.stderr.write("PROTECTED_OPERATION_FAILED\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
