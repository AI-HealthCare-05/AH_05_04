import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg17-bookworm"


def test_optional_database_roles_must_be_distinct() -> None:
    from infra.python.provision_database_roles import RUNTIME_LIFESTYLE_TABLES, validate_distinct_role_names

    assert RUNTIME_LIFESTYLE_TABLES == {"lifestyle_times"}

    validate_distinct_role_names(
        "admin",
        "owner",
        "runtime",
        "writer",
        None,
        "catalog",
        "index-builder",
        "cleanup",
        "candidate-builder",
        "approval",
    )

    with pytest.raises(ValueError, match="distinct"):
        validate_distinct_role_names(
            "admin", "owner", "runtime", "writer", None, "catalog", "index-builder", "cleanup", "writer", "approval"
        )


def test_runtime_bundle_citation_pin_is_append_only_for_runtime_role() -> None:
    from infra.python.provision_database_roles import (
        RUNTIME_APPEND_ONLY_TABLES,
        RUNTIME_MUTABLE_TABLES,
    )

    table = "rag_runtime_bundle_citation_approval"
    assert table in RUNTIME_APPEND_ONLY_TABLES
    assert table not in RUNTIME_MUTABLE_TABLES


def test_credentials_and_admin_process_are_separated() -> None:
    services = yaml.safe_load((ROOT / "infra/docker/docker-compose.prod.yml").read_text())["services"]
    for name in ("fastapi", "ai-worker", "migrate", "verify-db-head"):
        environment = services[name]["environment"]
        assert "env_file" not in services[name]
        assert not any(
            "DB_ADMIN_PASSWORD" in str(value)
            or "SOURCE_WRITER_PASSWORD" in str(value)
            or "CATALOG_WRITER_PASSWORD" in str(value)
            or "CANDIDATE_INDEX_BUILDER_PASSWORD" in str(value)
            for value in environment.values()
        )
    fastapi = services["fastapi"]
    assert fastapi["environment"]["ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE"] == "${ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE:-}"
    assert fastapi["environment"]["ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD"] == (
        "${ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD:-}"
    )
    worker = services["ai-worker"]
    assert "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE" not in worker["environment"]
    assert "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD" not in worker["environment"]
    provisioner = services["provision-db-roles"]
    assert provisioner["profiles"] == ["database-admin"]
    assert provisioner["restart"] == "no"
    assert provisioner["environment"]["DB_ADMIN_PASSWORD"] == "${DB_ADMIN_PASSWORD}"
    assert provisioner["environment"]["CATALOG_WRITER_USER"] == "${CATALOG_WRITER_USER:-}"
    assert provisioner["environment"]["KNOWLEDGE_INDEX_BUILDER_USER"] == "${KNOWLEDGE_INDEX_BUILDER_USER:-}"
    assert provisioner["environment"]["ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE"] == "${ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE:-}"
    assert provisioner["environment"]["CANDIDATE_INDEX_BUILDER_USER"] == "${CANDIDATE_INDEX_BUILDER_USER:-}"
    assert "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD" not in provisioner["environment"]
    assert "KNOWLEDGE_INDEX_BUILDER_PASSWORD" not in provisioner["environment"]
    assert "CATALOG_WRITER_PASSWORD" not in provisioner["environment"]
    assert "CANDIDATE_INDEX_BUILDER_PASSWORD" not in provisioner["environment"]
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

    catalog_writer = services["catalog-writer"]
    assert catalog_writer["profiles"] == ["catalog-admin"]
    assert catalog_writer["restart"] == "no"
    assert catalog_writer["environment"]["CATALOG_WRITER_PASSWORD"] == "${CATALOG_WRITER_PASSWORD:-}"
    assert not any(
        "DB_ADMIN_PASSWORD" in str(value) or "DB_APP_PASSWORD" in str(value)
        for value in catalog_writer["environment"].values()
    )

    candidate_builder = services["candidate-index-builder"]
    assert candidate_builder["profiles"] == ["candidate-index-admin"]
    assert candidate_builder["restart"] == "no"
    assert candidate_builder["environment"]["CANDIDATE_INDEX_BUILDER_PASSWORD"] == (
        "${CANDIDATE_INDEX_BUILDER_PASSWORD:-}"
    )
    assert not any(
        "DB_ADMIN_PASSWORD" in str(value) or "DB_APP_PASSWORD" in str(value)
        for value in candidate_builder["environment"].values()
    )

    dockerfile = (ROOT / "backend/app/Dockerfile").read_text()
    assert "COPY ./infra/python ./infra/python" in dockerfile
    assert "COPY ./scripts/ci/verify_database_head.py ./scripts/ci/verify_database_head.py" in dockerfile
    assert "COPY ./scripts/__init__.py ./scripts/__init__.py" in dockerfile
    assert "COPY ./scripts/candidate_index_builder.py ./scripts/candidate_index_builder.py" in dockerfile

    worker = services["ai-worker"]
    assert not any("KNOWLEDGE_INDEX_BUILDER" in key for key in worker["environment"])


