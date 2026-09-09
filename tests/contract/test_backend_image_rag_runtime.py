"""backend/app/Dockerfile로 빌드한 컨테이너 내부에서 rag_runtime 커널이 정상 import되는지 검증합니다 (PR #382, Issue #173)."""

import subprocess


def test_backend_container_can_import_rag_runtime(storage_dir_built_image: str) -> None:
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
            "from rag_runtime import evaluate_medication_identification_preflight; print('ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"
