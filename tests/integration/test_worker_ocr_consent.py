"""#465 스키마의 동의 조회 경계를 격리한 합성 DB 검증. 운영 migration은 추가하지 않는다."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_ocr_consent_repository import SqlAlchemyOcrConsentRepository
from ai_worker.tasks.ocr.consent import OcrConsentDeniedError, OcrConsentGate

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def database():
    url = os.environ.get("OCR_CONSENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires a dedicated synthetic PostgreSQL database")
    schema = "consent458_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        for ddl in [
            'CREATE TABLE "user" (id varchar(36) PRIMARY KEY, account_status varchar(25), is_active boolean)',
            "CREATE TABLE profile (id varchar(36) PRIMARY KEY, user_id varchar(36), profile_type varchar(20))",
            "CREATE TABLE medical_document (id varchar(36) PRIMARY KEY, uploaded_by varchar(36), profile_id varchar(36))",
            "CREATE TABLE ai_job (id varchar(36) PRIMARY KEY, user_id varchar(36))",
            "CREATE TABLE ocr_job (id varchar(36) PRIMARY KEY, ai_job_id varchar(36), document_id varchar(36))",
            "CREATE TABLE user_consent (id varchar(36) PRIMARY KEY, user_id varchar(36), purpose varchar(20), status varchar(20), policy_version varchar(100), granted_at timestamptz, withdrawn_at timestamptz, UNIQUE(user_id,purpose))",
        ]:
            await connection.execute(text(ddl))
        await connection.execute(text("INSERT INTO \"user\" VALUES ('owner','ACTIVE',true), ('other','ACTIVE',true)"))
        await connection.execute(text("INSERT INTO profile VALUES ('profile','owner','SELF')"))
        await connection.execute(text("INSERT INTO medical_document VALUES ('document','owner','profile')"))
        domain_id, job_id = uuid4(), uuid4()
        await connection.execute(text("INSERT INTO ai_job VALUES (:job,'owner')"), {"job": str(job_id)})
        await connection.execute(
            text("INSERT INTO ocr_job VALUES (:domain,:job,'document')"), {"domain": str(domain_id), "job": str(job_id)}
        )
        await connection.execute(
            text("INSERT INTO user_consent VALUES ('consent','owner','OCR','GRANTED','synthetic-policy',:now,NULL)"),
            {"now": datetime.now(UTC)},
        )
    try:
        yield factory, domain_id, job_id
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_separate_transactions_observe_withdrawal_and_reconsent(database):
    factory, domain, job = database
    gate = OcrConsentGate(SqlAlchemyOcrConsentRepository(factory), domain, job, lambda: "synthetic-policy")
    await gate.check()
    async with factory.begin() as session:
        await session.execute(text("UPDATE user_consent SET status='WITHDRAWN', withdrawn_at=now()"))
    with pytest.raises(OcrConsentDeniedError, match="WITHDRAWN"):
        await gate.check()
    async with factory.begin() as session:
        await session.execute(text("UPDATE user_consent SET status='GRANTED', granted_at=now(), withdrawn_at=NULL"))
    await gate.check()  # 새 검사만 허용하며 과거 Job을 자동 재개하지 않는다.


@pytest.mark.parametrize(
    "sql,reason",
    [
        ("DELETE FROM user_consent", "MISSING_CONSENT"),
        ("UPDATE user_consent SET user_id='other'", "MISSING_CONSENT"),
        ("UPDATE user_consent SET purpose='CHAT'", "MISSING_CONSENT"),
        ("UPDATE user_consent SET policy_version='old'", "POLICY_VERSION_MISMATCH"),
        ("UPDATE user_consent SET granted_at=NULL", "INVALID_CONSENT"),
        ("UPDATE profile SET user_id='other'", "OWNER_MISMATCH"),
        ("UPDATE ai_job SET user_id='other'", "OWNER_MISMATCH"),
        ("UPDATE profile SET profile_type='OTHER'", "OWNER_MISMATCH"),
        ('UPDATE "user" SET is_active=false', "ACCOUNT_NOT_ACTIVE"),
        ("UPDATE \"user\" SET account_status='WITHDRAWN'", "ACCOUNT_NOT_ACTIVE"),
        ("DROP TABLE user_consent", "LOOKUP_FAILED"),
    ],
)
async def test_database_denials(database, sql, reason):
    factory, domain, job = database
    async with factory.begin() as session:
        await session.execute(text(sql))
    gate = OcrConsentGate(SqlAlchemyOcrConsentRepository(factory), domain, job, lambda: "synthetic-policy")
    with pytest.raises(OcrConsentDeniedError, match=reason) as caught:
        await gate.check()
    assert caught.value.__context__ is None


async def test_wrong_job_cannot_read_another_documents_consent(database):
    factory, domain, _ = database
    gate = OcrConsentGate(SqlAlchemyOcrConsentRepository(factory), domain, uuid4(), lambda: "synthetic-policy")
    with pytest.raises(OcrConsentDeniedError, match="OWNER_MISMATCH"):
        await gate.check()
