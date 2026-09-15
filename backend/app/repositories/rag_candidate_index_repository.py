"""RAG-07B(#168) Candidate Index 영속 계층.

이 모듈은 ``ai_worker``를 import하지 않는다. RAG-08(Ranking)·RAG-09(Candidate Search)가
Worker package 없이 이 read port만으로 조회할 수 있어야 하기 때문이다 (build 진입점은
:mod:`app.services.rag_candidate_index_build`가 유일하게 ``ai_worker``를 import하는 지점으로
분리되어 있다).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from array import array
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def _stable_text_sort_key(value: str) -> bytes:
    return unicodedata.normalize("NFC", value).encode("utf-8")


def _identity_key(member: RagCandidateIndexMemberCreate) -> str:
    return f"{member.identity_entity_type.value}:{member.identity_code_system}:{member.identity_canonical_code}"


def _member_sort_key(member: RagCandidateIndexMemberCreate) -> tuple[bytes, bytes, bytes]:
    return (
        _stable_text_sort_key(_identity_key(member)),
        _stable_text_sort_key(member.entry_type.value),
        _stable_text_sort_key(member.entry_ref),
    )


def _canonical_member_order(
    members: tuple[RagCandidateIndexMemberCreate, ...],
) -> tuple[RagCandidateIndexMemberCreate, ...]:
    return tuple(sorted(members, key=_member_sort_key))


def _canonical_embedding_values(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(array("f", values))


def _embedding_storage_hash(values: tuple[float, ...] | list[float]) -> str:
    return _sha256({"embedding": _canonical_embedding_values(tuple(values))})


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

    ``member_set_hash``·``member_count``·``product_identity_count``·``product_name_count``·
    ``approved_alias_count``·``vector_count``·``configuration_hash``·``content_hash``는
    호출자(Service)가 RAG-07A의 순수 로직으로 이미 계산한 값을 그대로 전달한다. 이 중
    ``member_set_hash``와 5개 count는 실제로 함께 전달되는 member 행으로부터 재계산해
    대조한다 (:meth:`RagCandidateIndexRepository.build_index_version` 참고) -- member 하나만
    바뀌어도 반드시 달라지는 값이라 위조나 count 불일치를 저장 전에 잡아낼 수 있다.
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


class CandidateIndexMemberContentHashMismatchError(CandidateIndexBuildError):
    """전달된 member 행의 실제 필드가 claim된 member_content_hash와 다르다."""


class CandidateIndexMemberSetHashMismatchError(CandidateIndexBuildError):
    """전달된 member 행으로부터 재계산한 member_set_hash가 claim된 값과 다르다."""


class CandidateIndexMemberCountMismatchError(CandidateIndexBuildError):
    """전달된 member 행으로부터 재계산한 count가 claim된 값과 다르다."""


class CandidateIndexContentHashConflictError(CandidateIndexBuildError):
    """이미 저장된 content_hash가 다른 내용을 가리킨다."""


class CandidateIndexLifecycleError(CandidateIndexBuildError):
    """Candidate Index lifecycle 전이를 안전하게 수행할 수 없다."""


class CandidateIndexVersionNotFoundError(CandidateIndexLifecycleError):
    """전이 대상 Candidate Index Version이 존재하지 않는다."""


class CandidateIndexVersionNotBuildableError(CandidateIndexLifecycleError):
    """BUILDING 상태의 완성된 Candidate Index Version만 READY/FAILED로 전이할 수 있다."""


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


def _member_content_payload(member: RagCandidateIndexMemberCreate) -> dict[str, object]:
    """RAG-07A의 member_content_hash payload 정의와 같은 필드 집합."""
    return {
        "identity": {
            "entity_type": member.identity_entity_type.value,
            "code_system": member.identity_code_system,
            "canonical_code": member.identity_canonical_code,
        },
        "product_ref": member.product_ref,
        "entry_ref": member.entry_ref,
        "entry_type": member.entry_type.value,
        "display_text": member.display_text,
        "normalized_text": member.normalized_text,
        "alias_ref": member.alias_ref,
        "product_name": member.product_name,
        "strength_text": member.strength_text,
        "dosage_form": member.dosage_form,
        "manufacturer_name": member.manufacturer_name,
        "product_source_snapshot_id": str(member.product_source_snapshot_id),
        "entry_source_snapshot_id": str(member.entry_source_snapshot_id),
        "alias_source_snapshot_id": str(member.alias_source_snapshot_id) if member.alias_source_snapshot_id else None,
        "catalog_version": member.catalog_version,
        "catalog_manifest_hash": member.catalog_manifest_hash,
        "normalization_version": member.normalization_version,
    }


def _recomputed_lexical_member_content_hash(member: RagCandidateIndexMemberCreate) -> str:
    return _sha256(_member_content_payload(member))


def _recomputed_member_content_hash(
    version: RagCandidateIndexVersion | RagCandidateIndexVersionCreate,
    member: RagCandidateIndexMemberCreate,
) -> str:
    lexical_member_content_hash = _recomputed_lexical_member_content_hash(member)
    if version.build_mode is RagCandidateIndexBuildMode.LEXICAL_ONLY:
        return lexical_member_content_hash
    return _sha256(
        {
            "lexical_member_content_hash": lexical_member_content_hash,
            "embedding_model_version": version.embedding_model_version,
            "embedding": member.embedding,
        }
    )


def _recomputed_member_set_hash(members: tuple[RagCandidateIndexMemberCreate, ...]) -> str:
    """Recompute RAG-07A member_set_hash; see candidate_index.py:1051-1053."""
    return _sha256(
        [
            {"member_key": member.member_key, "member_content_hash": member.member_content_hash}
            for member in _canonical_member_order(members)
        ]
    )


def _assert_member_content_hashes_match(
    version: RagCandidateIndexVersion | RagCandidateIndexVersionCreate,
    members: tuple[RagCandidateIndexMemberCreate, ...],
) -> None:
    mismatched = tuple(
        member.member_key
        for member in members
        if _recomputed_member_content_hash(version, member) != member.member_content_hash
    )
    if mismatched:
        raise CandidateIndexMemberContentHashMismatchError(
            "member_content_hash does not match member fields: " + ", ".join(mismatched)
        )


def _recomputed_member_counts(members: tuple[RagCandidateIndexMemberCreate, ...]) -> dict[str, int]:
    """Recompute RAG-07A manifest counts; see candidate_index.py:1062-1066."""
    identity_keys = {(m.identity_entity_type, m.identity_code_system, m.identity_canonical_code) for m in members}
    return {
        "member_count": len(members),
        "product_identity_count": len(identity_keys),
        "product_name_count": sum(1 for m in members if m.entry_type is RagMedicationSearchEntryType.PRODUCT_NAME),
        "approved_alias_count": sum(1 for m in members if m.entry_type is RagMedicationSearchEntryType.APPROVED_ALIAS),
        "vector_count": sum(1 for m in members if m.embedding is not None),
    }


def _assert_member_metadata_matches(
    version: RagCandidateIndexVersion | RagCandidateIndexVersionCreate,
    members: tuple[RagCandidateIndexMemberCreate, ...],
    *,
    verify_member_content_hashes: bool = True,
) -> None:
    """Compare claimed hash/count metadata with values recomputed from member rows.

    This runs on both new build and content_hash reuse paths; otherwise a caller could
    claim an existing content_hash while passing unchecked member rows.
    """
    if verify_member_content_hashes:
        _assert_member_content_hashes_match(version, members)
    recomputed_hash = _recomputed_member_set_hash(members)
    if recomputed_hash != version.member_set_hash:
        raise CandidateIndexMemberSetHashMismatchError(
            f"recomputed member_set_hash {recomputed_hash[:12]} does not match "
            f"claimed value {version.member_set_hash[:12]}."
        )
    recomputed_counts = _recomputed_member_counts(members)
    mismatched = tuple(field for field, expected in recomputed_counts.items() if getattr(version, field) != expected)
    if mismatched:
        raise CandidateIndexMemberCountMismatchError(
            "recomputed member counts do not match claimed values: " + ", ".join(mismatched)
        )


def _member_create_from_persisted(member: RagCandidateIndexMember) -> RagCandidateIndexMemberCreate:
    return RagCandidateIndexMemberCreate(
        entry_type=member.entry_type,
        identity_entity_type=member.identity_entity_type,
        identity_code_system=member.identity_code_system,
        identity_canonical_code=member.identity_canonical_code,
        product_ref=member.product_ref,
        entry_ref=member.entry_ref,
        display_text=member.display_text,
        normalized_text=member.normalized_text,
        product_name=member.product_name,
        product_source_snapshot_id=member.product_source_snapshot_id,
        entry_source_snapshot_id=member.entry_source_snapshot_id,
        catalog_version=member.catalog_version,
        catalog_manifest_hash=member.catalog_manifest_hash,
        normalization_version=member.normalization_version,
        member_key=member.member_key,
        member_content_hash=member.member_content_hash,
        alias_ref=member.alias_ref,
        strength_text=member.strength_text,
        dosage_form=member.dosage_form,
        manufacturer_name=member.manufacturer_name,
        alias_source_snapshot_id=member.alias_source_snapshot_id,
        embedding=tuple(member.embedding) if member.embedding is not None else None,
    )


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
        """RAG-08/RAG-09가 조회할 active Candidate Index Version."""
        result = await self.session.execute(
            select(RagCandidateIndexVersion).where(
                RagCandidateIndexVersion.index_code == index_code,
                RagCandidateIndexVersion.status == RagCandidateIndexStatus.READY,
            )
        )
        return result.scalar_one_or_none()

    async def activate_ready_version(self, candidate_index_version_id: UUID) -> RagCandidateIndexVersion:
        """BUILDING Version을 READY로 전환하고 기존 READY는 RETIRED로 회수한다."""
        version = await self._get_version_for_update(candidate_index_version_id)
        versions = await self._lock_versions_by_code(version.index_code)
        target = next((candidate for candidate in versions if candidate.id == candidate_index_version_id), None)
        if target is None:
            raise CandidateIndexVersionNotFoundError(
                f"Candidate Index Version {candidate_index_version_id}을 찾을 수 없습니다."
            )
        if target.status is not RagCandidateIndexStatus.BUILDING:
            raise CandidateIndexVersionNotBuildableError("BUILDING 상태의 Candidate Index Version만 READY가 됩니다.")
        await self._assert_persisted_members_match(target)

        retired_existing = False
        for candidate in versions:
            if candidate.id != target.id and candidate.status is RagCandidateIndexStatus.READY:
                candidate.status = RagCandidateIndexStatus.RETIRED
                retired_existing = True
        if retired_existing:
            await self.session.flush()

        target.status = RagCandidateIndexStatus.READY
        await self.session.flush()
        return target

    async def mark_failed_version(self, candidate_index_version_id: UUID) -> RagCandidateIndexVersion:
        """BUILDING Version을 FAILED로 닫는다. READY/RETIRED는 실패 상태로 되돌리지 않는다."""
        version = await self._get_version_for_update(candidate_index_version_id)
        if version.status is not RagCandidateIndexStatus.BUILDING:
            raise CandidateIndexVersionNotBuildableError("BUILDING 상태의 Candidate Index Version만 FAILED가 됩니다.")
        version.status = RagCandidateIndexStatus.FAILED
        await self.session.flush()
        return version

    async def _get_version_for_update(self, candidate_index_version_id: UUID) -> RagCandidateIndexVersion:
        result = await self.session.execute(
            select(RagCandidateIndexVersion)
            .where(RagCandidateIndexVersion.id == candidate_index_version_id)
            .with_for_update()
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise CandidateIndexVersionNotFoundError(
                f"Candidate Index Version {candidate_index_version_id}을 찾을 수 없습니다."
            )
        return version

    async def _lock_versions_by_code(self, index_code: str) -> list[RagCandidateIndexVersion]:
        result = await self.session.execute(
            select(RagCandidateIndexVersion)
            .where(RagCandidateIndexVersion.index_code == index_code)
            .order_by(RagCandidateIndexVersion.created_at, RagCandidateIndexVersion.id)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def _assert_persisted_members_match(self, version: RagCandidateIndexVersion) -> None:
        persisted_rows = tuple(await self.list_members(version.id))
        persisted_members = tuple(_member_create_from_persisted(member) for member in persisted_rows)
        if not persisted_members:
            raise CandidateIndexVersionNotBuildableError(
                f"Candidate Index member_count={version.member_count}, but no persisted member rows exist."
            )
        self._assert_persisted_embedding_storage_hashes_match(version, persisted_rows)
        try:
            _assert_member_metadata_matches(
                version,
                persisted_members,
                verify_member_content_hashes=version.build_mode is RagCandidateIndexBuildMode.LEXICAL_ONLY,
            )
        except CandidateIndexBuildError as exc:
            raise CandidateIndexVersionNotBuildableError(
                "Persisted Candidate Index members do not reproduce manifest metadata before READY promotion."
            ) from exc

    def _assert_persisted_embedding_storage_hashes_match(
        self,
        version: RagCandidateIndexVersion,
        persisted_members: tuple[RagCandidateIndexMember, ...],
    ) -> None:
        mismatched: list[str] = []
        for member in persisted_members:
            if version.build_mode is RagCandidateIndexBuildMode.LEXICAL_ONLY:
                if member.embedding is not None or member.embedding_storage_hash is not None:
                    mismatched.append(member.member_key)
                continue
            if member.embedding is None or member.embedding_storage_hash != _embedding_storage_hash(member.embedding):
                mismatched.append(member.member_key)
        if mismatched:
            raise CandidateIndexVersionNotBuildableError(
                "Persisted Candidate Index embedding storage hash does not match DB vector rows: "
                + ", ".join(mismatched)
            )

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

        ``status``는 항상 ``BUILDING``으로 강제된다. ``READY``/``FAILED``/``RETIRED`` 전이는
        #583 lifecycle 메서드가 별도로 수행하며, Runtime 환경 pointer 전환은 이 repository가
        쓰지 않는다. 실패 시 이 트랜잭션 전체가 롤백되어 partial row가 남지 않는다.

        member 검증(``_assert_member_metadata_matches``)은 content_hash 재사용 여부를 정하기
        **전에** 실행된다. 재사용 경로 뒤로 미루면, 이미 저장된 content_hash와 우연히 같은 값을
        주장하면서 실제로는 다른(또는 변조된) member 입력을 넘겨도 검증 없이 기존 row를
        돌려주게 된다.

        동일 ``content_hash``의 진짜 동시 build는 하나의 결과로 수렴한다: 두 독립 transaction이
        모두 "존재하지 않음"을 보고 저장을 시도하면 unique 제약 위반은 loser 쪽에서만 나는데,
        이를 SAVEPOINT로 감싸 잡아내고 winner가 만든 행을 재조회해 멱등 재사용으로 전환한다.
        같은 ``index_code``의 서로 다른 내용은 이 경로를 타지 않고 그대로 fail-closed된다
        (``uq_rag_candidate_index_building_per_code`` 위반은 content_hash 재조회로 해소되지
        않으므로 예외가 그대로 전파된다).

        Raises:
            CandidateIndexEmptyMemberSetError: member가 0개다.
            CandidateIndexMemberSetHashMismatchError: 전달된 member 행이 claim된
                ``member_set_hash``를 재현하지 못한다.
            CandidateIndexMemberCountMismatchError: 전달된 member 행으로부터 재계산한 count가
                claim된 값과 다르다.
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
        _assert_member_metadata_matches(version, members)

        existing = await self.get_version_by_content_hash(version.content_hash)
        if existing is not None:
            _assert_version_matches(existing, version)
            existing_members = await self.list_members(existing.id)
            return RagCandidateIndexBuildResult(version=existing, members=tuple(existing_members), reused_existing=True)

        await self._assert_catalog_set_matches(version)

        try:
            async with self.session.begin_nested():
                created_version = RagCandidateIndexVersion(**asdict(version), status=RagCandidateIndexStatus.BUILDING)
                self.session.add(created_version)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_version_by_content_hash(version.content_hash)
            if existing is None:
                raise
            _assert_version_matches(existing, version)
            existing_members = await self.list_members(existing.id)
            return RagCandidateIndexBuildResult(version=existing, members=tuple(existing_members), reused_existing=True)

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
        values = asdict(payload)
        if payload.embedding is not None:
            values["embedding"] = list(payload.embedding)
            values["embedding_storage_hash"] = _embedding_storage_hash(payload.embedding)
        member = RagCandidateIndexMember(**values, candidate_index_version_id=candidate_index_version_id)
        self.session.add(member)
        await self.session.flush()
        return member
