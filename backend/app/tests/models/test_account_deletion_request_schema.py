from sqlalchemy import CheckConstraint

from app.core.db.databases import Base
from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus


def test_account_deletion_request_is_registered() -> None:
    assert "account_deletion_request" in Base.metadata.tables


def test_account_deletion_request_contains_lifecycle_and_audit_columns() -> None:
    table = Base.metadata.tables["account_deletion_request"]

    expected_columns = {
        "id",
        "user_id",
        "status",
        "requested_at",
        "started_at",
        "completed_at",
        "failed_at",
        "retry_count",
        "last_error_code",
        "created_at",
        "updated_at",
    }

    assert expected_columns == set(table.c.keys())
    assert {
        "chk_account_deletion_request_status",
        "chk_account_deletion_request_retry_count",
    }.issubset(constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint))


def test_account_deletion_request_status_enum_matches_contract() -> None:
    """account-lifecycle-v1.md 5.2절 상태 4종과 어긋나면 애플리케이션이 DB가 거부하는 값을
    저장하려 시도하거나, DB가 허용하는 값을 애플리케이션이 판정하지 못하게 되므로 동기화된
    상태로 고정합니다."""

    assert {member.value for member in AccountDeletionRequestStatus} == {
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "FAILED",
    }


def test_only_one_active_deletion_request_is_allowed_per_user() -> None:
    """5.4절: PENDING/IN_PROGRESS/FAILED(terminal 전 상태)는 사용자당 최대 1개여야 하고,
    COMPLETED는 이 제약 대상이 아니라 과거 이력으로 여러 개 남을 수 있어야 합니다."""

    table = Base.metadata.tables["account_deletion_request"]
    active_index = next(index for index in table.indexes if index.name == "uq_account_deletion_request_active_per_user")

    assert active_index.unique
    assert [column.name for column in active_index.columns] == ["user_id"]
    where_clause = active_index.dialect_options["postgresql"]["where"]
    assert where_clause is not None
    assert "PENDING" in str(where_clause)
    assert "IN_PROGRESS" in str(where_clause)
    assert "FAILED" in str(where_clause)
    assert "COMPLETED" not in str(where_clause)


def test_account_deletion_request_references_user_by_restrict() -> None:
    """탈퇴 처리 감사 기록은 User row가 지워져도(그런 경로 자체가 없지만) 함께 사라지면 안 되므로
    ON DELETE CASCADE/SET NULL이 아니라 기본(RESTRICT류) FK를 유지합니다."""

    table = Base.metadata.tables["account_deletion_request"]
    fk = next(iter(table.c["user_id"].foreign_keys))

    assert fk.column.table.name == "user"
    assert fk.ondelete is None


def test_account_deletion_request_model_matches_table_name() -> None:
    assert AccountDeletionRequest.__tablename__ == "account_deletion_request"
