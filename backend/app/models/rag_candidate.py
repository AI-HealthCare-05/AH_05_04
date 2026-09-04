from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class MedicationCandidateSearchStatus(StrEnum):
    RUNNING = "RUNNING"
    READY = "READY"
    AMBIGUOUS = "AMBIGUOUS"
    NO_CANDIDATE = "NO_CANDIDATE"
    INGREDIENT_ONLY = "INGREDIENT_ONLY"
    INVALID_INPUT = "INVALID_INPUT"
    INVALIDATED_INPUT_CHANGED = "INVALIDATED_INPUT_CHANGED"
    INVALIDATED_USER_REJECTED = "INVALIDATED_USER_REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CONSUMED = "CONSUMED"


class MedicationIdentificationStatus(StrEnum):
    MATCHED = "MATCHED"
    UNRESOLVED = "UNRESOLVED"


class MedicationIdentificationSource(StrEnum):
    USER_SELECTED = "USER_SELECTED"
    USER_REJECTED = "USER_REJECTED"


_STATUS_REASON_VALUES = (
    "INVALID_INPUT",
    "MISSING_STRENGTH_MULTIPLE_VARIANTS",
    "ATTRIBUTE_CONFLICT",
    "IDENTIFIER_ATTRIBUTE_CONFLICT",
    "PRODUCT_NAME_REQUIRED",
)

_IDENTIFICATION_DECISION_REASON_VALUES = ("USER_REJECTED_DISPLAYED_CANDIDATE",)


