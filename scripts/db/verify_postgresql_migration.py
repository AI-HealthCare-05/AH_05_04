"""MySQL → PostgreSQL 데이터 이관 결과를 검증합니다.

검증 항목:
- 테이블별 행 수
- 기본키(PK) 컬럼 및 PK 집합
- PK 기준 전체 일반 컬럼값
- 컬럼별 NULL 개수
- 상태 컬럼의 값 분포
- 날짜·시간 컬럼의 실제 시각
- 외래키(FK) 정의 및 PostgreSQL의 고아 레코드

보안:
- 실제 데이터값, 사용자 식별자, PK 값은 출력하지 않습니다.
- 검증 실패 시 테이블명, 컬럼명 및 건수만 출력합니다.
"""

import os
from collections.abc import Hashable
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    MetaData,
    Table,
    and_,
    create_engine,
    func,
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

# 기존 MySQL 서버·세션과 애플리케이션의 기본 시간대입니다.
LEGACY_LOCAL_TIMEZONE = ZoneInfo("Asia/Seoul")


# 서비스 코드에서 datetime.now(UTC)로 생성한 컬럼입니다.
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


# MySQL server_default=now() 또는 config.TIMEZONE으로 생성한 컬럼입니다.
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
    """필수 환경변수를 읽되 실제 값은 오류 메시지에 노출하지 않습니다."""
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"{name} 환경변수가 필요합니다.")

    return value


def env_int(name: str, default: int) -> int:
    """포트 환경변수를 정수로 변환합니다."""
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from exc


def build_mysql_url() -> URL:
    """기존 MySQL 원본 DB의 동기 접속 URL을 생성합니다."""
    return URL.create(
        drivername="mysql+pymysql",
        username=required_env("MYSQL_SOURCE_USER"),
        password=required_env("MYSQL_SOURCE_PASSWORD"),
        host=os.getenv("MYSQL_SOURCE_HOST", "127.0.0.1"),
        port=env_int("MYSQL_SOURCE_PORT", 3306),
        database=required_env("MYSQL_SOURCE_DB"),
    )


def build_postgresql_url() -> URL:
    """이관 대상 PostgreSQL의 동기 접속 URL을 생성합니다."""
    return URL.create(
        drivername="postgresql+psycopg",
        username=required_env("DB_USER"),
        password=required_env("DB_PASSWORD"),
        host=os.getenv("POSTGRES_MIGRATION_HOST", "127.0.0.1"),
        port=env_int("DB_EXPOSE_PORT", 5432),
        database=required_env("DB_NAME"),
    )


def create_database_engine(url: URL) -> Engine:
    """검증 오류에 실제 DB 값과 연결 파라미터가 노출되지 않는 엔진을 생성합니다."""
    return create_engine(
        url,
        pool_pre_ping=True,
        # 검증 중 발생한 SQLAlchemy 예외에서도 실제 값을 숨깁니다.
        hide_parameters=True,
    )


def reflect_tables(engine: Engine) -> dict[str, Table]:
    """DB 스키마를 읽고 검증 대상 테이블이 모두 있는지 확인합니다."""
    metadata = MetaData()
    metadata.reflect(bind=engine)

    missing_tables = [table_name for table_name in TABLE_ORDER if table_name not in metadata.tables]

    if missing_tables:
        joined = ", ".join(missing_tables)
        raise RuntimeError(f"필수 테이블을 찾을 수 없습니다: {joined}")

    return {table_name: metadata.tables[table_name] for table_name in TABLE_ORDER}


def table_count(connection: Connection, table: Table) -> int:
    """테이블 전체 행 수를 반환합니다."""
    count = connection.execute(select(func.count()).select_from(table)).scalar_one()

    return int(count)


def primary_key_names(table: Table) -> tuple[str, ...]:
    """PK 컬럼명을 선언 순서대로 반환합니다."""
    return tuple(column.name for column in table.primary_key.columns)


