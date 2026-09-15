"""RAG-07B(#168) Candidate Index 영속 계층.

이 모듈은 ``ai_worker``를 import하지 않는다. RAG-08(Ranking)·RAG-09(Candidate Search)가
Worker package 없이 이 read port만으로 조회할 수 있어야 하기 때문이다 (build 진입점은
:mod:`app.services.rag_candidate_index_build`가 유일하게 ``ai_worker``를 import하는 지점으로
분리되어 있다).
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_candidate_index import (
    RagCandidateIndexBuildMode,
    RagCandidateIndexEntityType,
    RagCandidateIndexMember,
    RagCandidateIndexStatus,
    RagCandidateIndexVersion,
)
from app.models.rag_catalog import RagCatalogSet, RagMedicationSearchEntryType


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class RagCandidateIndexMemberCreate:
    entry_type: RagMedicationSearchEntryType
    identity_entity_type: RagCandidateIndexEntityType
    identity_code_system: str
    identity_canonical_code: str
    product_ref: str
    entry_ref: str
    display_text: str
    normalized_text: str
    product_name: str
    product_source_snapshot_id: UUID
    entry_source_snapshot_id: UUID
    catalog_version: str
    catalog_manifest_hash: str
    normalization_version: str
    member_key: str
    member_content_hash: str
    alias_ref: str | None = None
    strength_text: str | None = None
    dosage_form: str | None = None
    manufacturer_name: str | None = None
    alias_source_snapshot_id: UUID | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class RagCandidateIndexVersionCreate:
    """RAG-07A ``CandidateIndexManifest``를 저장 가능한 컬럼 집합으로 투영한 값.

    ``member_set_hash``·``configuration_hash``·``content_hash``는 호출자(Service)가 RAG-07A의
    순수 로직으로 이미 계산한 값을 그대로 전달한다. 이 중 ``member_set_hash``만은 실제로 함께
    전달되는 member 행으로부터 재계산해 대조한다 (:meth:`RagCandidateIndexRepository.build_index_version`
    참고) -- member 하나라도 바뀌면 반드시 달라지는 값이라 위조를 가장 저렴하게 잡아낼 수 있다.
    """

    index_code: str
    index_version: str
    build_mode: RagCandidateIndexBuildMode
    catalog_set_id: UUID
    catalog_version: str
    catalog_manifest_hash: str
    schema_version: str
    normalization_version: str
    lexical_config_version: str
    search_order_version: str
    candidate_limit: int
    display_limit: int
    member_count: int
    product_identity_count: int
    product_name_count: int
    approved_alias_count: int
    vector_count: int
    member_set_hash: str
    configuration_hash: str
    content_hash: str
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    embedding_dimension: int | None = None
    distance_metric: str | None = None


class CandidateIndexBuildError(RuntimeError):
    """The requested Candidate Index write cannot produce a verifiable persisted version."""


class CandidateIndexEmptyMemberSetError(CandidateIndexBuildError):
    """member가 없는 Candidate Index Version은 저장할 수 없다."""


class CandidateIndexCatalogMismatchError(CandidateIndexBuildError):
    """catalog_set_id가 가리키는 Catalog Set과 build 입력이 서로 다른 내용을 주장한다."""


class CandidateIndexMemberSetHashMismatchError(CandidateIndexBuildError):
    """전달된 member 행으로부터 재계산한 member_set_hash가 claim된 값과 다르다."""


class CandidateIndexContentHashConflictError(CandidateIndexBuildError):
    """이미 저장된 content_hash가 다른 내용을 가리킨다."""


_VERSION_IDENTITY_FIELDS = (
    "index_code",
    "index_version",
    "build_mode",
    # catalog_set_id는 의도적으로 제외한다: 동일 envelope_hash를 가리키는 새 RagCatalogSet 행이
    # 다시 생겨도(예: 재승인) 이 조합은 같은 build 결과로 재사용되어야 하며, catalog_manifest_hash
    # 등 아래 내용 필드가 이미 그 동일성을 검증한다. FK 대리키까지 같아야 한다고 요구하면 내용은
    # 같은데 참조만 다른 정상적인 재사용을 거짓으로 충돌 처리하게 된다.
    "catalog_version",
    "catalog_manifest_hash",
    "schema_version",
    "normalization_version",
    "lexical_config_version",
    "search_order_version",
    "candidate_limit",
    "display_limit",
    "member_count",
    "product_identity_count",
    "product_name_count",
    "approved_alias_count",
    "vector_count",
    "member_set_hash",
    "configuration_hash",
    "embedding_provider",
    "embedding_model",
    "embedding_model_version",
    "embedding_dimension",
    "distance_metric",
)


def _recomputed_member_set_hash(members: tuple[RagCandidateIndexMemberCreate, ...]) -> str:
    """RAG-07A의 ``member_set_hash`` 정의(``candidate_index.py:1051-1053``)와 동일한 재구현."""
    return _sha256([{"member_key": m.member_key, "member_content_hash": m.member_content_hash} for m in members])


def _assert_version_matches(stored: RagCandidateIndexVersion, requested: RagCandidateIndexVersionCreate) -> None:
    mismatched = tuple(
        field for field in _VERSION_IDENTITY_FIELDS if getattr(stored, field) != getattr(requested, field)
    )
    if mismatched:
        raise CandidateIndexContentHashConflictError(
            f"content_hash {requested.content_hash}이(가) 이미 다른 {', '.join(mismatched)}(으)로 저장되어 있습니다."
        )


@dataclass(frozen=True, slots=True)
class RagCandidateIndexBuildResult:
    """한 번의 #168 build transaction이 저장(또는 재사용)한 결과."""

    version: RagCandidateIndexVersion
    members: tuple[RagCandidateIndexMember, ...]
    reused_existing: bool


class RagCandidateIndexRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_version_by_content_hash(self, content_hash: str) -> RagCandidateIndexVersion | None:
        result = await self.session.execute(
            select(RagCandidateIndexVersion).where(RagCandidateIndexVersion.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def get_version_by_code_and_version(
        self, *, index_code: str, index_version: str
    ) -> RagCandidateIndexVersion | None:
        result = await self.session.execute(
            select(RagCandidateIndexVersion).where(
                RagCandidateIndexVersion.index_code == index_code,
                RagCandidateIndexVersion.index_version == index_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_ready_version_by_code(self, index_code: str) -> RagCandidateIndexVersion | None:
        """RAG-08/RAG-09가 조회할 대상. #168 자신은 READY를 쓰지 않는다 (RAG-17/#180의 몫)."""
        result = await self.session.execute(
            select(RagCandidateIndexVersion).where(
                RagCandidateIndexVersion.index_code == index_code,
                RagCandidateIndexVersion.status == RagCandidateIndexStatus.READY,
            )
        )
        return result.scalar_one_or_none()

    async def list_members(self, candidate_index_version_id: UUID) -> list[RagCandidateIndexMember]:
        result = await self.session.execute(
            select(RagCandidateIndexMember)
            .where(RagCandidateIndexMember.candidate_index_version_id == candidate_index_version_id)
            .order_by(RagCandidateIndexMember.created_at, RagCandidateIndexMember.id)
        )
        return list(result.scalars().all())

    async def get_member_by_key(
        self, *, candidate_index_version_id: UUID, member_key: str
    ) -> RagCandidateIndexMember | None:
        result = await self.session.execute(
            select(RagCandidateIndexMember).where(
                RagCandidateIndexMember.candidate_index_version_id == candidate_index_version_id,
                RagCandidateIndexMember.member_key == member_key,
            )
        )
        return result.scalar_one_or_none()

    async def build_index_version(
        self,
        *,
        version: RagCandidateIndexVersionCreate,
        members: tuple[RagCandidateIndexMemberCreate, ...],
    ) -> RagCandidateIndexBuildResult:
        """RAG-07A(#167)가 계산한 build 결과 하나를 ``BUILDING``으로 저장(또는 멱등 재사용)한다.

        ``status``는 항상 ``BUILDING``으로 강제된다. ``READY``/``RETIRED``와 환경 pointer 전환은
        RAG-17(#180)의 몫이므로 이 메서드는 그것들을 쓰지 않는다 (RAG-12A
        ``build_runtime_bundle``과 동일한 경계). 실패 시 이 트랜잭션 전체가 롤백되어 partial row가
        남지 않는다.

        Raises:
            CandidateIndexEmptyMemberSetError: member가 0개다.
            CandidateIndexMemberSetHashMismatchError: 전달된 member 행이 claim된
                ``member_set_hash``를 재현하지 못한다.
            CandidateIndexCatalogMismatchError: ``catalog_set_id``가 가리키는 Catalog Set과
                내용이 다르다.
            CandidateIndexContentHashConflictError: 같은 ``content_hash``가 다른 내용으로 이미
                저장되어 있다.
        """
        if not members:
            raise CandidateIndexEmptyMemberSetError(
                "member 없는 Candidate Index Version은 저장할 수 없습니다. member_set_hash가 빈 "
                "member set을 가리키면 검색 대상 동일성을 재검증할 수 없습니다."
            )

        existing = await self.get_version_by_content_hash(version.content_hash)
        if existing is not None:
            _assert_version_matches(existing, version)
            existing_members = await self.list_members(existing.id)
            return RagCandidateIndexBuildResult(version=existing, members=tuple(existing_members), reused_existing=True)

        recomputed_member_set_hash = _recomputed_member_set_hash(members)
        if recomputed_member_set_hash != version.member_set_hash:
            raise CandidateIndexMemberSetHashMismatchError(
                f"전달된 member로 재계산한 member_set_hash {recomputed_member_set_hash[:12]}…이(가) "
                f"claim된 값 {version.member_set_hash[:12]}…과 다릅니다."
            )
        await self._assert_catalog_set_matches(version)

        created_version = RagCandidateIndexVersion(**asdict(version), status=RagCandidateIndexStatus.BUILDING)
        self.session.add(created_version)
        await self.session.flush()

        created_members = tuple(
            [await self._create_member(payload, candidate_index_version_id=created_version.id) for payload in members]
        )
        return RagCandidateIndexBuildResult(version=created_version, members=created_members, reused_existing=False)

    async def _assert_catalog_set_matches(self, version: RagCandidateIndexVersionCreate) -> None:
        catalog_set = await self.session.get(RagCatalogSet, version.catalog_set_id)
        if catalog_set is None:
            raise CandidateIndexCatalogMismatchError(f"catalog_set_id {version.catalog_set_id}가 존재하지 않습니다.")
        mismatched = tuple(
            field
            for field, expected in (
                ("catalog_version", catalog_set.catalog_version),
                ("catalog_manifest_hash", catalog_set.envelope_hash),
                ("schema_version", catalog_set.schema_version),
                ("normalization_version", catalog_set.normalization_version),
            )
            if getattr(version, field) != expected
        )
        if mismatched:
            raise CandidateIndexCatalogMismatchError(
                f"catalog_set_id {version.catalog_set_id}의 값과 다릅니다: {', '.join(mismatched)}"
            )

    async def _create_member(
        self, payload: RagCandidateIndexMemberCreate, *, candidate_index_version_id: UUID
    ) -> RagCandidateIndexMember:
        """Private on purpose: member는 오직 :meth:`build_index_version`을 통해서만 쓰여진다.

        공개 member-insert가 있으면 이미 build된 Version에 member를 덧붙일 수 있게 되어,
        ``member_set_hash``가 식별해야 할 member-set 불변성이 깨진다.
        """
        member = RagCandidateIndexMember(**asdict(payload), candidate_index_version_id=candidate_index_version_id)
        self.session.add(member)
        await self.session.flush()
        return member
