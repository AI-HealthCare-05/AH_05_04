import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.release_validation.ai_one_cycle_smoke as smoke_module
from app.core.db.databases import get_db_session
from app.dependencies.services import get_chat_engine, get_guide_generator
from app.main import app, fastapi_app
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import ConfirmationStatus, ExtractedField, FieldType, OcrJob, OcrStatus
from app.models.prescriptions import Medication, Prescription
from app.models.profiles import Profile
from app.models.users import User
from app.release_validation.ai_one_cycle_smoke import (
    CleanupPendingError,
    GuardError,
    HttpFlowError,
    NetworkOneCycleRunner,
    RunStateStore,
    ScenarioError,
    SyntheticFixture,
    _calculate_live_read_timeout_seconds,
    _cleanup_root,
    _runtime_environment,
    build_synthetic_fixture,
    cleanup_synthetic_fixture,
    compute_input_fingerprint,
    load_scenario,
    review_safety,
    run_deterministic_one_cycle,
    validate_clova_url,
    validate_live_environment,
    verify_one_cycle,
    verify_prescription_input,
)
from app.services.chat_ai import ChatReplyOutput
from app.services.guide_ai.schemas import GuideGenerationResult
from app.tests.conftest import test_engine

# backend/app/tests/release_validation/에서 4단계 위가 backend/이며, subprocess로 새로
# 실행하는 파이썬은 pytest의 rootdir 기반 sys.path 삽입을 물려받지 않아 app 패키지를
# 직접 찾지 못한다. PYTHONPATH로 backend/를 명시해 `-m app...` import를 가능하게 한다.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "release_validation" / "scenarios"


def _load_real_scenario(filename: str) -> dict[str, Any]:
    return json.loads((_SCENARIO_ROOT / filename).read_text(encoding="utf-8"))


def _subprocess_env(base_env: Mapping[str, str]) -> dict[str, str]:
    process_env = dict(base_env)
    existing_pythonpath = process_env.get("PYTHONPATH")
    process_env["PYTHONPATH"] = (
        f"{_BACKEND_ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(_BACKEND_ROOT)
    )
    return process_env


def _create_symlink_or_skip(
    link: Path,
    target: Path,
) -> None:
    """Windows에서 symlink 권한이 없을 때만 테스트를 건너뜁니다."""
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable (WinError 1314)")
        raise


@pytest.mark.parametrize(
    (
        "llm_enabled",
        "clova_timeout",
        "structure_timeout",
        "openai_timeout",
        "expected",
    ),
    [
        # LLM OFF: max(CLOVA 20초, Guide·Chat 20초) + 여유 5초
        ("false", "20", "30", "20", 25.0),
        # LLM ON: max(CLOVA 20초 + OCR OpenAI 30초, Guide·Chat 20초)
        # + 여유 5초
        ("true", "20", "30", "20", 55.0),
        # Guide·Chat timeout이 OCR 합산보다 긴 경우도 검증합니다.
        ("true", "10", "15", "40", 45.0),
    ],
)
def test_live_read_timeout_combines_sequential_ocr_providers(
    llm_enabled: str,
    clova_timeout: str,
    structure_timeout: str,
    openai_timeout: str,
    expected: float,
) -> None:
    environment = {
        "OCR_STRUCTURE_LLM_ENABLED": llm_enabled,
        "CLOVA_OCR_TIMEOUT_SECONDS": clova_timeout,
        "OCR_STRUCTURE_TIMEOUT_SECONDS": structure_timeout,
        "OPENAI_TIMEOUT_SECONDS": openai_timeout,
    }

    assert _calculate_live_read_timeout_seconds(environment) == expected


def test_live_read_timeout_rejects_invalid_llm_flag() -> None:
    with pytest.raises(
        GuardError,
        match="OCR_STRUCTURE_LLM_ENABLED must be true or false",
    ):
        _calculate_live_read_timeout_seconds(
            {
                "OCR_STRUCTURE_LLM_ENABLED": "enabled",
            }
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CLOVA_OCR_TIMEOUT_SECONDS", "0"),
        ("OCR_STRUCTURE_TIMEOUT_SECONDS", "-1"),
        ("OPENAI_TIMEOUT_SECONDS", "not-a-number"),
    ],
)
def test_live_read_timeout_rejects_invalid_timeout(
    name: str,
    value: str,
) -> None:
    environment = {
        "OCR_STRUCTURE_LLM_ENABLED": "true",
        "CLOVA_OCR_TIMEOUT_SECONDS": "20",
        "OCR_STRUCTURE_TIMEOUT_SECONDS": "30",
        "OPENAI_TIMEOUT_SECONDS": "20",
        name: value,
    }

    with pytest.raises(
        GuardError,
        match=f"{name} must be a positive finite number",
    ):
        _calculate_live_read_timeout_seconds(environment)


