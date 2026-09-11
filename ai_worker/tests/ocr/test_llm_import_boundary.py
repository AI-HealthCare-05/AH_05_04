import os
import subprocess
import sys
from pathlib import Path


def test_llm_runtime_imports_without_backend_or_credentials() -> None:
    root = Path(__file__).resolve().parents[3]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DB_", "OPENAI_", "CLOVA_", "OCR_STRUCTURE_"))
    }
    environment["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ocr_runtime.llm.client import OpenAIOcrStructureClient; "
            "from ocr_runtime.llm.structurer import LlmPrescriptionStructurer; "
            "from ocr_runtime.llm.validator import validate_and_convert_draft; "
            "import sys; "
            "assert not any(n == 'app' or n.startswith('app.') for n in sys.modules)",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
