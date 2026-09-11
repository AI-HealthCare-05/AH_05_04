from sqlalchemy import text

EXTENSION_SCHEMA = "test_extensions"


async def ensure_trigram_extension(connection, schema: str = EXTENSION_SCHEMA) -> None:
    await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    await connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA {schema}"))
    current = await connection.scalar(
        text(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
        )
    )
    if current != schema:
        await connection.execute(text(f"ALTER EXTENSION pg_trgm SET SCHEMA {schema}"))