def test_account_deletion_request_runtime_role_updates_only_lifecycle_columns() -> None:
    from infra.python.provision_database_roles import (
        ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES,
        ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES,
        ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS,
        ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES,
        RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS,
        RUNTIME_APPEND_ONLY_TABLES,
        RUNTIME_MUTABLE_TABLES,
    )

    assert "account_deletion_request" not in RUNTIME_APPEND_ONLY_TABLES
    assert "account_deletion_request" not in RUNTIME_MUTABLE_TABLES
    assert set(RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS) == {
        "status",
        "started_at",
        "completed_at",
        "failed_at",
        "retry_count",
        "last_error_code",
        "updated_at",
    }
    assert ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES == {
        "action_plan_followup",
        "action_plan_followup_audit",
        "barrier_response",
        "medication_candidate_search_result",
        "notification_record",
        "password_reset_token",
        "refresh_session",
        "retrieval_run",
        "safety_assessment",
        "support_action_plan",
        "user_consent",
    }
    assert ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES <= ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES
    assert ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES == {"account_deletion_request"}
    assert {"account_deletion_request", "ai_job", "profile", "user"} == set(ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS)
    assert "account_deletion_request" not in ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES

    source = (ROOT / "infra/python/provision_database_roles.py").read_text()
    assert "GRANT SELECT, INSERT ON TABLE public.account_deletion_request" in source
    assert "GRANT UPDATE ({names}) ON TABLE public.account_deletion_request" in source
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE public.account_deletion_request" not in source
    assert re.search(r"GRANT[^\n]*DELETE[^\n]*account_deletion_request", source) is None
    assert "GRANT TRUNCATE ON TABLE public.account_deletion_request" not in source
    assert "_grant_account_withdrawal_cleanup_permissions(connection, cleanup_sql)" in source
    assert "_grant_account_withdrawal_cleanup_permissions(connection, runtime_sql)" not in source


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


def test_track_c_runtime_role_privileges_are_least_privilege() -> None:
    """Track C 권한 상수 집합만 고정한다.

    실제 GRANT가 나가는지, Runtime이 정말 INSERT할 수 있고 삭제·전체 컬럼 UPDATE가
    막히는지는 실 PostgreSQL을 쓰는 통합 테스트가 검증한다.
    tests/integration/rag/test_database_role_provisioning.py::
    test_bootstrap_then_provision_and_redeploy_do_not_reopen_permissions
    """
    from infra.python.provision_database_roles import (
        RUNTIME_APPEND_ONLY_TABLES,
        RUNTIME_CHECKIN_LOCK_TABLES,
        RUNTIME_MUTABLE_TABLES,
        RUNTIME_TRACK_C_APPEND_TABLES,
        RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES,
        RUNTIME_TRACK_C_FOLLOWUP_TABLES,
        RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS,
    )

    # Track C는 revision 단위 append 뒤 고쳐 쓰지 않으므로 mutable/append-only 목록과 겹치지 않는다.
    assert RUNTIME_TRACK_C_APPEND_TABLES == {
        "safety_assessment",
        "barrier_response",
        "support_action_plan",
    }
    assert not (RUNTIME_TRACK_C_APPEND_TABLES & RUNTIME_MUTABLE_TABLES)
    assert not (RUNTIME_TRACK_C_APPEND_TABLES & RUNTIME_APPEND_ONLY_TABLES)

    # #668 row lock 경계는 유지된다.
    assert RUNTIME_CHECKIN_LOCK_TABLES == {"safety_assessment", "barrier_response"}
    assert RUNTIME_CHECKIN_LOCK_TABLES < RUNTIME_TRACK_C_APPEND_TABLES

    assert RUNTIME_TRACK_C_FOLLOWUP_TABLES == {"action_plan_followup", "action_plan_followup_audit"}
    assert not (RUNTIME_TRACK_C_FOLLOWUP_TABLES & RUNTIME_MUTABLE_TABLES)
    assert not (RUNTIME_TRACK_C_FOLLOWUP_TABLES & RUNTIME_APPEND_ONLY_TABLES)
    assert RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS == ("response", "revision", "updated_at")
    # 정정 이력 audit은 append 전용이다. 읽기는 #748 cleanup 역할에만 있다.
    assert RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES == {"action_plan_followup_audit"}
    assert RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES < RUNTIME_TRACK_C_FOLLOWUP_TABLES


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