def primary_key_values(
    connection: Connection,
    table: Table,
) -> set[tuple[Hashable, ...]]:
    """PK 값 집합을 메모리에서 비교합니다.

    PK 값은 비교에만 사용하고 화면에는 출력하지 않습니다.
    """
    columns = list(table.primary_key.columns)

    if not columns:
        return set()

    rows = connection.execute(select(*columns)).all()

    return {tuple(value for value in row) for row in rows}


def foreign_key_signatures(
    table: Table,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """FK의 로컬 컬럼, 참조 테이블, 참조 컬럼 구조를 반환합니다."""
    signatures: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()

    for constraint in table.foreign_key_constraints:
        local_columns = tuple(element.parent.name for element in constraint.elements)
        remote_table = constraint.elements[0].column.table.name
        remote_columns = tuple(element.column.name for element in constraint.elements)

        signatures.add((local_columns, remote_table, remote_columns))

    return signatures


def status_column_names(table: Table) -> list[str]:
    """상태 의미를 가진 것으로 보이는 컬럼명을 찾습니다."""
    return [column.name for column in table.columns if column.name == "status" or column.name.endswith("_status")]


def datetime_column_names(table: Table) -> list[str]:
    """날짜·시간 타입 컬럼명을 찾습니다."""
    return [column.name for column in table.columns if isinstance(column.type, DateTime)]


def null_count(
    connection: Connection,
    table: Table,
    column_name: str,
) -> int:
    """특정 컬럼의 NULL 행 수를 반환합니다."""
    column = table.columns[column_name]

    count = connection.execute(select(func.count()).select_from(table).where(column.is_(None))).scalar_one()

    return int(count)


def value_distribution(
    connection: Connection,
    table: Table,
    column_name: str,
) -> dict[Any, int]:
    """상태 컬럼의 값별 행 수를 반환합니다.

    반환값은 DB 간 비교에만 사용하며 실제 상태값은 출력하지 않습니다.
    """
    column = table.columns[column_name]

    rows = connection.execute(select(column, func.count()).select_from(table).group_by(column)).all()

    return {value: int(count) for value, count in rows}


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


def normalize_source_datetime(
    table_name: str,
    column_name: str,
    value: datetime | None,
) -> datetime | None:
    """MySQL datetime에 원래 시간대를 복원한 뒤 UTC로 정규화합니다."""
    if value is None:
        return None

    if value.tzinfo is None:
        original_timezone = source_datetime_timezone(
            table_name,
            column_name,
        )
        value = value.replace(tzinfo=original_timezone)

    return value.astimezone(UTC)


def normalize_target_datetime(
    table_name: str,
    column_name: str,
    value: datetime | None,
) -> datetime | None:
    """PostgreSQL timestamptz 값을 UTC로 정규화합니다."""
    if value is None:
        return None

    if value.tzinfo is None:
        raise RuntimeError(f"{table_name}.{column_name}: PostgreSQL 값에 시간대 정보가 없습니다.")

    return value.astimezone(UTC)


def normalize_source_column_value(
    table_name: str,
    column_name: str,
    value: Any,
) -> Any:
    """의도적으로 변환한 MySQL 원본값을 PostgreSQL 기대값으로 정규화합니다."""
    if (table_name, column_name) == ("user", "email") and isinstance(
        value,
        str,
    ):
        # 이관 스크립트와 동일한 소문자 변환 규칙으로 기대값을 계산합니다.
        return value.lower()

    return value


def column_values_by_primary_key(
    connection: Connection,
    table: Table,
    column_name: str,
) -> dict[tuple[Hashable, ...], Any]:
    """PK별 컬럼값을 읽되 실제 PK와 값은 출력하지 않습니다."""
    primary_key_columns = list(table.primary_key.columns)

    if not primary_key_columns:
        raise RuntimeError(f"{table.name}: 컬럼값 비교에 필요한 PK가 없습니다.")

    value_column = table.columns[column_name]
    rows = connection.execute(select(*primary_key_columns, value_column)).all()

    primary_key_length = len(primary_key_columns)

    return {tuple(row[:primary_key_length]): row[primary_key_length] for row in rows}


def datetime_values_by_primary_key(
    connection: Connection,
    table: Table,
    column_name: str,
) -> dict[tuple[Hashable, ...], datetime | None]:
    """PK별 datetime 값을 읽습니다.

    실제 PK와 datetime 값은 DB 간 비교에만 사용하고 출력하지 않습니다.
    """
    primary_key_columns = list(table.primary_key.columns)

    if not primary_key_columns:
        raise RuntimeError(f"{table.name}: datetime 비교에 필요한 PK가 없습니다.")

    datetime_column = table.columns[column_name]

    rows = connection.execute(select(*primary_key_columns, datetime_column)).all()

    values: dict[
        tuple[Hashable, ...],
        datetime | None,
    ] = {}

    primary_key_length = len(primary_key_columns)

    for row in rows:
        primary_key = tuple(row[:primary_key_length])
        datetime_value = row[primary_key_length]

        values[primary_key] = datetime_value

    return values


def count_postgresql_orphans(
    connection: Connection,
    table: Table,
    constraint: ForeignKeyConstraint,
) -> int:
    """PostgreSQL에서 참조 대상이 없는 FK 행 수를 계산합니다."""
    elements = list(constraint.elements)

    local_columns = [element.parent for element in elements]
    remote_columns = [element.column for element in elements]
    remote_table = remote_columns[0].table

    join_condition = and_(
        *[
            local_column == remote_column
            for local_column, remote_column in zip(
                local_columns,
                remote_columns,
                strict=True,
            )
        ]
    )

    # nullable FK가 모두 NULL인 행은 정상이며 고아 데이터로 보지 않습니다.
    local_values_present = and_(*[local_column.is_not(None) for local_column in local_columns])

    remote_row_missing = remote_columns[0].is_(None)

    statement = (
        select(func.count())
        .select_from(table.outerjoin(remote_table, join_condition))
        .where(local_values_present)
        .where(remote_row_missing)
    )

    return int(connection.execute(statement).scalar_one())


def verify_columns(
    table_name: str,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """MySQL과 PostgreSQL의 컬럼명 집합을 비교합니다."""
    source_columns = set(source_table.columns.keys())
    target_columns = set(target_table.columns.keys())

    if source_columns != target_columns:
        failures.append(f"{table_name}: 컬럼 구성이 다릅니다.")


def verify_row_counts(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> tuple[int, int]:
    """테이블 행 수를 비교하고 결과를 반환합니다."""
    source_count = table_count(
        source_connection,
        source_table,
    )
    target_count = table_count(
        target_connection,
        target_table,
    )

    if source_count != target_count:
        failures.append(f"{table_name}: 행 수 불일치 MySQL={source_count}, PostgreSQL={target_count}")

    return source_count, target_count


def verify_primary_keys(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """PK 컬럼 정의와 PK 값 집합을 비교합니다."""
    source_names = primary_key_names(source_table)
    target_names = primary_key_names(target_table)

    if source_names != target_names:
        failures.append(f"{table_name}: PK 컬럼 구성이 다릅니다.")
        return

    if not source_names:
        failures.append(f"{table_name}: PK가 정의되어 있지 않습니다.")
        return

    source_values = primary_key_values(
        source_connection,
        source_table,
    )
    target_values = primary_key_values(
        target_connection,
        target_table,
    )

    if source_values != target_values:
        missing_count = len(source_values - target_values)
        extra_count = len(target_values - source_values)

        # 실제 PK 값은 민감정보가 될 수 있으므로 출력하지 않습니다.
        failures.append(f"{table_name}: PK 집합 불일치 누락={missing_count}, 추가={extra_count}")


def verify_null_counts(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """모든 공통 컬럼의 NULL 개수를 비교합니다."""
    common_columns = sorted(set(source_table.columns.keys()) & set(target_table.columns.keys()))

    for column_name in common_columns:
        source_nulls = null_count(
            source_connection,
            source_table,
            column_name,
        )
        target_nulls = null_count(
            target_connection,
            target_table,
            column_name,
        )

        if source_nulls != target_nulls:
            failures.append(
                f"{table_name}.{column_name}: NULL 개수 불일치 MySQL={source_nulls}, PostgreSQL={target_nulls}"
            )


def verify_status_distributions(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """status 또는 *_status 컬럼의 값 분포를 비교합니다."""
    common_status_columns = sorted(set(status_column_names(source_table)) & set(status_column_names(target_table)))

    for column_name in common_status_columns:
        source_distribution = value_distribution(
            source_connection,
            source_table,
            column_name,
        )
        target_distribution = value_distribution(
            target_connection,
            target_table,
            column_name,
        )

        if source_distribution != target_distribution:
            # 실제 상태값은 출력하지 않고 불일치 사실만 기록합니다.
            failures.append(f"{table_name}.{column_name}: 상태값 분포가 다릅니다.")


def verify_column_values(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """PK가 같은 행의 모든 일반 컬럼값을 비교합니다.

    날짜·시간 컬럼은 시간대 정규화가 필요하므로
    verify_datetime_values()에서 별도로 비교합니다.
    실제 PK와 컬럼값은 오류 메시지에 출력하지 않습니다.
    """
    source_primary_keys = primary_key_names(source_table)
    target_primary_keys = primary_key_names(target_table)

    # PK 구성이 다르면 verify_primary_keys()에서 이미 실패로 기록합니다.
    if not source_primary_keys or source_primary_keys != target_primary_keys:
        return

    common_columns = set(source_table.columns.keys()) & set(target_table.columns.keys())
    datetime_columns = set(datetime_column_names(source_table)) | set(datetime_column_names(target_table))

    # PK 자체는 verify_primary_keys()에서, datetime은 전용 함수에서 검증합니다.
    comparable_columns = sorted(common_columns - set(source_primary_keys) - datetime_columns)

    for column_name in comparable_columns:
        source_values = column_values_by_primary_key(
            source_connection,
            source_table,
            column_name,
        )
        target_values = column_values_by_primary_key(
            target_connection,
            target_table,
            column_name,
        )

        common_primary_keys = source_values.keys() & target_values.keys()

        mismatch_count = sum(
            normalize_source_column_value(
                table_name,
                column_name,
                source_values[primary_key],
            )
            != target_values[primary_key]
            for primary_key in common_primary_keys
        )

        if mismatch_count:
            # 의료정보나 사용자정보 대신 불일치 건수만 기록합니다.
            failures.append(f"{table_name}.{column_name}: 값 불일치 {mismatch_count}건")


def verify_datetime_values(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """PK별 날짜·시간을 UTC instant 기준으로 정확하게 비교합니다."""
    source_datetime_columns = set(datetime_column_names(source_table))
    target_datetime_columns = set(datetime_column_names(target_table))

    common_datetime_columns = sorted(source_datetime_columns & target_datetime_columns)

    configured_columns = {
        column_name for configured_table, column_name in ALL_DATETIME_COLUMNS if configured_table == table_name
    }

    missing_mappings = sorted(source_datetime_columns - configured_columns)
    stale_mappings = sorted(configured_columns - source_datetime_columns)

    if missing_mappings:
        failures.append(f"{table_name}: 시간대 정책이 없는 컬럼이 있습니다: {missing_mappings}")
        return

    if stale_mappings:
        failures.append(f"{table_name}: 실제 스키마에 없는 시간대 정책이 있습니다: {stale_mappings}")
        return

    for column_name in common_datetime_columns:
        source_values = datetime_values_by_primary_key(
            source_connection,
            source_table,
            column_name,
        )
        target_values = datetime_values_by_primary_key(
            target_connection,
            target_table,
            column_name,
        )

        common_primary_keys = source_values.keys() & target_values.keys()

        mismatch_count = 0

        try:
            for primary_key in common_primary_keys:
                normalized_source = normalize_source_datetime(
                    table_name,
                    column_name,
                    source_values[primary_key],
                )
                normalized_target = normalize_target_datetime(
                    table_name,
                    column_name,
                    target_values[primary_key],
                )

                if normalized_source != normalized_target:
                    mismatch_count += 1

        except RuntimeError as exc:
            failures.append(str(exc))
            continue

        if mismatch_count:
            # 실제 PK와 시간값은 민감정보가 될 수 있으므로 출력하지 않습니다.
            failures.append(f"{table_name}.{column_name}: 실제 시각 불일치 {mismatch_count}건")


def verify_foreign_keys(
    table_name: str,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> None:
    """FK 정의를 비교하고 PostgreSQL 고아 데이터를 검사합니다."""
    source_signatures = foreign_key_signatures(source_table)
    target_signatures = foreign_key_signatures(target_table)

    if source_signatures != target_signatures:
        failures.append(f"{table_name}: MySQL과 PostgreSQL의 FK 구성이 다릅니다.")

    for constraint in target_table.foreign_key_constraints:
        orphan_count = count_postgresql_orphans(
            target_connection,
            target_table,
            constraint,
        )

        if orphan_count == 0:
            continue

        local_columns = ",".join(element.parent.name for element in constraint.elements)
        remote_table = constraint.elements[0].column.table.name

        failures.append(f"{table_name}.{local_columns} → {remote_table}: FK 고아 데이터 {orphan_count}건")


def verify_table(
    table_name: str,
    source_connection: Connection,
    target_connection: Connection,
    source_table: Table,
    target_table: Table,
    failures: list[str],
) -> int:
    """한 테이블에 대한 모든 검증을 수행합니다."""
    verify_columns(
        table_name,
        source_table,
        target_table,
        failures,
    )

    source_count, target_count = verify_row_counts(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )

    verify_primary_keys(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )
    verify_null_counts(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )
    # 행 수와 NULL 개수뿐 아니라 PK별 실제 컬럼값도 비교합니다.
    verify_column_values(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )

    verify_status_distributions(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )
    # 단순 표기값이 아니라 UTC instant 기준으로 PK별 시간을 비교합니다.
    verify_datetime_values(
        table_name,
        source_connection,
        target_connection,
        source_table,
        target_table,
        failures,
    )
    verify_foreign_keys(
        table_name,
        target_connection,
        source_table,
        target_table,
        failures,
    )

    print(f"[검증] {table_name}: MySQL={source_count}, PostgreSQL={target_count}")

    return source_count


def main() -> None:
    """전체 테이블을 검증하고 하나라도 다르면 실패 처리합니다."""
    # 원본과 대상 DB에서 발생한 예외에 실제 데이터가 포함되지 않게 합니다.
    source_engine = create_database_engine(build_mysql_url())
    target_engine = create_database_engine(build_postgresql_url())

    failures: list[str] = []
    total_source_rows = 0

    try:
        source_tables = reflect_tables(source_engine)
        target_tables = reflect_tables(target_engine)

        with source_engine.connect() as source_connection:
            with target_engine.connect() as target_connection:
                for table_name in TABLE_ORDER:
                    total_source_rows += verify_table(
                        table_name,
                        source_connection,
                        target_connection,
                        source_tables[table_name],
                        target_tables[table_name],
                        failures,
                    )

        if total_source_rows == 0:
            failures.append("MySQL 원본 업무 데이터가 모두 0건입니다.")

        if failures:
            print("\n[검증 실패]")

            for failure in failures:
                print(f"- {failure}")

            raise RuntimeError(f"PostgreSQL 이관 검증에서 {len(failures)}개 문제가 발견되었습니다.")

        print("\nMySQL → PostgreSQL 이관 정합성 검증을 통과했습니다.")

    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == "__main__":
    main()
