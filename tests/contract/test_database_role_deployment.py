from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_credentials_and_admin_process_are_separated() -> None:
    services = yaml.safe_load((ROOT / "infra/docker/docker-compose.prod.yml").read_text())["services"]
    for name in ("fastapi", "ai-worker", "migrate"):
        environment = services[name]["environment"]
        assert "env_file" not in services[name]
        assert not any(
            "DB_ADMIN_PASSWORD" in str(value) or "SOURCE_WRITER_PASSWORD" in str(value)
            for value in environment.values()
        )
    provisioner = services["provision-db-roles"]
    assert provisioner["profiles"] == ["database-admin"]
    assert provisioner["restart"] == "no"
    assert provisioner["environment"]["DB_ADMIN_PASSWORD"] == "${DB_ADMIN_PASSWORD}"
    assert not any("SOURCE_WRITER_PASSWORD" in str(value) for value in provisioner["environment"].values())
    writer = services["source-writer"]
    assert writer["profiles"] == ["source-admin"]
    assert writer["environment"]["SOURCE_WRITER_PASSWORD"] == "${SOURCE_WRITER_PASSWORD:-}"
    assert not any(
        "DB_ADMIN_PASSWORD" in str(value) or "DB_APP_PASSWORD" in str(value) for value in writer["environment"].values()
    )
    assert "COPY ./infra/python ./infra/python" in (ROOT / "backend/app/Dockerfile").read_text()


def test_deployment_stops_writers_and_provisions_before_starting_api() -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    stop = script.index("docker compose --profile source-admin stop")
    bootstrap = script.index("-f /docker-entrypoint-initdb.d/configure-app-role.sql")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')
    provisioning = script.index(
        "docker compose --profile database-admin run --rm --no-deps --pull always provision-db-roles"
    )
    startup = (
        script.index('echo "Starting application services"')
        if 'echo "Starting application services"' in script
        else script.index("docker compose up", provisioning)
    )
    assert stop < bootstrap < migration < provisioning < startup
    assert "set -euo pipefail" in script[script.index("ssh", script.index("configure-app-role.sql")) : provisioning]
    bootstrap_sql = (ROOT / "infra/docker/postgres/configure-app-role.sql").read_text()
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in bootstrap_sql
    assert "GRANT EXECUTE" not in bootstrap_sql
    assert "GRANT ALL" not in bootstrap_sql