class MedicationCandidateSearch(Base):
    __tablename__ = "medication_candidate_search"
    __table_args__ = (
        Index(
            "uq_medication_candidate_search_active",
            "prescription_version_medication_id",
            unique=True,
            postgresql_where=text("status IN ('RUNNING', 'READY')"),
        ),
        Index(
            "idx_medication_candidate_search_medication_status",
            "prescription_version_medication_id",
            "status",
            "created_at",
            "id",
        ),
        Index("idx_medication_candidate_search_expires", "expires_at", "id"),
        CheckConstraint(
            f"status IN ({_sql_in_list(MedicationCandidateSearchStatus)})",
            name="chk_medication_candidate_search_status",
        ),
        CheckConstraint("candidate_count >= 0", name="chk_medication_candidate_search_candidate_count"),
        CheckConstraint(
            "displayed_candidate_count >= 0",
            name="chk_medication_candidate_search_displayed_count",
        ),
        CheckConstraint(
            "displayed_candidate_count <= candidate_count",
            name="chk_medication_candidate_search_displayed_lte_candidate",
        ),
        CheckConstraint(
            "status <> 'READY' OR (candidate_count >= 1 AND displayed_candidate_count = 1)",
            name="chk_medication_candidate_search_ready_counts",
        ),
        CheckConstraint(
            "status <> 'INGREDIENT_ONLY' OR status_reason = 'PRODUCT_NAME_REQUIRED'",
            name="chk_medication_candidate_search_ingredient_reason",
        ),
        CheckConstraint(
            "status <> 'INVALID_INPUT' OR status_reason = 'INVALID_INPUT'",
            name="chk_medication_candidate_search_invalid_input_reason",
        ),
        CheckConstraint(
            f"status_reason IS NULL OR status_reason IN ({_sql_in_list(_STATUS_REASON_VALUES)})",
            name="chk_medication_candidate_search_status_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    # prescription_version_medication은 #169에서 생성된다. 이 테이블은 그 선행 테이블을
    # 중복 생성하지 않기 위해 우선 값만 저장하고, FK는 #169 이후 별도 migration에서 추가한다.
    prescription_version_medication_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    query_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_release_bundle_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    candidate_index_version_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    status: Mapped[MedicationCandidateSearchStatus] = mapped_column(
        Enum(MedicationCandidateSearchStatus, native_enum=False, length=40),
        nullable=False,
        default=MedicationCandidateSearchStatus.RUNNING,
    )
    status_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    displayed_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supersedes_search_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("medication_candidate_search.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    supersedes_search: Mapped["MedicationCandidateSearch | None"] = relationship(
        remote_side=[id],
    )
    results: Mapped[list["MedicationCandidateSearchResult"]] = relationship(
        back_populates="search",
        order_by=lambda: MedicationCandidateSearchResult.result_rank,
    )
    identifications: Mapped[list["MedicationIdentification"]] = relationship(
        back_populates="candidate_search",
    )


class MedicationCandidateSearchResult(Base):
    __tablename__ = "medication_candidate_search_result"
    __table_args__ = (
        UniqueConstraint("search_id", "result_rank", name="uq_medication_candidate_result_rank"),
        Index(
            "idx_medication_candidate_result_search",
            "search_id",
            "result_rank",
            "id",
        ),
        Index("idx_medication_candidate_result_product", "product_id"),
        Index(
            "uq_medication_candidate_result_displayed",
            "search_id",
            unique=True,
            postgresql_where=text("is_displayed = true"),
        ),
        Index(
            "uq_medication_candidate_result_selectable",
            "search_id",
            unique=True,
            postgresql_where=text("selection_eligible = true"),
        ),
        CheckConstraint("result_rank > 0", name="chk_medication_candidate_result_rank"),
        CheckConstraint(
            "selection_eligible = false OR is_displayed = true",
            name="chk_medication_candidate_result_selectable_displayed",
        ),
        CheckConstraint(
            "is_displayed = false OR (product_id IS NOT NULL AND code_system IS NOT NULL "
            "AND canonical_code IS NOT NULL AND product_name IS NOT NULL AND product_status IS NOT NULL)",
            name="chk_medication_candidate_result_display_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    search_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("medication_candidate_search.id"), nullable=False)
    # 공식 제품 Catalog는 #164의 Source/Catalog slice 또는 #166 이후 생성된다. product_id는 그
    # 테이블이 생기면 FK가 될 편의 포인터일 뿐, 정체성 판단에 쓰지 않는다 — Catalog row는 Source
    # Snapshot을 다시 적재할 때마다 새 UUID로 재생성될 수 있어 product_id만으로는 시간이 지나도
    # "같은 공식 제품"을 재식별할 수 없다(#260 Product Identity 원칙). 실제 정체성은
    # code_system·canonical_code tuple로 보존한다.
    product_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    code_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    canonical_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strength_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    result_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    is_displayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selection_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    search: Mapped[MedicationCandidateSearch] = relationship(back_populates="results")
    identifications: Mapped[list["MedicationIdentification"]] = relationship(
        back_populates="candidate_search_result",
    )


class MedicationIdentification(Base):
    __tablename__ = "medication_identification"
    __table_args__ = (
        Index(
            "idx_medication_identification_medication_created",
            "prescription_version_medication_id",
            "created_at",
            "id",
        ),
        Index(
            "idx_medication_identification_search",
            "candidate_search_id",
            "created_at",
            "id",
        ),
        Index("idx_medication_identification_product", "product_id"),
        Index(
            "uq_medication_identification_matched",
            "prescription_version_medication_id",
            unique=True,
            postgresql_where=text("status = 'MATCHED'"),
        ),
        UniqueConstraint("candidate_search_id", name="uq_medication_identification_search"),
        CheckConstraint(
            f"status IN ({_sql_in_list(MedicationIdentificationStatus)})",
            name="chk_medication_identification_status",
        ),
        CheckConstraint(
            f"source IN ({_sql_in_list(MedicationIdentificationSource)})",
            name="chk_medication_identification_source",
        ),
        CheckConstraint(
            "status <> 'MATCHED' OR (source = 'USER_SELECTED' AND candidate_search_result_id IS NOT NULL "
            "AND product_id IS NOT NULL AND code_system IS NOT NULL AND canonical_code IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name="chk_medication_identification_matched_payload",
        ),
        CheckConstraint(
            "status <> 'UNRESOLVED' OR (source = 'USER_REJECTED' AND candidate_search_result_id IS NOT NULL "
            "AND product_id IS NULL AND code_system IS NULL AND canonical_code IS NULL "
            "AND confirmed_at IS NULL AND rejected_at IS NOT NULL "
            "AND decision_reason = 'USER_REJECTED_DISPLAYED_CANDIDATE')",
            name="chk_medication_identification_unresolved_source",
        ),
        CheckConstraint(
            f"decision_reason IS NULL OR decision_reason IN ({_sql_in_list(_IDENTIFICATION_DECISION_REASON_VALUES)})",
            name="chk_medication_identification_decision_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    # prescription_version_medication은 #169에서 생성된다. Candidate Search와 같은 이유로
    # 우선 값만 저장하고, FK는 선행 테이블 구현 이후 별도 migration에서 추가한다.
    prescription_version_medication_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    candidate_search_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("medication_candidate_search.id"),
        nullable=False,
    )
    candidate_search_result_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("medication_candidate_search_result.id"),
        nullable=True,
    )
    # product_id는 Catalog 테이블이 생기면 FK가 될 편의 포인터일 뿐이다. 이 테이블은
    # append-only라 나중에 값을 보정할 수 없으므로, 재적재 후에도 안정적인 정체성은
    # code_system·canonical_code tuple로 보존한다(#260 Product Identity 원칙).
    product_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    code_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    canonical_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[MedicationIdentificationStatus] = mapped_column(
        Enum(MedicationIdentificationStatus, native_enum=False, length=20),
        nullable=False,
    )
    source: Mapped[MedicationIdentificationSource] = mapped_column(
        Enum(MedicationIdentificationSource, native_enum=False, length=30),
        nullable=False,
    )
    decision_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    candidate_search: Mapped[MedicationCandidateSearch] = relationship(back_populates="identifications")
    candidate_search_result: Mapped[MedicationCandidateSearchResult | None] = relationship(
        back_populates="identifications",
    )
