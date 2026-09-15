from pathlib import Path
from typing import Any

from ai_worker.core.config import Config

ROOT = Path(__file__).resolve().parents[2]
READINESS_DOC = ROOT / "docs/validation/rag/issue-593/source-handoff-readiness.md"


def _source_artifact_settings_from_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.startswith("SOURCE_ARTIFACT_"):
            values[key] = value
    return values


def _config_from_source_artifact_example(path: Path) -> Config:
    settings: dict[str, Any] = {
        "ENV": "local",
        "DB_HOST": "127.0.0.1",
        "DB_NAME": "test",
        "DB_USER": "worker",
        "DB_PASSWORD": "worker-password",
        "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
        "CLOVA_OCR_SECRET": "synthetic-clova-secret",
        "STORAGE_DIR": "/tmp/medical-documents",
    }
    settings.update(_source_artifact_settings_from_env_example(path))
    return Config(_env_file=None, **settings)  # type: ignore[call-arg]


def test_source_handoff_readiness_doc_pins_existing_runtime_boundaries() -> None:
    text = READINESS_DOC.read_text(encoding="utf-8")

    required_paths = [
        "docs/runbooks/source-snapshot-handoff-env-593.md",
        "ai_worker/core/config.py",
        "ai_worker/README.md",
        "ai_worker/adapters/source_artifact_store_factory.py",
        "ai_worker/adapters/local_private_source_artifact_store.py",
        "ai_worker/adapters/s3_private_source_artifact_store.py",
        "infra/python/source_management_role_policy.py",
        "tests/contract/test_source_management_deployment.py",
        "scripts/rag/verify_mfds_label_candidate.py",
        "docs/validation/rag/issue-591/local-storage-validation.md",
    ]
    for relative_path in required_paths:
        assert relative_path in text
        assert (ROOT / relative_path).exists()

    required_settings = [
        "SOURCE_ARTIFACT_STORAGE_BACKEND",
        "SOURCE_ARTIFACT_S3_PREFIX",
        "SOURCE_ARTIFACT_S3_REGION",
        "SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION",
        "SOURCE_ARTIFACT_S3_ENDPOINT_URL",
        "S3_PRIVATE",
        "LOCAL_PRIVATE",
    ]
    for setting in required_settings:
        assert setting in text


def test_source_handoff_readiness_doc_does_not_claim_provision_or_ingestion_complete() -> None:
    text = READINESS_DOC.read_text(encoding="utf-8")

    required_boundaries = [
        "실제 DB endpoint, credential, artifact root/bucket 값을 추가하지 않는다",
        "실제 노바스크 원문을 저장소에 추가하지 않는다",
        "#591 실제 적재 성공이나 운영 공개 승인을 주장하지 않는다",
        "제한 접근 위치",
        "인계용 Source Snapshot 적재 완료로 보지 않는다",
        "- [x] PR #597의 runbook이 develop에 반영됐다.",
    ]
    for boundary in required_boundaries:
        assert boundary in text

    forbidden_fragments = [
        "DB_PASSWORD=",
        "AWS_SECRET_ACCESS_KEY=",
        "AWS_ACCESS_KEY_ID=",
        "postgresql://",
        "postgres://",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in text


def test_source_handoff_env_examples_include_artifact_storage_keys() -> None:
    required_settings = [
        "SOURCE_ARTIFACT_STORAGE_BACKEND",
        "SOURCE_ARTIFACT_LOCAL_ROOT",
        "SOURCE_ARTIFACT_S3_BUCKET",
        "SOURCE_ARTIFACT_S3_PREFIX",
        "SOURCE_ARTIFACT_S3_REGION",
        "SOURCE_ARTIFACT_S3_ENDPOINT_URL",
        "SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION",
        "SOURCE_ARTIFACT_S3_KMS_KEY_ID",
    ]

    for env_path in (ROOT / "envs/example.local.env", ROOT / "envs/example.prod.env"):
        text = env_path.read_text(encoding="utf-8")
        for setting in required_settings:
            assert f"{setting}=" in text

    local_config = _config_from_source_artifact_example(ROOT / "envs/example.local.env")
    assert local_config.SOURCE_ARTIFACT_STORAGE_BACKEND == "DISABLED"
    assert local_config.SOURCE_ARTIFACT_LOCAL_ROOT is None
    assert local_config.SOURCE_ARTIFACT_S3_BUCKET is None
    assert local_config.SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION is None

    prod_config = _config_from_source_artifact_example(ROOT / "envs/example.prod.env")
    assert prod_config.SOURCE_ARTIFACT_STORAGE_BACKEND == "DISABLED"
    assert prod_config.SOURCE_ARTIFACT_LOCAL_ROOT is None
    assert prod_config.SOURCE_ARTIFACT_S3_KMS_KEY_ID is None

    prod_text = (ROOT / "envs/example.prod.env").read_text(encoding="utf-8")
    assert "access key" not in prod_text.lower()
    assert "secret key" not in prod_text.lower()
    assert "SOURCE_ARTIFACT_STORAGE_BACKEND=DISABLED" in prod_text