def test_candidate_index_role_policy_is_explicit_and_least_privilege() -> None:
    from infra.python.candidate_index_role_policy import (
        CANDIDATE_INDEX_CATALOG_READ_TABLES,
        CANDIDATE_INDEX_UPDATE_COLUMNS,
        CANDIDATE_INDEX_WRITE_TABLES,
    )
    from infra.python.catalog_role_policy import CATALOG_READ_TABLES

    assert CANDIDATE_INDEX_WRITE_TABLES == {
        "rag_candidate_index_version",
        "rag_candidate_index_member",
    }
    expected_read_tables = {
        # Catalog persistence tables
        "rag_entity_identity",
        "rag_medication_product",
        "rag_medication_ingredient",
        "rag_medication_alias",
        "rag_medication_product_component",
        "rag_medication_search_entry",
        "rag_catalog_set",
        "rag_catalog_set_source",
        "rag_catalog_set_member",
        "rag_catalog_set_hash",
        # Source provenance tables
        "rag_source",
        "rag_source_endpoint",
        "rag_source_operation",
        "rag_source_snapshot",
        "rag_source_snapshot_verification",
        # Catalog approval tables
        "catalog_source_approval",
        "catalog_build_approval",
        "catalog_build_approval_source",
    }
    assert CANDIDATE_INDEX_CATALOG_READ_TABLES == expected_read_tables
    assert CANDIDATE_INDEX_UPDATE_COLUMNS == {
        "rag_candidate_index_version": ("status",),
    }

    source = (ROOT / "infra/python/candidate_index_role_policy.py").read_text()
    assert not re.search(r"\b(?<!INDEX_)CATALOG_READ_TABLES\b", source), (
        "candidate_index_role_policy.py must not reference CATALOG_READ_TABLES to prevent unintended privilege widening"
    )
    # Ensure Candidate Builder scope is independent from future Catalog read scope expansions
    hypothetical_widened_catalog = CATALOG_READ_TABLES | {"rag_catalog_publication_audit"}
    assert CANDIDATE_INDEX_CATALOG_READ_TABLES != hypothetical_widened_catalog
    assert "rag_catalog_publication_audit" not in CANDIDATE_INDEX_CATALOG_READ_TABLES

    assert "GRANT SELECT, INSERT ON TABLE public.rag_candidate_index_version" in source
    assert "GRANT UPDATE (status) ON TABLE public.rag_candidate_index_version" in source
    assert "GRANT SELECT, INSERT ON TABLE public.rag_candidate_index_member" in source
    assert "GRANT UPDATE (candidate_index_lock_marker)" not in source
    assert "candidate_index_lock_marker" not in [line for line in source.splitlines() if "GRANT" in line]
    assert "GRANT DELETE" not in source
    assert "GRANT TRUNCATE" not in source


