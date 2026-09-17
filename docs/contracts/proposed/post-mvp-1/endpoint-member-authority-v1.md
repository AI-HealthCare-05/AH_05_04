# Endpoint Member Authority Contract Kernel v1

| 항목 | 값 |
| --- | --- |
| 문서 버전 | `endpoint-member-contract-v1` / `source-member-identity-v1` |
| 상태 | Proposed / Review pending · Issue #180 |
| 상위 결정 | [`PD-180-EM-20260916`](../../../governance/decisions/2026-09-16-endpoint-member-authority-contract.md) |
| 구현 위치 | `ai_worker/tasks/rag/source_member_identity.py` |
| 계약 책임 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 교차 리뷰 | 송은영 (`@phina-io`) — DB·Guard / 김지혜 (`@Jye-rookie`) — Source provenance |

---

## 1. 목적 및 권위 한계

본 문서는 RAG 파이프라인에서 소비되는 Source Snapshot Member(Endpoint/Operation 및 Ingestion Artifact)의 식별자 구조, 필수성 규칙, 영속 저장값 매핑, 그리고 관측(observed) member와 검색(selected) member 간의 exact-match 결속 검증 규칙을 규정한다.

### 권위 한계 (Authority Boundary)

1. **순수 계약 커널 범위 (Pure Kernel Scope)**:
   - 본 계약 커널은 영속 계층 I/O, 데이터베이스 쿼리, 네트워크 호출, 로깅이 배제된 순수(pure) 인메모리 유효성 검증만을 수행한다.
2. **구조 일치 검증 vs 외부 권한 인가 (Verification vs Authorization)**:
   - `verify_member_authority_binding` verifier는 두 `SourceMemberIdentity` 인스턴스 간의 구조적 완전성 및 5개 필드의 byte-for-byte exact equality만을 증명한다.
   - 실제 데이터베이스 레코드 존재, 의사결정 소유권(Decision ownership), #174 REQUEST Guard의 실제 `PASS` 판정, 발급자의 진위(issuer authenticity)는 보증하지 않는다.
   - 용어 불변성 원칙에 따라 이 단계의 결과에는 `Authorized` 명칭을 사용할 수 없으며 오직 `Verified` 또는 일치 검증으로만 다룬다.
