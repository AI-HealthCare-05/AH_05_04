"""MySQL 데이터를 빈 PostgreSQL 스키마로 일괄 이관합니다.

주의:
- PostgreSQL 대상 테이블은 Alembic으로 미리 생성되어 있어야 합니다.
- 대상 업무 테이블에 데이터가 하나라도 있으면 실행을 중단합니다.
- 실제 데이터나 식별자는 출력하지 않고 테이블별 행 수만 출력합니다.
- 전체 복사는 하나의 PostgreSQL 트랜잭션에서 실행됩니다.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    DateTime,
    MetaData,
    Table,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import URL, Connection, Engine

TABLE_ORDER = [
    "user",
    "medical_document",
    "ocr_job",
    "extracted_field",
    "prescription",
    "medication",
    "knowledge_document",
    "knowledge_chunk",
    "guide",
    "guide_citation",
    "chat_session",
    "chat_message",
    "chat_citation",
]

BATCH_SIZE = 1_000

# 기존 MySQL 서버·세션 및 애플리케이션 기본 시간대입니다.
# MySQL DATETIME에는 시간대 정보가 없으므로 생성 주체에 따라
# 원래 시간대를 명시적으로 복원해야 합니다.
LEGACY_LOCAL_TIMEZONE = ZoneInfo("Asia/Seoul")


# 서비스 코드에서 datetime.now(UTC)로 직접 생성한 컬럼입니다.
# MySQL에는 timezone 정보 없이 저장됐지만 실제 의미는 UTC입니다.
UTC_SOURCE_DATETIME_COLUMNS = frozenset(
    {
        ("ocr_job", "started_at"),
        ("ocr_job", "completed_at"),
        ("extracted_field", "confirmed_at"),
        ("prescription", "confirmed_at"),
        ("guide", "completed_at"),
        ("chat_message", "completed_at"),
    }
)


# MySQL의 server_default=now() 또는 config.TIMEZONE으로 생성된 컬럼입니다.
# 기존 MySQL 서버와 애플리케이션 설정이 Asia/Seoul이므로 KST로 복원합니다.
LOCAL_SOURCE_DATETIME_COLUMNS = frozenset(
    {
        ("user", "last_login"),
        ("user", "created_at"),
        ("user", "updated_at"),
        ("medical_document", "uploaded_at"),
        ("ocr_job", "created_at"),
        ("extracted_field", "created_at"),
        ("extracted_field", "updated_at"),
        ("prescription", "created_at"),
        ("medication", "created_at"),
        ("knowledge_document", "created_at"),
        ("knowledge_chunk", "created_at"),
        ("guide", "requested_at"),
        ("guide_citation", "created_at"),
        ("chat_session", "last_message_at"),
        ("chat_session", "created_at"),
        ("chat_session", "updated_at"),
        ("chat_message", "created_at"),
        ("chat_citation", "created_at"),
    }
)


ALL_DATETIME_COLUMNS = UTC_SOURCE_DATETIME_COLUMNS | LOCAL_SOURCE_DATETIME_COLUMNS


def required_env(name: str) -> str:
    """필수 환경변수를 읽되 값 자체는 오류 메시지에 노출하지 않습니다."""
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"{name} 환경변수가 필요합니다.")

    return value


def env_int(name: str, default: int) -> int:
    """포트 등 정수 환경변수를 안전하게 변환합니다."""
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from exc


def env_bool(name: str, default: bool = False) -> bool:
    """true/false 형식의 환경변수를 bool로 변환합니다."""
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(f"{name}은 true 또는 false여야 합니다.")


def build_mysql_url() -> URL:
    """특수문자가 포함된 비밀번호도 안전하게 처리하도록 URL.create를 사용합니다."""
    return URL.create(
        drivername="mysql+pymysql",
        username=required_env("MYSQL_SOURCE_USER"),
        password=required_env("MYSQL_SOURCE_PASSWORD"),
        host=os.getenv("MYSQL_SOURCE_HOST", "127.0.0.1"),
        port=env_int("MYSQL_SOURCE_PORT", 3306),
        database=required_env("MYSQL_SOURCE_DB"),
    )


def build_postgresql_url() -> URL:
    """현재 PostgreSQL 로컬 컨테이너에 접속할 동기 드라이버 URL을 생성합니다."""
    return URL.create(
        drivername="postgresql+psycopg",
        username=required_env("DB_USER"),
        password=required_env("DB_PASSWORD"),
        host=os.getenv("POSTGRES_MIGRATION_HOST", "127.0.0.1"),
        port=env_int("DB_EXPOSE_PORT", 5432),
        database=required_env("DB_NAME"),
    )


def reflect_tables(engine: Engine) -> dict[str, Table]:
    """마이그레이션 대상 테이블만 DB에서 읽습니다."""
    metadata = MetaData()
    metadata.reflect(bind=engine, only=TABLE_ORDER)

    missing_tables = [table_name for table_name in TABLE_ORDER if table_name not in metadata.tables]

    if missing_tables:
        joined = ", ".join(missing_tables)
        raise RuntimeError(f"필수 테이블을 찾을 수 없습니다: {joined}")

    return {table_name: metadata.tables[table_name] for table_name in TABLE_ORDER}


def table_count(connection: Connection, table: Table) -> int:
    """테이블 전체 행 수를 반환합니다."""
    count = connection.execute(select(func.count()).select_from(table)).scalar_one()

    return int(count)


def validate_columns(
    source_table: Table,
    target_table: Table,
) -> None:
    """MySQL과 PostgreSQL의 컬럼 구성이 동일한지 확인합니다."""
    source_columns = set(source_table.columns.keys())
    target_columns = set(target_table.columns.keys())

    if source_columns == target_columns:
        return

    missing_in_target = sorted(source_columns - target_columns)
    extra_in_target = sorted(target_columns - source_columns)

    raise RuntimeError(
        f"{source_table.name} 컬럼 불일치: PostgreSQL 누락={missing_in_target}, PostgreSQL 추가={extra_in_target}"
    )


def validate_datetime_mapping(table: Table) -> None:
    """모든 DATETIME 컬럼에 명시적인 원본 시간대 정책이 있는지 확인합니다.

    매핑되지 않은 시간 컬럼을 임의의 시간대로 처리하지 않고
    마이그레이션을 중단하도록 하는 안전장치입니다.
    """
    datetime_columns = {(table.name, column.name) for column in table.columns if isinstance(column.type, DateTime)}

    missing_mappings = sorted(datetime_columns - ALL_DATETIME_COLUMNS)
    stale_mappings = sorted(
        {mapping for mapping in ALL_DATETIME_COLUMNS if mapping[0] == table.name} - datetime_columns
    )

    if missing_mappings:
        raise RuntimeError(f"{table.name}: 시간대 정책이 없는 DATETIME 컬럼이 있습니다: {missing_mappings}")

    if stale_mappings:
        raise RuntimeError(f"{table.name}: 실제 스키마에 없는 DATETIME 정책이 있습니다: {stale_mappings}")


def source_datetime_timezone(
    table_name: str,
    column_name: str,
) -> tzinfo:
    """MySQL DATETIME 값이 원래 생성된 시간대를 반환합니다."""
    key = (table_name, column_name)

    if key in UTC_SOURCE_DATETIME_COLUMNS:
        return UTC

    if key in LOCAL_SOURCE_DATETIME_COLUMNS:
        return LEGACY_LOCAL_TIMEZONE

    raise RuntimeError(f"{table_name}.{column_name}: 명시적인 원본 시간대 정책이 없습니다.")


def normalize_datetime_values(
    table_name: str,
    rows: list[dict[str, Any]],
) -> None:
    """MySQL의 naive datetime에 원래 시간대를 복원합니다.

    UTC로 생성된 값은 UTC로, MySQL now() 또는 애플리케이션 기본
    시간대로 생성된 값은 Asia/Seoul로 해석합니다.

    실제 시각 자체를 더하거나 빼지 않고 tzinfo만 복원합니다.
    PostgreSQL 드라이버가 이를 올바른 instant로 저장합니다.
    """
    for row in rows:
        for column_name, value in row.items():
            if not isinstance(value, datetime):
                continue

            if value.tzinfo is not None:
                # 예상과 달리 이미 timezone-aware라면 이중 변환하지 않습니다.
                continue

            original_timezone = source_datetime_timezone(
                table_name,
                column_name,
            )

            row[column_name] = value.replace(tzinfo=original_timezone)


def iter_rows(
    connection: Connection,
    table: Table,
) -> Iterator[list[dict[str, Any]]]:
    """PK 순서로 데이터를 읽어 재현 가능한 배치를 생성합니다."""
    statement = select(table)
    primary_key_columns = list(table.primary_key.columns)

    if primary_key_columns:
        statement = statement.order_by(*primary_key_columns)

    result = connection.execution_options(stream_results=True).execute(statement)

    for partition in result.mappings().partitions(BATCH_SIZE):
        yield [dict(row) for row in partition]


def run_preflight(
    source_connection: Connection,
    target_connection: Connection,
    source_tables: dict[str, Table],
    target_tables: dict[str, Table],
) -> dict[str, int]:
    """복사 전에 컬럼, 원본 데이터 및 대상 테이블 상태를 검증합니다."""
    source_counts: dict[str, int] = {}
    total_source_rows = 0

    for table_name in TABLE_ORDER:
        source_table = source_tables[table_name]
        target_table = target_tables[table_name]

        validate_columns(source_table, target_table)

        # 스키마의 모든 DATETIME 컬럼에 원본 시간대 정책이 있는지 검사합니다.
        validate_datetime_mapping(source_table)

        source_count = table_count(source_connection, source_table)
        target_count = table_count(target_connection, target_table)

        source_counts[table_name] = source_count
        total_source_rows += source_count

        print(f"[사전검증] {table_name}: MySQL={source_count}, PostgreSQL={target_count}")

        # 재실행이나 기존 PostgreSQL 데이터 덮어쓰기를 차단합니다.
        if target_count != 0:
            raise RuntimeError(
                f"PostgreSQL {table_name} 테이블이 비어 있지 않습니다. 중복 이관을 방지하기 위해 실행을 중단합니다."
            )

    if total_source_rows == 0 and not env_bool("ALLOW_EMPTY_SOURCE"):
        raise RuntimeError(
            "MySQL 원본 업무 데이터가 모두 0건입니다. "
            "잘못된 MySQL DB에 연결했을 수 있습니다. "
            "빈 DB 이관이 의도된 경우에만 ALLOW_EMPTY_SOURCE=true를 설정하세요."
        )

    return source_counts


def copy_table(
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
) -> int:
    """한 테이블을 배치 단위로 복사하고 복사 행 수를 반환합니다."""
    copied_count = 0

    for rows in iter_rows(source_connection, source_table):
        # MySQL DATETIME에 생성 당시의 UTC 또는 KST 시간대를 복원합니다.
        normalize_datetime_values(source_table.name, rows)
        target_connection.execute(insert(target_table), rows)
        copied_count += len(rows)

    return copied_count


def main() -> None:
    """사전검증 후 전체 데이터를 하나의 트랜잭션으로 이관합니다."""
    source_engine = create_engine(
        build_mysql_url(),
        pool_pre_ping=True,
    )
    target_engine = create_engine(
        build_postgresql_url(),
        pool_pre_ping=True,
    )

    try:
        source_tables = reflect_tables(source_engine)
        target_tables = reflect_tables(target_engine)

        with source_engine.connect() as source_connection:
            # target.begin() 범위 전체가 하나의 트랜잭션입니다.
            # 중간 테이블에서 실패하면 앞서 복사한 데이터도 롤백됩니다.
            with target_engine.begin() as target_connection:
                source_counts = run_preflight(
                    source_connection,
                    target_connection,
                    source_tables,
                    target_tables,
                )

                # 안전을 위해 환경변수를 생략하면 사전검증만 수행합니다.
                # 실제 복사는 MIGRATION_DRY_RUN=false를 명시한 경우에만 허용합니다.
                if env_bool("MIGRATION_DRY_RUN", default=True):
                    print("사전검증만 완료했습니다. MIGRATION_DRY_RUN=false로 실제 이관을 실행하세요.")
                    return

                for table_name in TABLE_ORDER:
                    copied_count = copy_table(
                        source_connection,
                        target_connection,
                        source_tables[table_name],
                        target_tables[table_name],
                    )

                    expected_count = source_counts[table_name]

                    if copied_count != expected_count:
                        raise RuntimeError(f"{table_name} 복사 건수 불일치: 예상={expected_count}, 실제={copied_count}")

                    print(f"[이관완료] {table_name}: {copied_count}건")

        print("MySQL에서 PostgreSQL로 데이터 이관을 완료했습니다.")

    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == "__main__":
    main()