def test_cli_rejects_non_uuid_run_id_before_any_state_change(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text("{}", encoding="utf-8")
    process_env = _subprocess_env(os.environ)
    process_env.pop("OPENAI_API_KEY", None)
    process_env.pop("CLOVA_OCR_SECRET", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.release_validation.ai_one_cycle_smoke",
            "--mode",
            "local-live-full",
            "--run-id",
            "not-a-uuid",
            "--base-url",
            "http://127.0.0.1:8000/api/v1",
            "--scenario",
            str(scenario_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=process_env,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 1
    assert json.loads(completed.stdout) == {
        "operation": "run",
        "run_id": "not-a-uuid",
        "mode": "local-live-full",
        "transport": "network",
        "execution": "FAIL",
        "failure_stage": "GUARD",
        "cleanup": "PASS",
        "evidence_qualified": False,
    }


def test_cli_argument_errors_emit_exactly_one_json_object() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.release_validation.ai_one_cycle_smoke",
            "--mode",
            "local-live-full",
            "--run-id",
            str(uuid4()),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(os.environ),
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 1
    assert json.loads(completed.stdout)["failure_stage"] == "GUARD"


def _scenario_payload(*, version: str = "ai-one-cycle-v1") -> dict[str, object]:
    return {
        "scenario_version": version,
        "fixture_path": None,
        "fixture_sha256": None,
        "prescribed_date": "2026-08-21",
        "medications": [
            {
                "display_order": 1,
                "medication_name": "합성의약품 에이",
                "strength_text": "100mg",
                "dose_value": "1",
                "dose_unit": "정",
                "frequency_per_day": 2,
                "timing_text": "식후",
                "duration_days": 3,
            }
        ],
        "expected_field_identities": [],
        "question": "이 합성 처방은 하루에 몇 번 복용하도록 되어 있나요?",
        "expected_answer_facts": ["합성의약품 에이:1일 2회"],
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://tenant.apigw.ntruss.com/ocr",
        "https://apigw.ntruss.com/ocr",
        "https://user@tenant.apigw.ntruss.com/ocr",
        "https://tenant.apigw.ntruss.com/ocr#fragment",
        "https://tenant.example.com/ocr",
    ],
)
def test_clova_url_guard_rejects_non_provider_urls_without_echoing_value(url: str) -> None:
    with pytest.raises(GuardError) as raised:
        validate_clova_url(url)

    assert url not in str(raised.value)


def test_staging_guard_is_positive_allow_gate_and_rejects_provider_key() -> None:
    env = {
        "ENV": "staging",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "RELEASE_VALIDATION_STAGING_API_HOST": "staging.example.test",
        "RELEASE_VALIDATION_STAGING_DB_HOST": "db.staging.internal",
        "RELEASE_VALIDATION_STAGING_DB_NAME": "app_staging",
        "DB_HOST": "db.staging.internal",
        "DB_NAME": "app_staging",
        "OPENAI_API_KEY": "present-but-must-not-be-read",
    }

    with pytest.raises(GuardError) as raised:
        validate_live_environment(
            mode="staging-live",
            base_url="https://staging.example.test/api/v1",
            env=env,
            commit_sha="a" * 40,
            image_repo_digest=None,
        )

    assert "OPENAI_API_KEY" in str(raised.value)
    assert env["OPENAI_API_KEY"] not in str(raised.value)


@pytest.mark.parametrize(
    "image_repo_digest",
    ["placeholder", "sha256:1234", f"sha256:{'A' * 64}", f"sha256:{'0' * 64}", f"sha512:{'a' * 64}"],
)
def test_staging_guard_rejects_noncanonical_image_repository_digest(image_repo_digest: str) -> None:
    env = {
        "ENV": "staging",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "RELEASE_VALIDATION_STAGING_API_HOST": "staging.example.test",
        "RELEASE_VALIDATION_STAGING_DB_HOST": "staging-db",
        "RELEASE_VALIDATION_STAGING_DB_NAME": "validation",
        "DB_HOST": "staging-db",
        "DB_NAME": "validation",
    }

    with pytest.raises(GuardError):
        validate_live_environment(
            mode="staging-live",
            base_url="https://staging.example.test/api/v1",
            env=env,
            commit_sha=None,
            image_repo_digest=image_repo_digest,
        )


def test_staging_guard_accepts_canonical_image_repository_digest_without_commit() -> None:
    env = {
        "ENV": "staging",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "RELEASE_VALIDATION_STAGING_API_HOST": "staging.example.test",
        "RELEASE_VALIDATION_STAGING_DB_HOST": "staging-db",
        "RELEASE_VALIDATION_STAGING_DB_NAME": "validation",
        "DB_HOST": "staging-db",
        "DB_NAME": "validation",
    }

    validated = validate_live_environment(
        mode="staging-live",
        base_url="https://staging.example.test/api/v1",
        env=env,
        commit_sha=None,
        image_repo_digest=f"sha256:{'a' * 64}",
    )

    assert validated.environment == "staging"


@pytest.mark.parametrize("mode", ["local-preflight", "local-live-full"])
def test_local_live_guard_does_not_require_provider_credentials(mode: str, tmp_path: Path) -> None:
    env = {
        "ENV": "local",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "CLOVA_OCR_INVOKE_URL": "https://tenant.apigw.ntruss.com/ocr",
        "STORAGE_DIR": str(tmp_path),
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
    }

    validated = validate_live_environment(
        mode=mode,
        base_url="http://127.0.0.1:8000/api/v1",
        env=env,
        commit_sha=None,
        image_repo_digest=None,
    )

    assert validated.environment == "local"
    assert validated.storage_dir == tmp_path.resolve()


@pytest.mark.parametrize("mode", ["local-preflight", "local-live-full"])
def test_local_runtime_environment_excludes_provider_credentials(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELEASE_VALIDATION_RUNNER", raising=False)
    monkeypatch.delenv("CLOVA_OCR_SECRET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_USER", "synthetic-runner")
    monkeypatch.setenv("DB_PASSWORD", uuid4().hex)
    monkeypatch.setenv("DB_NAME", "synthetic-runner-db")
    monkeypatch.setenv("CLOVA_OCR_INVOKE_URL", "https://tenant.apigw.ntruss.com/ocr")
    monkeypatch.setenv(
        "OCR_STRUCTURE_LLM_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "OCR_STRUCTURE_TIMEOUT_SECONDS",
        "30",
    )
    runtime_environment = _runtime_environment(mode)

    assert runtime_environment["RELEASE_VALIDATION_RUNNER"] == "1"
    assert runtime_environment["OCR_STRUCTURE_LLM_ENABLED"] == "true"
    assert runtime_environment["OCR_STRUCTURE_TIMEOUT_SECONDS"] == "30"
    assert "CLOVA_OCR_SECRET" not in runtime_environment
    assert "OPENAI_API_KEY" not in runtime_environment


def test_local_runner_does_not_load_provider_credentials_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=synthetic-openai-sentinel\nCLOVA_OCR_SECRET=synthetic-clova-sentinel\n",
        encoding="utf-8",
    )
    process_env = os.environ.copy()
    process_env.pop("OPENAI_API_KEY", None)
    process_env.pop("CLOVA_OCR_SECRET", None)
    process_env.update(
        {
            "ENV": "local",
            "RELEASE_VALIDATION_ALLOWED": "1",
            "CLOVA_OCR_INVOKE_URL": "https://tenant.apigw.ntruss.com/ocr",
            "STORAGE_DIR": str(tmp_path),
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "DB_USER": "synthetic-runner",
            "DB_PASSWORD": uuid4().hex,
            "DB_NAME": "synthetic-runner-db",
        }
    )
    repository_root = Path(__file__).resolve().parents[3]
    process_env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(repository_root), process_env.get("PYTHONPATH"))))
    script = """
import json
from app.release_validation.ai_one_cycle_smoke import _runtime_environment

runtime = _runtime_environment("local-live-full")
from app.core import config

print(json.dumps({
    "runner_marker": runtime.get("RELEASE_VALIDATION_RUNNER"),
    "runtime_has_openai": "OPENAI_API_KEY" in runtime,
    "runtime_has_clova": "CLOVA_OCR_SECRET" in runtime,
    "config_openai": config.OPENAI_API_KEY,
    "config_clova": config.CLOVA_OCR_SECRET,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=process_env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "runner_marker": "1",
        "runtime_has_openai": False,
        "runtime_has_clova": False,
        "config_openai": "sk-not-configured",
        "config_clova": "",
    }


@pytest.mark.parametrize("credential_name", ["CLOVA_OCR_SECRET", "OPENAI_API_KEY"])
def test_local_runner_rejects_inherited_provider_credentials_without_exposing_values(
    credential_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "synthetic-provider-credential-must-not-appear"
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_USER", "synthetic-runner")
    monkeypatch.setenv("DB_PASSWORD", uuid4().hex)
    monkeypatch.setenv("DB_NAME", "synthetic-runner-db")
    monkeypatch.setenv("CLOVA_OCR_INVOKE_URL", "https://tenant.apigw.ntruss.com/ocr")
    monkeypatch.delenv("CLOVA_OCR_SECRET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(credential_name, sentinel)

    with pytest.raises(GuardError) as exc_info:
        _runtime_environment("local-live-full")

    assert credential_name not in str(exc_info.value)
    assert sentinel not in str(exc_info.value)


def test_local_live_guard_rejects_container_only_database_identity(tmp_path: Path) -> None:
    env = {
        "ENV": "local",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "CLOVA_OCR_INVOKE_URL": "https://tenant.apigw.ntruss.com/ocr",
        "STORAGE_DIR": str(tmp_path),
        "DB_HOST": "postgres",
        "DB_PORT": "5432",
    }

    with pytest.raises(GuardError):
        validate_live_environment(
            mode="local-live-full",
            base_url="http://127.0.0.1:8000/api/v1",
            env=env,
            commit_sha=None,
            image_repo_digest=None,
        )


def test_scenario_loader_rejects_placeholder_live_manifest_before_provider_call(tmp_path: Path) -> None:
    payload = _scenario_payload(version="ai-one-cycle-clova-openai-v1")
    payload["fixture_path"] = "PLACEHOLDER"
    payload["fixture_sha256"] = "sha256:" + "0" * 64
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioError):
        load_scenario(scenario_path, mode="local-live-full", repository_root=tmp_path)


def test_input_fingerprint_uses_canonical_scenario_values() -> None:
    payload = _scenario_payload()

    assert compute_input_fingerprint(payload) == (
        "sha256:eaecc304bae14a164c50c3c2723dd3669337a3be7fde29500b7974bcf5d30ec2"
    )


def test_run_state_is_exclusive_atomic_and_private(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    run_id = str(uuid4())
    store = RunStateStore.create(state_root, run_id, {"run_id": run_id, "ids": {}})

    if smoke_module._STRICT_POSIX_FILE_MODES:
        assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        RunStateStore.create(state_root, run_id, {"run_id": run_id})

    store.update(in_flight_stage="AUTH", cleanup_not_before="2026-08-25T10:00:00+00:00")
    if smoke_module._STRICT_POSIX_FILE_MODES:
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.read()["in_flight_stage"] == "AUTH"
    assert list(state_root.glob(f".{run_id}.*.tmp")) == []


def test_directory_fsync_is_skipped_when_not_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_module,
        "_DIRECTORY_FSYNC_SUPPORTED",
        False,
    )

    def fail_if_opened(
        *_args: object,
        **_kwargs: object,
    ) -> int:
        raise AssertionError("directory must not be opened for fsync")

    monkeypatch.setattr(
        smoke_module.os,
        "open",
        fail_if_opened,
    )

    RunStateStore._fsync_directory(tmp_path)


def test_cleanup_is_pending_during_in_flight_grace_without_calling_cleanup(tmp_path: Path) -> None:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    store = RunStateStore.create(
        tmp_path,
        run_id,
        {
            "run_id": run_id,
            "in_flight_stage": "GUIDE",
            "cleanup_not_before": (now + timedelta(minutes=1)).isoformat(),
        },
    )

    called = False

    def cleanup() -> tuple[int, int]:
        nonlocal called
        called = True
        return 0, 0

    result, exit_code = store.cleanup_only(now=now, cleanup=cleanup)

    assert exit_code == 3
    assert result["cleanup"] == "PENDING"
    assert result["verification"] == "UNAVAILABLE"
    assert result["remaining_rows"] is None
    assert result["remaining_files"] is None
    assert called is False


class _TtyBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def test_safety_review_keeps_bodies_only_on_tty_and_returns_codes(capsys: pytest.CaptureFixture[str]) -> None:
    guide_body = "PRIVATE_GUIDE_BODY"
    chat_body = "PRIVATE_CHAT_BODY"
    tty_input = _TtyBuffer("yes\nyes\nyes\nyes\nyes\nyes\nno\nyes\nyes\nyes\n")
    tty_output = _TtyBuffer()

    result = review_safety(
        guide_content=guide_body,
        chat_content=chat_body,
        tty_input=tty_input,
        tty_output=tty_output,
    )

    captured = capsys.readouterr()
    assert result == {
        "safety_review": {"guide": "PASS", "chat": "FAIL", "overall": "FAIL"},
        "failed_safety_codes": ["CHAT_UNLISTED_MEDICATION"],
    }
    assert guide_body in tty_output.getvalue()
    assert chat_body in tty_output.getvalue()
    assert guide_body not in captured.out + captured.err + json.dumps(result)
    assert chat_body not in captured.out + captured.err + json.dumps(result)


def test_safety_review_without_tty_fails_closed() -> None:
    result = review_safety(
        guide_content="private guide",
        chat_content="private chat",
        tty_input=StringIO("yes\n" * 10),
        tty_output=StringIO(),
    )

    assert result["safety_review"] == {"guide": "FAIL", "chat": "FAIL", "overall": "FAIL"}
    assert result["failed_safety_codes"] == ["GUIDE_UNCONFIRMED", "CHAT_UNCONFIRMED"]


@pytest.mark.asyncio
async def test_fixture_builder_commits_completed_confirmed_synthetic_fixture() -> None:
    run_id = uuid4()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    fixture = await build_synthetic_fixture(factory, run_id=run_id, scenario=_scenario_payload())

    assert isinstance(fixture, SyntheticFixture)
    assert run_id.hex[:18] in fixture.email
    async with factory() as verification_session:
        user = await verification_session.get(User, fixture.user_id)
        document = await verification_session.get(MedicalDocument, fixture.document_id)
        job = await verification_session.get(OcrJob, fixture.ocr_job_id)
        fields = list(
            (
                await verification_session.scalars(
                    select(ExtractedField).where(ExtractedField.ocr_job_id == fixture.ocr_job_id)
                )
            ).all()
        )
        assert user is not None and user.email == fixture.email
        assert document is not None and document.uploaded_by == fixture.user_id
        assert job is not None and job.ocr_status == OcrStatus.COMPLETED
        assert len(fields) == 8
        assert {field.confirmation_status for field in fields} == {ConfirmationStatus.CONFIRMED}

        await verification_session.execute(
            delete(ExtractedField).where(ExtractedField.ocr_job_id == fixture.ocr_job_id)
        )
        await verification_session.execute(delete(OcrJob).where(OcrJob.id == fixture.ocr_job_id))
        await verification_session.execute(delete(MedicalDocument).where(MedicalDocument.id == fixture.document_id))
        await verification_session.execute(delete(Profile).where(Profile.user_id == fixture.user_id))
        await verification_session.execute(delete(User).where(User.id == fixture.user_id))
        await verification_session.commit()


@pytest.mark.asyncio
async def test_staging_scenario_without_strength_builds_fixture() -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    scenario = _load_real_scenario("ai-one-cycle-v1.json")

    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=scenario)

    try:
        async with factory() as verification_session:
            fields = list(
                (
                    await verification_session.scalars(
                        select(ExtractedField).where(ExtractedField.ocr_job_id == fixture.ocr_job_id)
                    )
                ).all()
            )
        assert len(fields) == 7
        assert FieldType.MEDICATION_STRENGTH not in {field.field_type for field in fields}
    finally:
        await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)


@pytest.mark.asyncio
async def test_local_live_scenario_builds_separate_name_and_strength_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _load_real_scenario("ai-one-cycle-clova-openai-v1.json")
    scenario["resolved_fixture_path"] = scenario["fixture_path"]
    run_id = str(uuid4())
    state = RunStateStore.create(
        tmp_path / "state",
        run_id,
        {"run_id": run_id, "ids": {"document_id": str(uuid4())}},
    )
    runner = NetworkOneCycleRunner(
        base_url="http://127.0.0.1:8000/api/v1",
        state=state,
        read_timeout_seconds=5,
    )
    confirmed_values: dict[str, str] = {}

    async def fake_preflight(**_kwargs: object) -> dict[str, object]:
        runner._preflight_fields = [
            {
                "field_id": "medication-name-field",
                "medication_index": 1,
                "field_type": "MEDICATION_NAME",
            },
            {
                "field_id": "medication-strength-field",
                "medication_index": 1,
                "field_type": "MEDICATION_STRENGTH",
            },
        ]
        return {"preflight": "READY", "field_count": 2}

    async def fake_request(
        _stage: str,
        _method: str,
        request_path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        json_body = kwargs["json_body"]
        assert isinstance(json_body, dict)
        confirmed_values[request_path] = str(json_body["confirmed_value"])
        return {}

    async def fake_generation(**_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(runner, "run_preflight", fake_preflight)
    monkeypatch.setattr(runner, "_request", fake_request)
    monkeypatch.setattr(runner, "_run_generation", fake_generation)

    await runner.run_local_full(
        email="synthetic@example.invalid",
        password="Password123!",
        scenario=scenario,
    )

    assert confirmed_values == {
        "/extracted-fields/medication-name-field": "합성의약품에이정",
        "/extracted-fields/medication-strength-field": "100mg",
    }


@pytest.mark.asyncio
async def test_fixture_builder_uses_preallocated_user_id_for_crash_recovery() -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    user_id = uuid4()

    fixture = await build_synthetic_fixture(
        factory,
        run_id=uuid4(),
        scenario=_scenario_payload(),
        user_id=user_id,
    )

    assert fixture.user_id == user_id
    assert await cleanup_synthetic_fixture(factory, user_id=user_id) == 0


@pytest.mark.asyncio
async def test_cleanup_only_recovers_fixture_committed_before_state_marker_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    run_id = uuid4()
    state_parent = tmp_path / "state-parent"
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(b"approved-synthetic-candidate")
    draft = _scenario_payload(version="ai-one-cycle-clova-openai-v1")
    draft["expected_field_identities"] = [[0, "PRESCRIBED_DATE"]]
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    runtime_env = {
        "ENV": "local",
        "RELEASE_VALIDATION_ALLOWED": "1",
        "CLOVA_OCR_INVOKE_URL": "https://tenant.apigw.ntruss.com/ocr",
        "STORAGE_DIR": str(storage_dir),
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_NAME": "test",
        "CLOVA_OCR_TIMEOUT_SECONDS": "20",
        "OPENAI_TIMEOUT_SECONDS": "20",
    }
    monkeypatch.setenv("RELEASE_VALIDATION_STATE_DIR", str(state_parent))
    monkeypatch.setattr(smoke_module, "_runtime_environment", lambda _mode: runtime_env)
    monkeypatch.setattr("app.core.db.databases.AsyncSessionFactory", factory)
    original_update = smoke_module.RunStateStore.update
    crash_enabled = True

    def crash_after_fixture_commit(store: RunStateStore, **values: object) -> None:
        if crash_enabled and values.get("in_flight_stage") is None:
            raise RuntimeError("simulated crash after fixture commit")
        original_update(store, **values)

    monkeypatch.setattr(smoke_module.RunStateStore, "update", crash_after_fixture_commit)
    args = argparse.Namespace(
        mode="local-preflight",
        run_id=str(run_id),
        base_url="http://127.0.0.1:8000/api/v1",
        scenario=None,
        candidate_image=str(candidate),
        scenario_draft=str(draft_path),
        commit_sha=None,
        image_repo_digest=None,
        cleanup_only=False,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        await smoke_module._execute(args, run_id)

    crash_enabled = False
    root = smoke_module._state_root("local-preflight")
    store = RunStateStore.open(root, str(run_id))
    state = store.read()
    user_id = state["user_id"]
    store.update(cleanup_not_before=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    validated = smoke_module.validate_cleanup_environment(
        mode="local-preflight",
        base_url=args.base_url,
        env=runtime_env,
    )

    result, exit_code = await smoke_module._cleanup_only(args=args, validated=validated, store=store)

    assert exit_code == 0
    assert result["cleanup"] == "PASS"
    assert not store.path.exists()
    async with factory() as verification_session:
        assert await verification_session.get(User, user_id) is None


@pytest.mark.asyncio
async def test_cleanup_deletes_only_the_exact_synthetic_root() -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    target = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    survivor = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())

    remaining_rows = await cleanup_synthetic_fixture(factory, user_id=target.user_id)

    assert remaining_rows == 0
    async with factory() as verification_session:
        assert await verification_session.get(User, target.user_id) is None
        assert await verification_session.get(User, survivor.user_id) is not None
    assert await cleanup_synthetic_fixture(factory, user_id=survivor.user_id) == 0


@pytest.mark.asyncio
async def test_network_runner_uses_real_tcp_and_preserves_http_id_order(tmp_path: Path) -> None:
    document_id = str(uuid4())
    prescription_id = str(uuid4())
    guide_id = str(uuid4())
    session_id = str(uuid4())
    user_message_id = str(uuid4())
    assistant_message_id = str(uuid4())
    paths: list[str] = []
    prescription_checks: list[tuple[str, str, int]] = []

    async def check_prescription(created_prescription_id: str, created_document_id: str) -> None:
        prescription_checks.append((created_prescription_id, created_document_id, len(paths)))

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = (await reader.readline()).decode("ascii")
        _, path, _ = request_line.split(" ", 2)
        paths.append(path)
        content_length = 0
        while line := await reader.readline():
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            if name.lower() == "content-length":
                content_length = int(value.strip())
        if content_length:
            await reader.readexactly(content_length)
        payloads = {
            "/api/v1/auth/login": {"access_token": "synthetic-token"},
            f"/api/v1/documents/{document_id}/prescription": {"data": {"prescription_id": prescription_id}},
            "/api/v1/guides": {
                "data": {
                    "guide_id": guide_id,
                    "generation_status": "COMPLETED",
                    "content": "private guide",
                    "model_name": "real-model-id",
                    "prompt_version": "guide-prompt-v3",
                }
            },
            f"/api/v1/prescriptions/{prescription_id}/chat-sessions": {"data": {"session_id": session_id}},
            f"/api/v1/chat-sessions/{session_id}/messages": {
                "data": {
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "generation_status": "COMPLETED",
                    "content": "private chat",
                    "model_name": "real-model-id",
                    "prompt_version": "chat-prompt-v2",
                }
            },
        }
        body = json.dumps(payloads[path]).encode()
        # NoStoreMiddleware가 /api/v1/* 전체에 no-store를 적용하므로 auth/login도 포함합니다.
        cache = b"Cache-Control: no-store\r\n"
        status_line = b"HTTP/1.1 200 OK\r\n" if path.endswith("/auth/login") else b"HTTP/1.1 201 Created\r\n"
        writer.write(
            status_line
            + b"Content-Type: application/json\r\n"
            + cache
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    validated = validate_live_environment(
        mode="local-live-full",
        base_url=f"http://127.0.0.1:{port}/api/v1",
        env={
            "ENV": "local",
            "RELEASE_VALIDATION_ALLOWED": "1",
            "CLOVA_OCR_INVOKE_URL": "https://tenant.apigw.ntruss.com/ocr",
            "STORAGE_DIR": str(tmp_path),
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
        },
        commit_sha=None,
        image_repo_digest=None,
    )
    run_id = str(uuid4())
    store = RunStateStore.create(tmp_path, run_id, {"run_id": run_id, "ids": {}})
    try:
        async with server:
            async with NetworkOneCycleRunner(
                base_url=validated.base_url,
                state=store,
                read_timeout_seconds=5,
                prescription_check=check_prescription,
            ) as runner:
                result = await runner.run_staging_fixture(
                    email="synthetic@example.invalid",
                    password="Password123!",
                    document_id=document_id,
                    question="synthetic question",
                )
    finally:
        server.close()
        await server.wait_closed()

    assert paths == [
        "/api/v1/auth/login",
        f"/api/v1/documents/{document_id}/prescription",
        "/api/v1/guides",
        f"/api/v1/prescriptions/{prescription_id}/chat-sessions",
        f"/api/v1/chat-sessions/{session_id}/messages",
    ]
    assert result["transport"] == "network"
    assert result["ids"] == {
        "document_id": document_id,
        "prescription_id": prescription_id,
        "guide_id": guide_id,
        "session_id": session_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }
    assert result["guide_content"] == "private guide"
    assert result["chat_content"] == "private chat"
    assert prescription_checks == [(prescription_id, document_id, 2)]
    assert store.read()["in_flight_stage"] is None


@pytest.mark.asyncio
async def test_prescription_input_mismatch_stops_before_guide_request(tmp_path: Path) -> None:
    document_id = str(uuid4())
    prescription_id = str(uuid4())
    paths: list[str] = []

    async def reject_input(_prescription_id: str, _document_id: str) -> None:
        raise HttpFlowError("PRESCRIPTION_INPUT")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _, path, _ = (await reader.readline()).decode("ascii").split(" ", 2)
        paths.append(path)
        content_length = 0
        while line := await reader.readline():
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            if name.lower() == "content-length":
                content_length = int(value.strip())
        if content_length:
            await reader.readexactly(content_length)
        payload: dict[str, Any] = (
            {"access_token": "synthetic-token"}
            if path.endswith("/auth/login")
            else {"data": {"prescription_id": prescription_id}}
        )
        body = json.dumps(payload).encode()
        status = b"200 OK" if path.endswith("/auth/login") else b"201 Created"
        # NoStoreMiddleware가 /api/v1/* 전체에 no-store를 적용하므로 auth/login도 포함합니다.
        cache = b"Cache-Control: no-store\r\n"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: application/json\r\n"
            + cache
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    run_id = str(uuid4())
    store = RunStateStore.create(tmp_path, run_id, {"run_id": run_id, "ids": {}})
    try:
        async with server:
            async with NetworkOneCycleRunner(
                base_url=f"http://127.0.0.1:{port}/api/v1",
                state=store,
                read_timeout_seconds=5,
                prescription_check=reject_input,
            ) as runner:
                with pytest.raises(HttpFlowError, match="PRESCRIPTION_INPUT"):
                    await runner.run_staging_fixture(
                        email="synthetic@example.invalid",
                        password="Password123!",
                        document_id=document_id,
                        question="private question",
                    )
    finally:
        server.close()
        await server.wait_closed()

    assert paths == [
        "/api/v1/auth/login",
        f"/api/v1/documents/{document_id}/prescription",
    ]


@pytest.mark.asyncio
async def test_lost_upload_cleanup_requires_exactly_one_matching_new_file(tmp_path: Path) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    source = b"approved-synthetic-source"
    source_sha = "sha256:" + hashlib.sha256(source).hexdigest()
    run_id = str(uuid4())
    store = RunStateStore.create(
        tmp_path / "state",
        run_id,
        {
            "run_id": run_id,
            "ids": {},
            "storage_baseline": [],
            "source_image_sha256": source_sha,
            "transport_failed_at": datetime.now(UTC).isoformat(),
            "file_cleanup": "NOT_STARTED",
        },
    )

    with pytest.raises(CleanupPendingError):
        await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)

    first = tmp_path / "orphan-one.png"
    second = tmp_path / "orphan-two.png"
    first.write_bytes(source)
    second.write_bytes(source)
    with pytest.raises(CleanupPendingError):
        await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)

    second.unlink()
    with pytest.raises(CleanupPendingError):
        await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)

    first.unlink()
    owned = tmp_path / f"{fixture.document_id}.png"
    owned.write_bytes(source)
    rows, files = await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)
    assert (rows, files) == (0, 0)
    assert not owned.exists()
    assert store.read()["file_cleanup"] == "DONE"


@pytest.mark.asyncio
async def test_lost_upload_cleanup_does_not_delete_other_user_same_sha_file(tmp_path: Path) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    other_fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    content = b"approved-synthetic-source"
    content_sha = "sha256:" + hashlib.sha256(content).hexdigest()
    other_user_file = tmp_path / f"{other_fixture.document_id}.png"
    other_user_file.write_bytes(content)
    run_id = str(uuid4())
    store = RunStateStore.create(
        tmp_path / "state",
        run_id,
        {
            "run_id": run_id,
            "ids": {},
            "storage_baseline": [],
            "source_image_sha256": content_sha,
            "transport_failed_at": datetime.now(UTC).isoformat(),
            "file_cleanup": "NOT_STARTED",
        },
    )

    try:
        with pytest.raises(CleanupPendingError):
            await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)

        assert other_user_file.read_bytes() == content
        assert store.read()["file_cleanup"] == "NOT_STARTED"
        async with factory() as verification_session:
            assert await verification_session.get(User, other_fixture.user_id) is not None
    finally:
        other_user_file.unlink(missing_ok=True)
        await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)
        await cleanup_synthetic_fixture(factory, user_id=other_fixture.user_id)


@pytest.mark.asyncio
async def test_delete_intent_cleanup_does_not_follow_symlink_to_other_user_file(tmp_path: Path) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    other_fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    content = b"approved-synthetic-source"
    content_sha = "sha256:" + hashlib.sha256(content).hexdigest()
    target = tmp_path / f"{other_fixture.document_id}.png"
    target.write_bytes(content)
    tracked = tmp_path / f"{fixture.document_id}.png"
    run_id = str(uuid4())
    store = RunStateStore.create(
        tmp_path / "state",
        run_id,
        {
            "run_id": run_id,
            "ids": {"document_id": str(fixture.document_id)},
            "tracked_file_path": str(tracked),
            "tracked_file_sha256": content_sha,
            "file_cleanup": "DELETE_INTENT",
        },
    )

    try:
        _create_symlink_or_skip(tracked, target)

        with pytest.raises(CleanupPendingError):
            await _cleanup_root(factory, user_id=fixture.user_id, storage_dir=tmp_path, store=store)

        assert target.read_bytes() == content
        assert tracked.is_symlink()
        assert store.read()["file_cleanup"] == "DELETE_INTENT"
        async with factory() as verification_session:
            assert await verification_session.get(User, other_fixture.user_id) is not None
    finally:
        tracked.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)
        await cleanup_synthetic_fixture(factory, user_id=other_fixture.user_id)


@pytest.mark.asyncio
async def test_document_cleanup_does_not_follow_storage_symlink(tmp_path: Path) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=_scenario_payload())
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / f"{fixture.document_id}.png"
    content = b"approved-synthetic-source"
    target.write_bytes(content)
    candidate = tmp_path / f"{fixture.document_id}.png"
    run_id = str(uuid4())
    store = RunStateStore.create(
        tmp_path / "state",
        run_id,
        {
            "run_id": run_id,
            "ids": {"document_id": str(fixture.document_id)},
            "file_cleanup": "NOT_STARTED",
        },
    )

    try:
        _create_symlink_or_skip(candidate, target)

        rows, files = await _cleanup_root(
            factory,
            user_id=fixture.user_id,
            storage_dir=tmp_path,
            store=store,
        )

        assert rows == 0
        assert files > 0
        assert target.read_bytes() == content
        assert candidate.is_symlink()
        assert store.read()["file_cleanup"] == "NOT_STARTED"
    finally:
        candidate.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)


@pytest.mark.asyncio
async def test_preflight_stops_after_ocr_get_and_never_calls_openai_paths(tmp_path: Path) -> None:
    document_id = str(uuid4())
    job_id = str(uuid4())
    paths: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _, path, _ = (await reader.readline()).decode("ascii").split(" ", 2)
        paths.append(path)
        content_length = 0
        while line := await reader.readline():
            if line == b"\r\n":
                break
            name, value = line.decode("ascii").split(":", 1)
            if name.lower() == "content-length":
                content_length = int(value.strip())
        if content_length:
            await reader.readexactly(content_length)
        payload, status = {
            "/api/v1/auth/login": ({"access_token": "synthetic-token"}, "200 OK"),
            "/api/v1/documents": ({"data": {"document_id": document_id}}, "201 Created"),
            f"/api/v1/documents/{document_id}/ocr-jobs": (
                {"data": {"job_id": job_id, "fields": []}},
                "202 Accepted",
            ),
            f"/api/v1/ocr-jobs/{job_id}": (
                {
                    "data": {
                        "job_id": job_id,
                        "ocr_status": "COMPLETED",
                        "fields": [{"medication_index": 0, "field_type": "PRESCRIBED_DATE"}],
                    }
                },
                "200 OK",
            ),
        }[path]
        body = json.dumps(payload).encode()
        # NoStoreMiddleware가 /api/v1/* 전체에 no-store를 적용하므로 auth/login도 포함합니다.
        cache = b"Cache-Control: no-store\r\n"
        writer.write(
            f"HTTP/1.1 {status}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + cache
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(b"synthetic-png-candidate")
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    run_id = str(uuid4())
    store = RunStateStore.create(tmp_path / "state", run_id, {"run_id": run_id, "ids": {}})
    try:
        async with server:
            async with NetworkOneCycleRunner(
                base_url=f"http://127.0.0.1:{port}/api/v1", state=store, read_timeout_seconds=5
            ) as runner:
                result = await runner.run_preflight(
                    email="synthetic@example.invalid",
                    password="Password123!",
                    candidate_image=candidate,
                    expected_field_identities=[[0, "PRESCRIBED_DATE"]],
                )
    finally:
        server.close()
        await server.wait_closed()

    assert paths == [
        "/api/v1/auth/login",
        "/api/v1/documents",
        f"/api/v1/documents/{document_id}/ocr-jobs",
        f"/api/v1/ocr-jobs/{job_id}",
    ]
    assert result["preflight"] == "READY"
    assert result["field_identities_match"] is True
    assert result["field_count"] == 1
    assert "fields" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_filename", "expected_strength"),
    [
        ("ai-one-cycle-v1.json", None),
        ("ai-one-cycle-clova-openai-v1.json", "100mg"),
    ],
)
async def test_db_verifiers_accept_optional_strength_from_real_scenarios(
    scenario_filename: str,
    expected_strength: str | None,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    scenario = _load_real_scenario(scenario_filename)
    medication = scenario["medications"][0]
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=scenario)
    now = datetime.now(UTC)
    async with factory() as session:
        document = await session.get(MedicalDocument, fixture.document_id)
        assert document is not None
        assert document.profile_id is not None
        prescription = Prescription(
            document_id=fixture.document_id,
            source_ocr_job_id=fixture.ocr_job_id,
            profile_id=document.profile_id,
            prescribed_date=datetime(2026, 8, 21, tzinfo=UTC).date(),
            confirmed_at=now,
        )
        session.add(prescription)
        await session.flush()
        session.add(
            Medication(
                prescription_id=prescription.id,
                medication_name=medication["medication_name"],
                strength_text=expected_strength,
                dose_value=Decimal(str(medication["dose_value"])),
                dose_unit=medication["dose_unit"],
                frequency_per_day=medication["frequency_per_day"],
                timing_text=medication["timing_text"],
                duration_days=medication["duration_days"],
                display_order=medication["display_order"],
            )
        )
        guide = Guide(
            prescription_id=prescription.id,
            profile_id=prescription.profile_id,
            generation_status=GuideGenerationStatus.COMPLETED,
            content="private guide",
            model_name="gpt-4o-mini-actual",
            prompt_version="guide-prompt-v3",
            completed_at=now,
        )
        chat_session = ChatSession(prescription_id=prescription.id, profile_id=prescription.profile_id)
        session.add_all([guide, chat_session])
        await session.flush()
        session.add_all(
            [
                ChatMessage(
                    session_id=chat_session.id,
                    message_seq=1,
                    role=ChatRole.USER,
                    content=str(scenario["question"]),
                    generation_status=ChatGenerationStatus.COMPLETED,
                ),
                ChatMessage(
                    session_id=chat_session.id,
                    message_seq=2,
                    role=ChatRole.ASSISTANT,
                    content="private chat",
                    generation_status=ChatGenerationStatus.COMPLETED,
                    model_name="gpt-4o-mini-actual",
                    prompt_version="chat-prompt-v2",
                    completed_at=now,
                ),
            ]
        )
        await session.commit()
        ids = {
            "prescription_id": str(prescription.id),
            "guide_id": str(guide.id),
            "session_id": str(chat_session.id),
        }

    await verify_prescription_input(
        factory,
        prescription_id=str(prescription.id),
        document_id=str(fixture.document_id),
        scenario=scenario,
    )
    verified = await verify_one_cycle(factory, fixture=fixture, ids=ids, scenario=scenario)

    assert verified["input_check"] == "PASS"
    assert verified["guide"]["prompt_version"] == "guide-prompt-v3"
    assert verified["chat"]["prompt_version"] == "chat-prompt-v2"
    assert verified["guide_content"] == "private guide"
    assert verified["chat_content"] == "private chat"
    await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)


@pytest.mark.asyncio
async def test_deterministic_one_cycle_uses_asgi_routes_with_only_provider_boundary_fakes() -> None:
    class FakeGuideGenerator:
        async def generate(self, _generation_input: object) -> GuideGenerationResult:
            return GuideGenerationResult(
                content="private deterministic guide",
                model_name="gpt-4o-mini-deterministic",
                prompt_version="guide-prompt-v3",
            )

    class FakeChatEngine:
        async def reply(self, _chat_input: object) -> ChatReplyOutput:
            return ChatReplyOutput(
                content="private deterministic chat",
                model_name="gpt-4o-mini-deterministic",
                prompt_version="chat-prompt-v2",
            )

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    scenario = _scenario_payload()
    fixture = await build_synthetic_fixture(factory, run_id=uuid4(), scenario=scenario)
    paths: list[str] = []
    previous_db_override = fastapi_app.dependency_overrides[get_db_session]

    async def independent_db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def record_path(request):
        paths.append(request.url.path)

    fastapi_app.dependency_overrides[get_db_session] = independent_db_session
    fastapi_app.dependency_overrides[get_guide_generator] = FakeGuideGenerator
    fastapi_app.dependency_overrides[get_chat_engine] = FakeChatEngine
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            event_hooks={"request": [record_path]},
        ) as client:
            result = await run_deterministic_one_cycle(
                client,
                fixture=fixture,
                scenario=scenario,
                session_factory=factory,
            )
    finally:
        fastapi_app.dependency_overrides[get_db_session] = previous_db_override
        fastapi_app.dependency_overrides.pop(get_guide_generator, None)
        fastapi_app.dependency_overrides.pop(get_chat_engine, None)

    assert paths == [
        "/api/v1/auth/login",
        f"/api/v1/documents/{fixture.document_id}/prescription",
        "/api/v1/guides",
        f"/api/v1/prescriptions/{result['ids']['prescription_id']}/chat-sessions",
        f"/api/v1/chat-sessions/{result['ids']['session_id']}/messages",
    ]
    assert result["transport"] == "asgi"
    assert result["input_check"] == "PASS"
    await cleanup_synthetic_fixture(factory, user_id=fixture.user_id)