def test_catalog_role_policy_includes_approval_tables() -> None:
    from infra.python.catalog_role_policy import (
        CATALOG_APPROVAL_READ_TABLES,
        CATALOG_READ_TABLES,
        CATALOG_WRITE_TABLES,
    )

    assert CATALOG_APPROVAL_READ_TABLES == {
        "catalog_source_approval",
        "catalog_build_approval",
        "catalog_build_approval_source",
    }
    assert CATALOG_APPROVAL_READ_TABLES <= CATALOG_READ_TABLES
    assert not (CATALOG_APPROVAL_READ_TABLES & CATALOG_WRITE_TABLES)

    source = (ROOT / "infra/python/catalog_role_policy.py").read_text()
    assert "CATALOG_APPROVAL_READ_TABLES" in source


def test_catalog_approval_role_policy_is_explicit_and_least_privilege() -> None:
    from infra.python.catalog_approval_role_policy import (
        APPROVAL_AUDIT_TABLE,
        APPROVAL_INSERT_TABLES,
        APPROVAL_PAYLOAD_TABLES,
        APPROVAL_PERMISSION_INSERT_COLUMNS,
        APPROVAL_PERMISSION_TABLE,
        APPROVAL_PERMISSION_UPDATE_COLUMNS,
        APPROVAL_READ_TABLES,
        APPROVAL_REVOKE_COLUMNS,
        APPROVAL_SOURCE_READ_TABLES,
        APPROVAL_STATE_INSERT_TABLES,
        APPROVAL_USER_COLUMNS,
        REVOCABLE_TABLES,
    )
    from infra.python.catalog_role_policy import (
        CATALOG_APPROVAL_READ_TABLES,
        CATALOG_WRITE_TABLES,
    )

    assert APPROVAL_AUDIT_TABLE == "catalog_approval_audit"
    assert APPROVAL_PERMISSION_TABLE == "catalog_approval_permission"
    assert APPROVAL_PAYLOAD_TABLES == {
        "catalog_source_approval",
        "catalog_build_approval",
        "catalog_build_approval_source",
    }
    assert APPROVAL_INSERT_TABLES == APPROVAL_PAYLOAD_TABLES | {APPROVAL_AUDIT_TABLE}
    assert APPROVAL_STATE_INSERT_TABLES == {APPROVAL_PERMISSION_TABLE}
    assert APPROVAL_PERMISSION_INSERT_COLUMNS == ("user_id", "enabled", "evidence_ref", "revision", "updated_at")
    assert APPROVAL_PERMISSION_UPDATE_COLUMNS == ("enabled", "evidence_ref", "revision", "updated_at")
    assert REVOCABLE_TABLES == {"catalog_source_approval", "catalog_build_approval"}
    assert APPROVAL_REVOKE_COLUMNS == ("revoked_at", "revoked_by", "revoked_reason")
    assert APPROVAL_USER_COLUMNS == ("id", "is_active", "account_status")
    assert APPROVAL_READ_TABLES == APPROVAL_PAYLOAD_TABLES | {APPROVAL_AUDIT_TABLE, APPROVAL_PERMISSION_TABLE}
    assert CATALOG_APPROVAL_READ_TABLES == APPROVAL_PAYLOAD_TABLES
    assert "rag_source_snapshot" in APPROVAL_SOURCE_READ_TABLES

    # Catalog Writer must never write to approval tables
    assert not (CATALOG_WRITE_TABLES & APPROVAL_READ_TABLES)

    # Approval role must never write to catalog business tables
    assert not (APPROVAL_INSERT_TABLES & CATALOG_WRITE_TABLES)
    assert not (APPROVAL_STATE_INSERT_TABLES & CATALOG_WRITE_TABLES)
    assert not (REVOCABLE_TABLES & CATALOG_WRITE_TABLES)

    source = (ROOT / "infra/python/catalog_approval_role_policy.py").read_text()
    assert "GRANT INSERT ON TABLE public." in source
    assert "GRANT INSERT ({permission_insert_columns}) ON TABLE public.{permission_sql}" in source
    assert "GRANT UPDATE (" in source
    assert "GRANT DELETE" not in source
    assert "GRANT TRUNCATE" not in source


def test_deployment_stops_writers_and_provisions_before_starting_api() -> None:
    script = (ROOT / "scripts/deployment.sh").read_text()
    stop = script.index(
        "docker compose --profile source-admin --profile catalog-admin --profile candidate-index-admin stop"
    )
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
