"""The management credential and routes stay out of ordinary deployment surfaces."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_management_profiles_keep_credentials_out_of_runtime_services():
    services = yaml.safe_load((ROOT / "infra/docker/docker-compose.prod.yml").read_text())["services"]
    for name, service in services.items():
        environment = service.get("environment", {})
        if name not in {"source-management", "source-management-bootstrap", "provision-db-roles"}:
            assert "SOURCE_MANAGEMENT_USER" not in environment
            assert "SOURCE_MANAGEMENT_PASSWORD" not in str(environment)
    manager = services["source-management"]
    assert manager["profiles"] == ["source-management"]
    assert manager["ports"] == ["127.0.0.1:8010:8010"]
    assert manager["environment"]["DB_PASSWORD"] == "${SOURCE_MANAGEMENT_PASSWORD:-}"
    assert "DB_ADMIN_PASSWORD" not in str(manager["environment"])
    assert "DB_APP_PASSWORD" not in str(manager["environment"])
    assert "DB_MIGRATION_PASSWORD" not in str(manager["environment"])
    assert "source_management" not in (ROOT / "backend/app/main.py").read_text()
    for service in services.values():
        assert "source-management" not in service.get("depends_on", {})
