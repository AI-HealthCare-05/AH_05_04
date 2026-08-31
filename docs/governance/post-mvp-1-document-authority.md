# Post-MVP-1 문서 권위와 상태

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Contract Freeze v4 target governance — 2026-08-27 동기화 |
| 구현 | Not implemented |
| 적용 범위 | Post-MVP-1 계약·아키텍처·테스트·공개 게이트 |

## 상태 해석

| 상태 | 의미 | 현재 동작 판단 근거 |
| --- | --- | --- |
| Current | 현재 실행·배포 가능한 계약 | 병합된 코드, migration, 현재 OpenAPI/DTO, 자동 테스트 |
| Approved target — Not implemented | 승인됐지만 아직 구현·검증되지 않은 목표 | 아래 승인 원본과 저장소 목표 계약. 현재 runtime으로 해석하지 않음 |
| Proposed | 검토 중인 제안 | 구현 또는 승인 근거로 사용할 수 없음 |
| Publication gate | 구현과 별개인 외부 승인·공개 조건 | 승인 등록부와 재현 가능한 증빙 |

문서 승인, 구현 완료, 자동 테스트 통과, 외부 승인과 사용자 공개는 서로 다른 상태다. Approved v4는 현재 normative target이지만, 구현 PR이 병합되기 전에는 현재 API·DB 동작이 아니다. PostgreSQL 플랫폼 전환 완료도 RAG/Eval schema, OpenAPI, 기능·계약 테스트나 Evaluation Run 완료를 뜻하지 않는다. 제품·기획 문서는 의도와 우선순위를 설명하며 runtime 계약을 대체하지 않는다.

## 승인 원본과 provenance

