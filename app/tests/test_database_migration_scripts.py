"""DB 전환 스크립트의 성공·차단·롤백 흐름을 합성 데이터로 검증합니다."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, func, select
from sqlalchemy.engine import URL, Connection
from sqlalchemy.exc import IntegrityError

from scripts.db import migrate_mysql_to_postgresql as migration
from scripts.db import verify_postgresql_migration as verification


def _sqlite_url(path: Path) -> URL:
    """실제 개발·운영 DB에 접근하지 않는 격리된 합성 DB URL을 만듭니다."""
    return URL.create(
        drivername="sqlite+pysqlite",
        database=str(path),
    )


def _create_schema(
    url: URL,
    table_names: tuple[str, ...],
) -> None:
    """성공·실패 흐름 검증에 필요한 최소 합성 스키마를 생성합니다."""
    engine = create_engine(url)
    metadata = MetaData()

    for table_name in table_names:
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", String(30), nullable=False),
            Column("optional_value", String(100), nullable=True),
        )

    metadata.create_all(engine)
    engine.dispose()


def _insert_rows(
    url: URL,
    table_name: str,
    rows: list[dict[str, Any]],
) -> None:
    """합성 식별자와 상태값만 테스트 DB에 추가합니다."""
    engine = create_engine(url)
    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)

    with engine.begin() as connection:
        connection.execute(table.insert(), rows)

    engine.dispose()


def _row_count(
    url: URL,
    table_name: str,
) -> int:
    """실제 행 내용을 출력하지 않고 합성 테이블 행 수만 반환합니다."""
    engine = create_engine(url)
    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)

    with engine.connect() as connection:
        count = connection.execute(select(func.count()).select_from(table)).scalar_one()

    engine.dispose()
    return int(count)


def _configure_migration_databases(
    monkeypatch: pytest.MonkeyPatch,
    source_url: URL,
    target_url: URL,
    table_names: list[str],
) -> None:
    """이관 스크립트가 격리된 합성 DB만 사용하도록 설정합니다."""
    monkeypatch.setattr(migration, "TABLE_ORDER", table_names)
    monkeypatch.setattr(migration, "build_mysql_url", lambda: source_url)
    monkeypatch.setattr(migration, "build_postgresql_url", lambda: target_url)


def _configure_verification_databases(
    monkeypatch: pytest.MonkeyPatch,
    source_url: URL,
    target_url: URL,
    table_names: list[str],
) -> None:
    """검증 스크립트가 격리된 합성 DB만 사용하도록 설정합니다."""
    monkeypatch.setattr(verification, "TABLE_ORDER", table_names)
    monkeypatch.setattr(verification, "ALL_DATETIME_COLUMNS", frozenset())
    monkeypatch.setattr(verification, "build_mysql_url", lambda: source_url)
    monkeypatch.setattr(
        verification,
        "build_postgresql_url",
        lambda: target_url,
    )


def test_migration_defaults_to_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """실행 옵션을 생략하면 실제 데이터가 복사되지 않아야 합니다."""
    source_url = _sqlite_url(tmp_path / "dry-run-source.db")
    target_url = _sqlite_url(tmp_path / "dry-run-target.db")
    table_name = "synthetic_record"

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))
    _insert_rows(
        source_url,
        table_name,
        [{"id": 1, "status": "READY", "optional_value": None}],
    )

    _configure_migration_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )
    monkeypatch.delenv("MIGRATION_DRY_RUN", raising=False)

    migration.main()

    assert _row_count(target_url, table_name) == 0
    assert "사전검증만 완료했습니다" in capsys.readouterr().out


def test_migration_copies_synthetic_rows_when_explicitly_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """명시적으로 허용한 경우에만 합성 데이터 전체를 복사합니다."""
    source_url = _sqlite_url(tmp_path / "success-source.db")
    target_url = _sqlite_url(tmp_path / "success-target.db")
    table_name = "synthetic_record"

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))
    _insert_rows(
        source_url,
        table_name,
        [
            {"id": 1, "status": "READY", "optional_value": None},
            {"id": 2, "status": "COMPLETED", "optional_value": "synthetic"},
        ],
    )

    _configure_migration_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )
    monkeypatch.setenv("MIGRATION_DRY_RUN", "false")

    migration.main()

    assert _row_count(target_url, table_name) == 2
    assert "데이터 이관을 완료했습니다" in capsys.readouterr().out


def test_migration_rejects_nonempty_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대상 테이블에 데이터가 있으면 중복 이관을 차단합니다."""
    source_url = _sqlite_url(tmp_path / "nonempty-source.db")
    target_url = _sqlite_url(tmp_path / "nonempty-target.db")
    table_name = "synthetic_record"

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))
    _insert_rows(
        source_url,
        table_name,
        [{"id": 1, "status": "READY", "optional_value": None}],
    )
    _insert_rows(
        target_url,
        table_name,
        [{"id": 99, "status": "EXISTING", "optional_value": None}],
    )

    _configure_migration_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )
    monkeypatch.setenv("MIGRATION_DRY_RUN", "false")

    with pytest.raises(RuntimeError, match="비어 있지 않습니다"):
        migration.main()

    assert _row_count(target_url, table_name) == 1


