"""backend/app/Dockerfile로 빌드한 컨테이너 내부에서 rag_runtime 커널이 정상 import되는지 검증합니다 (PR #382, Issue #173)."""

import subprocess


def test_backend_container_can_import_runtime_and_load_migration_head(storage_dir_built_image: str) -> None:
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
            "assert migration_heads() == ('3980718293a4',); print('ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"
