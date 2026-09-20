from pathlib import Path

from infra.python.provision_database_roles import RUNTIME_APPEND_ONLY_TABLES
from scripts.ci.check_protected_table_writes import APPROVED_WRITERS, protected_writes

TABLES = {
    "rag_citation_authorization_source_decision",
    "rag_citation_authorization_member_decision",
    "rag_citation_authorization_receipt",
    "rag_citation_authorization_receipt_selection",
}
WRITER = "ai_worker/adapters/sqlalchemy_citation_authorization_authority.py"


def test_citation_authority_tables_are_runtime_append_only() -> None:
    assert TABLES <= RUNTIME_APPEND_ONLY_TABLES


def test_only_worker_core_store_can_write_citation_authority_tables() -> None:
    assert {table: APPROVED_WRITERS[table] for table in TABLES} == {table: frozenset({WRITER}) for table in TABLES}


def test_protected_write_scanner_detects_sqlalchemy_core_table_writes() -> None:
    source = """
from sqlalchemy import column, insert, table
authority = table('rag_citation_authorization_receipt', column('id'))
statement = insert(authority).values(id='x')
"""
    assert protected_writes(Path("example.py"), source) == {("rag_citation_authorization_receipt", 4)}