def test_migration_rolls_back_all_tables_when_later_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """중간 테이블 실패 시 앞서 복사한 합성 데이터도 롤백합니다."""
    source_url = _sqlite_url(tmp_path / "rollback-source.db")
    target_url = _sqlite_url(tmp_path / "rollback-target.db")
    table_names = ["synthetic_parent", "synthetic_child"]

    _create_schema(source_url, tuple(table_names))
    _create_schema(target_url, tuple(table_names))

    for table_name in table_names:
        _insert_rows(
            source_url,
            table_name,
            [{"id": 1, "status": "READY", "optional_value": None}],
        )

    _configure_migration_databases(
        monkeypatch,
        source_url,
        target_url,
        table_names,
    )
    monkeypatch.setenv("MIGRATION_DRY_RUN", "false")

    original_copy_table = migration.copy_table

    def fail_on_second_table(
        source_connection: Connection,
        target_connection: Connection,
        source_table: Table,
        target_table: Table,
    ) -> int:
        if source_table.name == "synthetic_child":
            raise RuntimeError("합성 중간 실패")

        return original_copy_table(
            source_connection,
            target_connection,
            source_table,
            target_table,
        )

    monkeypatch.setattr(migration, "copy_table", fail_on_second_table)

    with pytest.raises(RuntimeError, match="합성 중간 실패"):
        migration.main()

    assert _row_count(target_url, "synthetic_parent") == 0
    assert _row_count(target_url, "synthetic_child") == 0


def test_datetime_normalization_preserves_utc_and_asia_seoul_instants() -> None:
    """MySQL의 naive datetime을 컬럼별 원본 시간대로 복원합니다."""
    rows: list[dict[str, Any]] = [
        {
            "started_at": datetime(2026, 1, 1, 9, 0),
            "created_at": datetime(2026, 1, 1, 9, 0),
        }
    ]

    migration.normalize_datetime_values("ocr_job", rows)

    started_at = rows[0]["started_at"]
    created_at = rows[0]["created_at"]

    assert isinstance(started_at, datetime)
    assert isinstance(created_at, datetime)
    assert started_at.tzinfo == UTC
    assert created_at.tzinfo == ZoneInfo("Asia/Seoul")
    assert created_at.astimezone(UTC) == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_datetime_normalization_rejects_unknown_policy() -> None:
    """시간대 정책이 없는 컬럼을 임의로 변환하지 않습니다."""
    rows: list[dict[str, Any]] = [{"unknown_at": datetime(2026, 1, 1, 0, 0)}]

    with pytest.raises(RuntimeError, match="시간대 정책이 없습니다"):
        migration.normalize_datetime_values("synthetic_record", rows)


def test_verifier_accepts_matching_synthetic_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """행 수·PK·NULL·상태 분포가 같으면 정합성 검증을 통과합니다."""
    source_url = _sqlite_url(tmp_path / "verify-success-source.db")
    target_url = _sqlite_url(tmp_path / "verify-success-target.db")
    table_name = "synthetic_record"
    rows = [
        {"id": 1, "status": "READY", "optional_value": None},
        {"id": 2, "status": "COMPLETED", "optional_value": "synthetic"},
    ]

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))
    _insert_rows(source_url, table_name, rows)
    _insert_rows(target_url, table_name, rows)

    _configure_verification_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )

    verification.main()

    assert "정합성 검증을 통과했습니다" in capsys.readouterr().out


