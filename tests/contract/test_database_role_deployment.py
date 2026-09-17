from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg17-bookworm"


def test_optional_database_roles_must_be_distinct() -> None:
    from infra.python.provision_database_roles import RUNTIME_LIFESTYLE_TABLES, validate_distinct_role_names

    assert RUNTIME_LIFESTYLE_TABLES == {"lifestyle_times"}

    validate_distinct_role_names("admin", "owner", "runtime", "writer", None, "index-builder")

    with pytest.raises(ValueError, match="distinct"):
        validate_distinct_role_names("admin", "owner", "runtime", "writer", None, "writer")


def test_credentials_and_admin_process_are_separated() -> None:
    services = yaml.safe_load((ROOT / "infra/docker/docker-compose.prod.yml").read_text())["services"]
    for name in ("fastapi", "ai-worker", "migrate", "verify-db-head"):
        environment = services[name]["environment"]
        assert "env_file" not in services[name]
        assert not any(
            "DB_ADMIN_PASSWORD" in str(value)
            or "SOURCE_WRITER_PASSWORD" in str(value)
            or "CATALOG_WRITER_PASSWORD" in str(value)
            for value in environment.values()
        )
    provisioner = services["provision-db-roles"]
    assert provisioner["profiles"] == ["database-admin"]
    assert provisioner["restart"] == "no"
    assert provisioner["environment"]["DB_ADMIN_PASSWORD"] == "${DB_ADMIN_PASSWORD}"
    assert provisioner["environment"]["CATALOG_WRITER_USER"] == "${CATALOG_WRITER_USER:-}"
    assert provisioner["environment"]["KNOWLEDGE_INDEX_BUILDER_USER"] == "${KNOWLEDGE_INDEX_BUILDER_USER:-}"
    assert "KNOWLEDGE_INDEX_BUILDER_PASSWORD" not in provisioner["environment"]
    assert "CATALOG_WRITER_PASSWORD" not in provisioner["environment"]
    assert not any("SOURCE_WRITER_PASSWORD" in str(value) for value in provisioner["environment"].values())
    verifier = services["verify-db-head"]
    assert verifier["profiles"] == ["database-maintenance"]
    assert verifier["restart"] == "no"
    assert verifier["environment"]["DB_USER"] == "${DB_MIGRATION_USER}"
    assert verifier["environment"]["DB_PASSWORD"] == "${DB_MIGRATION_PASSWORD}"
    writer = services["source-writer"]
    assert writer["profiles"] == ["source-admin"]
    assert writer["environment"]["SOURCE_WRITER_PASSWORD"] == "${SOURCE_WRITER_PASSWORD:-}"
    assert not any(
        "DB_ADMIN_PASSWORD" in str(value) or "DB_APP_PASSWORD" in str(value) for value in writer["environment"].values()
    )
    dockerfile = (ROOT / "backend/app/Dockerfile").read_text()
    assert "COPY ./infra/python ./infra/python" in dockerfile
    assert "COPY ./scripts/ci/verify_database_head.py ./scripts/ci/verify_database_head.py" in dockerfile

    worker = services["ai-worker"]
    assert not any("KNOWLEDGE_INDEX_BUILDER" in key for key in worker["environment"])


def test_account_deletion_request_runtime_role_is_append_only() -> None:
    from infra.python.provision_database_roles import RUNTIME_APPEND_ONLY_TABLES, RUNTIME_MUTABLE_TABLES

    assert "account_deletion_request" in RUNTIME_APPEND_ONLY_TABLES
    assert "account_deletion_request" not in RUNTIME_MUTABLE_TABLES


def test_retrieval_run_tables_runtime_role_privileges_are_least_privilege() -> None:
    from infra.python.provision_database_roles import (
        RUNTIME_APPEND_ONLY_TABLES,
        RUNTIME_MUTABLE_TABLES,
        RUNTIME_RETRIEVAL_RUN_TABLES,
    )

    # #689: retrieval_run lifecycle requires SELECT, INSERT, UPDATE; direct DELETE is prohibited.
    assert RUNTIME_RETRIEVAL_RUN_TABLES == {"retrieval_run"}
    assert "retrieval_run" not in RUNTIME_MUTABLE_TABLES
    assert "retrieval_run" not in RUNTIME_APPEND_ONLY_TABLES

    # #689: child tables retrieval_signal and retrieval_hit are strictly append-only.
    assert "retrieval_signal" in RUNTIME_APPEND_ONLY_TABLES
    assert "retrieval_hit" in RUNTIME_APPEND_ONLY_TABLES
    assert "retrieval_signal" not in RUNTIME_MUTABLE_TABLES
    assert "retrieval_hit" not in RUNTIME_MUTABLE_TABLES

    # Ensure no overlap with RUNTIME_MUTABLE_TABLES
    assert not (RUNTIME_RETRIEVAL_RUN_TABLES & RUNTIME_MUTABLE_TABLES)
    assert not ({"retrieval_signal", "retrieval_hit"} & RUNTIME_MUTABLE_TABLES)

    source = (ROOT / "infra/python/provision_database_roles.py").read_text()
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE public.retrieval_run" in source
    assert "GRANT DELETE ON TABLE public.retrieval_run" not in source
    assert "GRANT TRUNCATE ON TABLE public.retrieval_run" not in source
    assert "GRANT DELETE ON TABLE public.retrieval_signal" not in source
    assert "GRANT UPDATE ON TABLE public.retrieval_signal" not in source
    assert "GRANT DELETE ON TABLE public.retrieval_hit" not in source
    assert "GRANT UPDATE ON TABLE public.retrieval_hit" not in source


