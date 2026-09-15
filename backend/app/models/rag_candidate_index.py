from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar
from app.models.rag_catalog import RagMedicationSearchEntryType


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RagCandidateIndexStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class RagCandidateIndexBuildMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    HYBRID = "HYBRID"


class RagCandidateIndexEntityType(StrEnum):
    PRODUCT = "PRODUCT"
    INGREDIENT = "INGREDIENT"


class RagCandidateIndexVersion(Base):
    """RAG-07A(#167) 순수 build 결과의 영속 스냅샷 (RAG-07B, #168).

    ``status``는 이 테이블을 쓰는 build transaction에서 항상 ``BUILDING``으로 강제된다.
    ``READY``/``RETIRED``와 환경 pointer 전환은 #583의 몫이므로 여기서는 다루지 않는다
    (RAG-12A ``rag_runtime_release_bundle``과 동일한 경계, ``rag_runtime_repository.py``의
    ``build_runtime_bundle`` 참고). partial/failed build는 row 자체를 남기지 않는다.
    """

    __tablename__ = "rag_candidate_index_version"
    __table_args__ = (
        UniqueConstraint("index_code", "index_version", name="uq_rag_candidate_index_version"),
        UniqueConstraint("content_hash", name="uq_rag_candidate_index_content_hash"),
        # 같은 index_code에 대해 동시에 두 개의 BUILDING row가 만들어지는 것을 DB 레벨에서 막는다.
        Index(
            "uq_rag_candidate_index_building_per_code",
            "index_code",
            unique=True,
            postgresql_where=text("status = 'BUILDING'"),
        ),
        Index(
            "uq_rag_candidate_index_ready_per_code",
            "index_code",
            unique=True,
            postgresql_where=text("status = 'READY'"),
        ),
        CheckConstraint(
            f"status IN ({_sql_in_list(RagCandidateIndexStatus)})",
            name="chk_rag_candidate_index_status",
        ),
        CheckConstraint(
            f"build_mode IN ({_sql_in_list(RagCandidateIndexBuildMode)})",
            name="chk_rag_candidate_index_build_mode",
        ),
        CheckConstraint("length(trim(index_code)) > 0", name="chk_rag_candidate_index_code_nonblank"),
        CheckConstraint("length(trim(index_version)) > 0", name="chk_rag_candidate_index_version_nonblank"),
        CheckConstraint("length(trim(catalog_version)) > 0", name="chk_rag_candidate_index_catalog_version_nonblank"),
        CheckConstraint(
            "catalog_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_catalog_manifest_hash"
        ),
        CheckConstraint("length(trim(schema_version)) > 0", name="chk_rag_candidate_index_schema_version_nonblank"),
        CheckConstraint(
            "length(trim(normalization_version)) > 0", name="chk_rag_candidate_index_normalization_version_nonblank"
        ),
        CheckConstraint(
            "length(trim(lexical_config_version)) > 0", name="chk_rag_candidate_index_lexical_config_nonblank"
        ),
        CheckConstraint("length(trim(search_order_version)) > 0", name="chk_rag_candidate_index_search_order_nonblank"),
        CheckConstraint("candidate_limit > 0", name="chk_rag_candidate_index_candidate_limit"),
        CheckConstraint(
            "display_limit > 0 AND display_limit <= candidate_limit", name="chk_rag_candidate_index_display_limit"
        ),
        CheckConstraint("member_count >= 0", name="chk_rag_candidate_index_member_count"),
        CheckConstraint("product_identity_count >= 0", name="chk_rag_candidate_index_product_identity_count"),
        CheckConstraint("product_name_count >= 0", name="chk_rag_candidate_index_product_name_count"),
        CheckConstraint("approved_alias_count >= 0", name="chk_rag_candidate_index_approved_alias_count"),
        CheckConstraint("vector_count >= 0", name="chk_rag_candidate_index_vector_count"),
        CheckConstraint("member_set_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_set_hash"),
        CheckConstraint("configuration_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_configuration_hash"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_content_hash"),
        # LEXICAL_ONLY는 embedding 관련 컬럼이 전부 비어 있어야 하고, HYBRID는 전부 채워져야 한다.
        CheckConstraint(
            "(build_mode = 'LEXICAL_ONLY' AND embedding_provider IS NULL AND embedding_model IS NULL "
            "AND embedding_model_version IS NULL AND embedding_dimension IS NULL AND distance_metric IS NULL) OR "
            "(build_mode = 'HYBRID' AND embedding_provider IS NOT NULL AND embedding_model IS NOT NULL "
            "AND embedding_model_version IS NOT NULL AND embedding_dimension IS NOT NULL "
            "AND distance_metric IS NOT NULL)",
            name="chk_rag_candidate_index_build_mode_embedding_shape",
        ),
        CheckConstraint(
            "distance_metric IS NULL OR distance_metric = 'COSINE'", name="chk_rag_candidate_index_distance_metric"
        ),
        CheckConstraint(
            "embedding_dimension IS NULL OR embedding_dimension BETWEEN 1 AND 2000",
            name="chk_rag_candidate_index_embedding_dimension",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    index_code: Mapped[str] = mapped_column(String(120), nullable=False)
    index_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[RagCandidateIndexStatus] = mapped_column(
        Enum(RagCandidateIndexStatus, native_enum=False, length=20),
        nullable=False,
        default=RagCandidateIndexStatus.BUILDING,
    )
    build_mode: Mapped[RagCandidateIndexBuildMode] = mapped_column(
        Enum(RagCandidateIndexBuildMode, native_enum=False, length=20), nullable=False
    )
    catalog_set_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_catalog_set.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    catalog_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(100), nullable=False)
    lexical_config_version: Mapped[str] = mapped_column(String(100), nullable=False)
    search_order_version: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    display_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_metric: Mapped[str | None] = mapped_column(String(20), nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    product_identity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_alias_count: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False)
    member_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    members: Mapped[list["RagCandidateIndexMember"]] = relationship(back_populates="candidate_index_version")


class RagCandidateIndexMember(Base):
    """한 Candidate Index Version에 속한 결정적 build 결과 member 한 건.

    Member는 build transaction에서 한 번만 생성되고 이후 update되지 않는다 (RAG-12A의
    ``rag_runtime_bundle_source``와 동일한 create-only 경계).
    """

    __tablename__ = "rag_candidate_index_member"
    __table_args__ = (
        UniqueConstraint("candidate_index_version_id", "member_key", name="uq_rag_candidate_index_member_key"),
        CheckConstraint(
            f"entry_type IN ({_sql_in_list(RagMedicationSearchEntryType)})",
            name="chk_rag_candidate_index_member_entry_type",
        ),
        CheckConstraint(
            f"identity_entity_type IN ({_sql_in_list(RagCandidateIndexEntityType)})",
            name="chk_rag_candidate_index_member_identity_entity_type",
        ),
        CheckConstraint(
            "length(trim(identity_code_system)) > 0", name="chk_rag_candidate_index_member_identity_code_system"
        ),
        CheckConstraint(
            "length(trim(identity_canonical_code)) > 0",
            name="chk_rag_candidate_index_member_identity_canonical_code",
        ),
        CheckConstraint("length(trim(product_ref)) > 0", name="chk_rag_candidate_index_member_product_ref"),
        CheckConstraint("length(trim(entry_ref)) > 0", name="chk_rag_candidate_index_member_entry_ref"),
        CheckConstraint("length(trim(display_text)) > 0", name="chk_rag_candidate_index_member_display_text"),
        CheckConstraint("length(trim(normalized_text)) > 0", name="chk_rag_candidate_index_member_normalized_text"),
        CheckConstraint("length(trim(product_name)) > 0", name="chk_rag_candidate_index_member_product_name"),
        CheckConstraint("length(trim(catalog_version)) > 0", name="chk_rag_candidate_index_member_catalog_version"),
        CheckConstraint(
            "catalog_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_catalog_manifest_hash"
        ),
        CheckConstraint(
            "length(trim(normalization_version)) > 0",
            name="chk_rag_candidate_index_member_normalization_version",
        ),
        CheckConstraint("length(trim(member_key)) > 0", name="chk_rag_candidate_index_member_key_nonblank"),
        CheckConstraint("member_content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_content_hash"),
        # alias_ref와 alias_source_snapshot_id는 함께 있거나 함께 없어야 한다 (APPROVED_ALIAS만 alias를 가짐).
        CheckConstraint(
            "(alias_ref IS NULL) = (alias_source_snapshot_id IS NULL)",
            name="chk_rag_candidate_index_member_alias_pair",
        ),
        CheckConstraint(
            "embedding IS NULL OR vector_dims(embedding) BETWEEN 1 AND 2000",
            name="chk_rag_candidate_index_member_embedding_dimension",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    candidate_index_version_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_candidate_index_version.id", ondelete="RESTRICT"), nullable=False
    )
    entry_type: Mapped[RagMedicationSearchEntryType] = mapped_column(
        Enum(RagMedicationSearchEntryType, native_enum=False, length=20), nullable=False
    )
    identity_entity_type: Mapped[RagCandidateIndexEntityType] = mapped_column(
        Enum(RagCandidateIndexEntityType, native_enum=False, length=20), nullable=False
    )
    identity_code_system: Mapped[str] = mapped_column(String(100), nullable=False)
    identity_canonical_code: Mapped[str] = mapped_column(String(200), nullable=False)
    product_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    entry_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    strength_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manufacturer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_source_snapshot_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    entry_source_snapshot_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    alias_source_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"), nullable=True
    )
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    catalog_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(100), nullable=False)
    member_key: Mapped[str] = mapped_column(String(300), nullable=False)
    member_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_storage_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    candidate_index_version: Mapped[RagCandidateIndexVersion] = relationship(back_populates="members")