3. **선행 계약 상태 (Proposed Status)**:
   - 본 계약은 Approved 결정 `PD-315-20260908`(Issue #680 Path B 재판정에 의해 승인 조건 해소)의 nullable operation_code 정책을 반영한 **Proposed** 계약이다. 구현·migration·OpenAPI/DTO·자동 테스트·실행 증빙 및 지정 책임 리뷰어 승인을 갖춘 별도 PR 전까지 `docs/contracts/current/`로 승격하지 않는다.

---

## 2. 데이터 구조 및 필드별 필수성

### 2.1 `SourceMemberIdentity` 구조

```python
@dataclass(frozen=True, slots=True)
class SourceMemberIdentity:
    member_kind: SourceMemberKind
    endpoint_code: str | None = None
    operation_code: str | None = None
    artifact_code: str | None = None
    artifact_version: str | None = None
```

### 2.2 `member_kind`별 필드 조건

| 필드 | 타입 | `ENDPOINT_OPERATION` | `ARTIFACT_MEMBER` | 검증 규칙 |
|---|---|---|---|---|
| `member_kind` | `SourceMemberKind` | 필수 (`ENDPOINT_OPERATION`) | 필수 (`ARTIFACT_MEMBER`) | enum 정확 일치 (`type(x) is SourceMemberKind`) |
| `endpoint_code` | `str \| None` | **필수 (non-null)** | **금지 (`None` 필수)** | 비어있지 않은 NFC 정규화 문자열 (`_is_nonblank_nfc`) |
| `operation_code` | `str \| None` | **선택 (nullable)** | **금지 (`None` 필수)** | `None` 허용; 값이 있을 경우 비어있지 않은 NFC |
| `artifact_code` | `str \| None` | **금지 (`None` 필수)** | **필수 (non-null)** | 비어있지 않은 NFC 정규화 문자열 |
| `artifact_version` | `str \| None` | **금지 (`None` 필수)** | **필수 (non-null)** | 비어있지 않은 NFC 정규화 문자열 |

- 검증 실패 사유는 fail-closed 원칙에 따라 **전부 수집(accumulated)**되어 중복 없이 알파벳 순으로 정렬된 tuple로 반환된다.
- `member_kind` 자체가 유효하지 않은 경우 타 필드 검증을 생략하고 즉시 `(MEMBER_KIND_INVALID,)`만 반환한다.

---

## 3. 사유 코드 명세 (`SourceMemberIdentityReason`)

| 사유 코드 | 발생 조건 |
|---|---|
| `MEMBER_KIND_INVALID` | 대상 객체의 타입이 `SourceMemberIdentity`가 아니거나 `type(member_kind) is not SourceMemberKind`인 경우 (동일 값의 문자열 및 타 StrEnum은 StrEnum 동등 비교 우회를 방지하기 위해 엄격히 거부) |
| `ENDPOINT_CODE_REQUIRED` | `ENDPOINT_OPERATION`에서 `endpoint_code`가 `None`, 빈 문자열, 공백만 있거나 non-NFC인 경우 |
| `OPERATION_CODE_INVALID` | `ENDPOINT_OPERATION`에서 `operation_code`가 `None`이 아니면서 빈 문자열, 공백만 있거나 non-NFC인 경우 |
| `ARTIFACT_FIELDS_FORBIDDEN` | `ENDPOINT_OPERATION`에서 `artifact_code` 또는 `artifact_version`이 `None`이 아닌 경우 |
| `ARTIFACT_CODE_REQUIRED` | `ARTIFACT_MEMBER`에서 `artifact_code`가 `None`, 빈 문자열, 공백만 있거나 non-NFC인 경우 |
| `ARTIFACT_VERSION_REQUIRED` | `ARTIFACT_MEMBER`에서 `artifact_version`가 `None`, 빈 문자열, 공백만 있거나 non-NFC인 경우 |
| `ENDPOINT_FIELDS_FORBIDDEN` | `ARTIFACT_MEMBER`에서 `endpoint_code` 또는 `operation_code`가 `None`이 아닌 경우 |
| `PERSISTED_MEMBER_KIND_UNKNOWN` | DB 영속 wire value를 역변환할 때 알 수 없는 문자열이 전달된 경우 |
| `MEMBER_AUTHORITY_MISMATCH` | `verify_member_authority_binding` 시 observed와 selected 간 5개 필드 중 하나라도 불일치하는 경우 |

---

## 4. DB 영속 저장값 매핑 (F7 해소)

DB 영속 저장 계층의 기존 데이터 안정성을 위해 DB 컬럼 값은 변경하지 않고, 순수 커널 모듈에서 양방향 매핑을 캡슐화한다.

| 순수 커널 enum (`SourceMemberKind`) | DB 컬럼 저장값 (`rag_source_snapshot_member.member_kind`) | `SourceSnapshotMemberKind` (ORM/Adapter) |
|---|---|---|
| `ENDPOINT_OPERATION` | `"ENDPOINT_OPERATION"` | `SourceSnapshotMemberKind.ENDPOINT_OPERATION` |
| `ARTIFACT_MEMBER` | `"ARTIFACT"` | `SourceSnapshotMemberKind.ARTIFACT` |

- `persisted_member_kind_value(kind: SourceMemberKind) -> str`
- `member_kind_from_persisted(value: str) -> SourceMemberKind`
  - 알 수 없는 영속값은 호출자에게 날것의 `ValueError`를 던지지 않고, typed 예외 `SourceMemberIdentityError(PERSISTED_MEMBER_KIND_UNKNOWN)`로 변환한다.
- `try_member_kind_from_persisted(value: str) -> tuple[SourceMemberKind | None, SourceMemberIdentityReason | None]`
  - 예외 없이 안전하게 tuple 형태로 사유를 수신할 수 있는 변형 헬퍼를 함께 제공한다.

---

## 5. Exact-Match 권위 결속 검증 (`verify_member_authority_binding`)

```python
def verify_member_authority_binding(
    *,
    observed: SourceMemberIdentity,
    selected: SourceMemberIdentity,
) -> tuple[SourceMemberIdentityReason, ...]
```

### 규칙
1. `observed`와 `selected` 양쪽을 각각 `validate_source_member_identity`로 독립 검증한다.
2. 두 객체의 5개 필드(`member_kind`, `endpoint_code`, `operation_code`, `artifact_code`, `artifact_version`) 전부가 완벽히 동일한지 비교한다.
3. 양쪽 필드가 일치하지 않으면 `MEMBER_AUTHORITY_MISMATCH`를 사유 목록에 추가한다.
4. 양쪽 객체 자체에서 발생한 유효성 검증 오류 사유가 있다면 mismatch 사유와 함께 결합하여 반환한다.
5. 오직 양쪽이 각각 완전히 유효하고 5개 필드가 100% 일치할 때만 빈 tuple `()`을 반환한다.

---

## 6. 골든 벡터 해시 불변성 보증

`_source_binding_payload`, `_selection_payload`, `_selection_projection`은 위임 후에도 바이트 단위 불변성을 엄격히 유지하며, 아래 10개 기준 해시와 항시 일치해야 한다 (`origin/develop` = `66328a09` 베이스라인):

| 식별자 | 대상 함수 / 연산 | 케이스 | 기준 SHA-256 해시 |
|---|---|---|---|
| H1 | `canonical_claim_support_projection_hash` | ENDPOINT | `c798d7f857f688d84fccb445c03193624721a59b1bc5f6506828164f5ed6ddc0` |
| H1 | `canonical_claim_support_projection_hash` | ARTIFACT | `0976fa757c4cb9b4f5c87b9bed4b001ac2d2e9190ba4c3a6d41903b0614c9d7a` |
| H2 | `canonical_validated_selection_hash` | ENDPOINT | `31a5ec2955e8c36f584f4bd153a7c9350daeafdcc9c393387c30280cf06d7d2a` |
| H2 | `canonical_validated_selection_hash` | ARTIFACT | `e5e54a8146f4e6a2771e9d35730351475361773a2b2df4832b6c8a60dc354b9f` |
| H3 | `_selection_manifest_hash` | ENDPOINT | `0e7ae8cec219ae46a39b486931cb4f99675cdfd3cb18484257a2eec7eba7573e` |
| H3 | `_selection_manifest_hash` | ARTIFACT | `a1c277c295e59b6375452dd7a829d13e57a030b89e3b9bc84dd99439c70ae396` |
| H4 | `build_citation_authorization_request.request_sha256` | ENDPOINT | `b424e2515ae162cefd3ef2b373343307b968ecf22f1094ab42f41d72472b472a` |
| H4 | `build_citation_authorization_request.request_sha256` | ARTIFACT | `74105ba87a0c4e326b803e7ba7f559d71c9b3dfff1a43c6c60ff84f6096317a3` |
| H5 | `compute_guide_evidence_handoff_hash` | ENDPOINT | `db446fd35f7b984be5d0f1085bd1b714792bc24a019dea3225fd1f4371d9a3ee` |
| H5 | `compute_guide_evidence_handoff_hash` | ARTIFACT | `2ffdd800d13d71e93c22b9b30bb2e38fbb367a55aed51460f1ed0c5a00d6a74a` |
