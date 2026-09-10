"""backend/app/Dockerfile로 빌드한 컨테이너 내부에서 rag_runtime 커널이 정상 import되는지 검증합니다 (PR #382, Issue #173)."""

import subprocess

from scripts.ci.verify_database_head import migration_heads


def test_backend_container_can_import_runtime_and_load_migration_head(storage_dir_built_image: str) -> None:
    expected_heads = migration_heads()
    assert len(expected_heads) == 1
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            storage_dir_built_image,
            "uv",
            "run",
            "--no-sync",
            "python",
            "-c",
            "from rag_runtime import evaluate_medication_identification_preflight; "
            "from scripts.ci.verify_database_head import migration_heads; "
            f"assert migration_heads() == {expected_heads!r}; print('ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"


def test_backend_image_contains_isolated_management_entrypoints(storage_dir_built_image: str) -> None:
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "DB_HOST=synthetic",
            "-e",
            "DB_USER=synthetic",
            "-e",
            "DB_PASSWORD=synthetic",
            "-e",
            "DB_NAME=synthetic",
            storage_dir_built_image,
            "uv",
            "run",
            "--no-sync",
            "python",
            "-c",
            "from app.admin.source_management_api import management_app; "
            "from app.admin.source_management_permissions import PermissionChange; "
            "assert '/management/{kind}/{target_id}' in management_app.openapi()['paths']; print('ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"