def test_knowledge_index_role_policy_is_explicit_and_least_privilege() -> None:
    from infra.python.knowledge_index_role_policy import (
        KNOWLEDGE_INDEX_LOCK_COLUMNS,
        KNOWLEDGE_INDEX_RUNTIME_READ_TABLES,
        KNOWLEDGE_INDEX_WRITE_TABLES,
    )

    assert KNOWLEDGE_INDEX_WRITE_TABLES == {
        "knowledge_document",
        "knowledge_chunk",
        "rag_knowledge_index",
        "rag_knowledge_index_member",
    }
    assert KNOWLEDGE_INDEX_RUNTIME_READ_TABLES == KNOWLEDGE_INDEX_WRITE_TABLES
    assert set(KNOWLEDGE_INDEX_LOCK_COLUMNS) == {
        "rag_source",
        "rag_source_endpoint",
        "rag_source_operation",
        "rag_source_snapshot",
        "rag_source_snapshot_member",
        "rag_source_ingestion_run",
        "rag_source_ingestion_artifact",
        "knowledge_document",
        "knowledge_chunk",
        "rag_knowledge_index",
    }

    source = (ROOT / "infra/python/knowledge_index_role_policy.py").read_text()
    assert "GRANT SELECT, INSERT" in source
    assert "GRANT UPDATE (" in source
    assert "GRANT UPDATE ON TABLE" not in source
    assert "GRANT DELETE" not in source
    assert "GRANT TRUNCATE" not in source


def test_deployment_stops_writers_and_provisions_before_starting_api() -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    stop = script.index("docker compose --profile source-admin stop")
    bootstrap = script.index("-f /docker-entrypoint-initdb.d/configure-app-role.sql")
    migration = script.index('migration_exit_code="$(docker wait migrate)"')
    verification = script.index(
        "docker compose --profile database-maintenance run --rm --no-deps --pull always verify-db-head"
    )
    provisioning = script.index(
        "docker compose --profile database-admin run --rm --no-deps --pull always provision-db-roles"
    )
    startup = (
        script.index('echo "Starting application services"')
        if 'echo "Starting application services"' in script
        else script.index("docker compose up", provisioning)
    )
    assert stop < bootstrap < migration < verification < provisioning < startup
    assert "set -euo pipefail" in script[script.index("ssh", script.index("configure-app-role.sql")) : verification]
    bootstrap_sql = (ROOT / "infra/docker/postgres/configure-app-role.sql").read_text()
    services = yaml.safe_load((ROOT / "infra/docker/docker-compose.prod.yml").read_text())["services"]
    assert services["postgres"]["image"] == PGVECTOR_IMAGE
    assert "CREATE EXTENSION IF NOT EXISTS vector" in bootstrap_sql
    assert "extversion = '0.8.6'" in bootstrap_sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in bootstrap_sql
    assert "GRANT EXECUTE" not in bootstrap_sql
    assert "GRANT ALL" not in bootstrap_sql


def test_real_stack_uses_the_same_pgvector_server_image() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.real-stack-e2e.yml").read_text())["services"]

    assert services["postgres"]["image"] == PGVECTOR_IMAGE


def test_ci_runs_legacy_downgrades_before_irreversible_cutover():
    runner = (ROOT / "scripts/ci/run_test.sh").read_text()
    assert runner.index("upgrade 398b2c3d4e5f") < runner.index("pytest tests/migration") < runner.index("upgrade head")
    workflow = yaml.safe_load((ROOT / ".github/workflows/checks.yml").read_text())
    migration_steps = workflow["jobs"]["test-migration"]["steps"]
    rag_steps = workflow["jobs"]["test-rag"]["steps"]
    base = next(i for i, step in enumerate(migration_steps) if "upgrade 398b2c3d4e5f" in step.get("run", ""))
    legacy = next(i for i, step in enumerate(migration_steps) if "pytest tests/migration" in step.get("run", ""))
    cutover = next(
        i for i, step in enumerate(migration_steps) if step.get("name") == "Apply irreversible Source cutover"
    )
    rag = next(step for step in rag_steps if step.get("name") == "Run RAG Integration Tests with Coverage")
    assert base < legacy < cutover
    assert rag["env"]["ISSUE398_TEST_POSTGRES_CONTAINER"] == "${{ job.services.postgres.id }}"
