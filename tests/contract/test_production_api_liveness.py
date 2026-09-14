"""Check the deployed probe against the real app, without using a database or Provider."""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_production_probe_targets_an_existing_app_route(tmp_path: Path) -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "infra/docker/docker-compose.prod.yml").read_text())
    services = compose["services"]
    probe = services["fastapi"]["healthcheck"]["test"]
    assert probe[:6] == ["CMD", "uv", "run", "--no-sync", "python", "-c"]
    url = re.search(r"urlopen\('([^']+)', timeout=3\)", probe[-1])
    assert url is not None
    target = urlsplit(url.group(1))
    assert target.netloc == "127.0.0.1:8000"
    assert services["nginx"]["depends_on"]["fastapi"]["condition"] == "service_healthy"

    # Isolate Config imports from other test modules and use only synthetic settings.
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT)]),
        "ENV": "local",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",
        "DB_NAME": "synthetic_liveness",
        "DB_USER": "synthetic",
        "DB_PASSWORD": "synthetic-only-password",
        "OPENAI_API_KEY": "synthetic-not-a-provider-key",
        "PUBLIC_TRACK_F_ENABLED": "false",
        "OCR_STRUCTURE_LLM_ENABLED": "false",
        "CHAT_HISTORY_CONTEXT_ENABLED": "false",
        "STORAGE_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "import sys\n"
            "with TestClient(app) as client:\n"
            "    response = client.get(sys.argv[1])\n"
            "    assert response.status_code == 200, response.status_code\n"
            "    assert response.json()['openapi']\n",
            target.path,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_deployment_smoke_instructions_use_the_same_existing_endpoint() -> None:
    script = (PROJECT_ROOT / "scripts/deployment.sh").read_text()
    runbook = (PROJECT_ROOT / "docs/runbooks/aws-production-demo.md").read_text()
    assert "${PRODUCTION_PUBLIC_ORIGIN}/api/openapi.json" in script
    assert "${PRODUCTION_PUBLIC_ORIGIN}/api/v1/health" not in script
    assert "cloudfront.net/api/openapi.json" in runbook
    assert "cloudfront.net/api/v1/health" not in runbook