def test_verifier_rejects_pk_null_and_status_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """행 수가 같아도 PK·NULL·상태 분포가 다르면 실패해야 합니다."""
    source_url = _sqlite_url(tmp_path / "verify-failure-source.db")
    target_url = _sqlite_url(tmp_path / "verify-failure-target.db")
    table_name = "synthetic_record"

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))
    _insert_rows(
        source_url,
        table_name,
        [{"id": 1, "status": "READY", "optional_value": None}],
    )
    _insert_rows(
        target_url,
        table_name,
        [{"id": 2, "status": "FAILED", "optional_value": "synthetic"}],
    )

    _configure_verification_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )

    # 검증기가 불일치를 발견하면 요약 예외를 반환해야 합니다.
    with pytest.raises(RuntimeError, match="PostgreSQL 이관 검증"):
        verification.main()

    output = capsys.readouterr().out
    assert "PK 집합 불일치" in output
    assert "NULL 개수 불일치" in output
    assert "상태값 분포가 다릅니다" in output


@pytest.mark.parametrize(
    "script_module",
    [migration, verification],
)
def test_database_engine_hides_bound_parameters(
    tmp_path: Path,
    script_module: Any,
) -> None:
    """DB 오류가 발생해도 SQL 파라미터의 실제 값은 예외에 노출하지 않습니다."""
    database_url = _sqlite_url(tmp_path / f"{script_module.__name__.split('.')[-1]}-hidden-parameters.db")
    synthetic_sensitive_value = "synthetic-sensitive-value"

    engine = script_module.create_database_engine(database_url)

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE synthetic_secret (id INTEGER PRIMARY KEY, secret_value TEXT UNIQUE)"
            )
            connection.commit()

            connection.exec_driver_sql(
                "INSERT INTO synthetic_secret (id, secret_value) VALUES (?, ?)",
                (1, synthetic_sensitive_value),
            )
            connection.commit()

            # 동일한 unique 값을 삽입해 실제 SQLAlchemy DB 오류를 발생시킵니다.
            with pytest.raises(IntegrityError) as exc_info:
                connection.exec_driver_sql(
                    "INSERT INTO synthetic_secret (id, secret_value) VALUES (?, ?)",
                    (2, synthetic_sensitive_value),
                )

            connection.rollback()

        error_message = str(exc_info.value)

        assert engine.hide_parameters is True
        assert synthetic_sensitive_value not in error_message
    finally:
        engine.dispose()


def test_verifier_rejects_content_mismatch_without_exposing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """행 구조가 같아도 값이 다르면 실패하고 실제 값은 출력하지 않습니다."""
    source_url = _sqlite_url(tmp_path / "content-source.db")
    target_url = _sqlite_url(tmp_path / "content-target.db")
    table_name = "synthetic_record"

    source_value = "synthetic-source-private-value"
    target_value = "synthetic-target-private-value"

    _create_schema(source_url, (table_name,))
    _create_schema(target_url, (table_name,))

    # PK·상태·NULL 여부는 같고 일반 컬럼값만 다르게 구성합니다.
    _insert_rows(
        source_url,
        table_name,
        [
            {
                "id": 1,
                "status": "COMPLETED",
                "optional_value": source_value,
            }
        ],
    )
    _insert_rows(
        target_url,
        table_name,
        [
            {
                "id": 1,
                "status": "COMPLETED",
                "optional_value": target_value,
            }
        ],
    )

    _configure_verification_databases(
        monkeypatch,
        source_url,
        target_url,
        [table_name],
    )

    with pytest.raises(RuntimeError, match="PostgreSQL 이관 검증"):
        verification.main()

    output = capsys.readouterr().out

    assert "synthetic_record.optional_value: 값 불일치 1건" in output
    assert source_value not in output
    assert target_value not in output