아래 SHA-256은 2026-08-27 Approved v4 로컬 원본을 동기화할 때 계산했다. 원본의 공개 가능한 연결점은 [Post-MVP-1 착수 전 게이트](https://app.notion.com/p/3c3233603e2780a7bcc2ff86de5abb74)와 [Post-MVP-1 세부 결정사항](https://app.notion.com/p/3d7d841cc6c0444399e6e20037a2fd5d)이다. 공개 링크가 없는 로컬 artifact에는 링크를 임의 생성하지 않는다.

| 원본 (`FinalProject Documents/`) | SHA-256 |
| --- | --- |
| `00_Index.md` | `868a9f3ba0ab7465133221ea650f6fddae46f301e9c98ae0744e818e59482c04` |
| `01_Product/User_Flow_v5.md` | `95d221d95459d11e92a98669fbc193b61fede848b04584056de18d8cd40695c5` |
| `01_Product/프로젝트_개요.md` | `59b9cdd3995c011a068ede24c74b9d7d2399f6ba6b0d940e4dd4440b77adf297` |
| `02_Requirements/요구사항_정의서.xlsx` | `7c7f2fe2bf51ba517426d27a1d3ef25123ac88529e047d52fa5b5bceab241754` |
| `03_Planning/Post-MVP 구현 계획.md` | `eb71b1459419a4d9314407198d95373eace06a8d3fd2a5ae7adad60110b83782` |
| `03_Planning/Post-MVP 구현 완료 기준.md` | `5ea6fb87d314be18a6872cfcba1158dac74fed0f164c2c635878d4d9e120e79c` |
| `04_Decision/contract-freeze-v1.md` | `023de67c42f3ed2c31b7ef5b6343e0fd737e2d28e22a0da663ae5c6ff38ffbe8` |
| `04_Decision/track-a-async-foundation-v1.md` | `31811895013ef63d729462d9c16f2aeb5dbc8d42627cf0c161a095e48fcd082c` |
| `04_Decision/track-b-adherence-v1.md` | `e8324c74ac6a6c514dd18a9195be84716f5b618574303f8eef9bd812a30563f7` |
| `04_Decision/track-c-support-v1.md` | `ae3dabad424ac2536e4930d46eafee5444491afeccb9791a3958ff97654af8b1` |
| `04_Decision/track-d-otc-v1.md` | `1a21e4981e80d9d5bbf3eb87a22678cb61180329dbc6898399ee9f703cef7020` |
| `04_Decision/track-e-ocr-regression-v1.md` | `4ff276a31800c160c2e51f3c0feb033155b1300b417dce1092d644e783f623d6` |
| `04_Decision/track-f-rag-citation-safety-v1.md` | `d2b7104dfafbb8c0f990128535ffbd8eda7b534fb25658360373392283b5c9f4` |
| `04_Decision/external-approval-register-v1.md` | `466d52a7d52751490a8dde705d9d504163dc83df21fe94b228ad98182f1cc8ce` |
| `05_Architecture/System_Architecture_v2.md` | `738072439e5a7b71dffdfbe867de625a9262fd10c242a5f64f0fc6721fc0ac8a` |

## 충돌과 승격 규칙

1. 원본과 저장소 목표 계약이 다르면 구현을 중단하고 차이를 기록한다. 값을 추정하거나 두 계약을 혼합하지 않는다.
2. 계약 변경은 Decision 또는 Contract Freeze version, 관련 요구사항 ID, API·migration·테스트와 함께 관련 영역의 지정 리뷰어 검토를 받는다.
3. 상태 디렉터리를 계약 상태의 기준으로 사용한다. 목표 또는 Proposed 계약을 Current로 승격하려면 구현 PR에서 관련 구현, migration, OpenAPI/DTO, 계약·통합 테스트와 실행 증빙을 연결하고 지정 리뷰어 승인을 받은 뒤 `docs/contracts/current/`로 이동한다. 같은 PR에서 문서 상태와 인덱스를 갱신하고 이전 상태 디렉터리에 중복 파일을 남기지 않는다.
4. 외부 승인과 공개 flag는 구현 완료와 별도로 관리한다. 상세 조건은 [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)를 따른다.

`ISS-TBD-035`는 `FinalProject Documents/00_Index.md`의 상류 계획 레지스터 ID이며, 이 구현 저장소에서는 [Issue #91](https://github.com/AI-HealthCare-05/AH_05_04/issues/91)과 연결해 추적한다. 상류 artifact에 공개 가능한 안정 URL이 생기기 전에는 존재하지 않는 저장소 링크를 만들지 않는다. HIRA 식별, Track D 전용 OTC API 또는 의미 기반 NLI를 Approved v4 목표로 복원하지 않는다.

## 구현 전 재결정이 필요한 충돌

다음 항목은 Approved v4 문구와 현재 실행·배포 경계 사이의 남은 충돌이다. 이 PR은 값을 추정해 해소하지 않으며, [Issue #91](https://github.com/AI-HealthCare-05/AH_05_04/issues/91)에 연결된 후속 Product Decision 또는 새 Contract Freeze version이 승인될 때까지 관련 구현과 `current/` 승격을 차단한다. 2026-08-31 [Product Decision `PD-91-20260831`](./decisions/2026-08-31-ocr-timeout-idempotency.md)로 OCR `hard timeout 60초 / lease 75초`, PostgreSQL `BYTEA`, 단일 `idempotency_record + record_type`을 목표 계약에 확정했으며 더 이상 미정 충돌로 취급하지 않는다.

- **SELF profile 소유권 이관:** 사용자당 `SELF` profile 1개를 보장하는 제약, 기존 의료 데이터의 `profile_id` backfill, FK·index 생성과 read/write cutover 순서, rollback, endpoint별 권한 테스트가 미정이다. 별도 Decision과 migration 계획이 승인될 때까지 기존 `user_id` 소유권 기준을 유지하고 `profile_id`로 읽기·쓰기를 전환하지 않는다.
- **Runtime Bundle과 Worker 배포:** Worker artifact version을 Bundle에 포함하면서 재시도 snapshot 고정, active Bundle 변경 시 `STALE`, 구·신 Worker 동시 배포를 함께 만족시키는 전이가 미정이다. `RETRY_WAIT` 처리, Worker–Bundle 호환성 검사와 drain/rolling deployment 방식을 함께 확정한다.
- **OTC 질문의 안정 Identity:** Chat 자유 입력에서 OTC 제품·성분·함량·제형을 식별하고 애매함·사용자 확인을 거쳐 Rule 입력 Identity로 고정하는 전이가 미정이다. 이 전이가 확정되기 전에는 불충분한 입력으로 Rule 평가를 실행하지 않는다.
