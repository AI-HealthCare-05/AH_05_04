# Post-MVP-1 문서 권위와 상태

| 항목 | 값 |
| --- | --- |
| 상태 | Approved target governance — 2026-08-24 동기화 |
| 구현 | Not implemented |
| 적용 범위 | Post-MVP-1 계약·아키텍처·테스트·공개 게이트 |

## 상태 해석

| 상태 | 의미 | 현재 동작 판단 근거 |
| --- | --- | --- |
| Current | 현재 실행·배포 가능한 계약 | 병합된 코드, migration, 현재 OpenAPI/DTO, 자동 테스트 |
| Approved target — Not implemented | 승인됐지만 아직 구현·검증되지 않은 목표 | 아래 승인 원본과 저장소 목표 계약. 현재 runtime으로 해석하지 않음 |
| Proposed | 검토 중인 제안 | 구현 또는 승인 근거로 사용할 수 없음 |
| Publication gate | 구현과 별개인 외부 승인·공개 조건 | 승인 등록부와 재현 가능한 증빙 |

문서 승인, 구현 완료, 자동 테스트 통과, 외부 승인과 사용자 공개는 서로 다른 상태다. `docs/contracts/targets/post-mvp-1/`의 목표 문서는 관련 영역의 지정 리뷰어가 승인한 뒤 구현 시 따라야 할 normative target이지만, 구현 PR이 병합되기 전에는 현재 API·DB 동작이 아니다. 제품·기획 문서는 의도와 우선순위를 설명하며 runtime 계약을 대체하지 않는다.

## 승인 원본과 provenance

아래 SHA-256은 2026-08-24 로컬 승인 원본을 동기화할 때 계산했다. 원본의 공개 가능한 연결점은 [Post-MVP-1 착수 전 게이트](https://app.notion.com/p/3c3233603e2780a7bcc2ff86de5abb74)와 [Post-MVP-1 세부 결정사항](https://app.notion.com/p/3d7d841cc6c0444399e6e20037a2fd5d)이다. 공개 링크가 없는 로컬 artifact에는 링크를 임의 생성하지 않는다.

| 원본 (`FinalProject Documents/`) | SHA-256 |
| --- | --- |
| `00_Index.md` | `8a00065090c1b2036ff7bfea54d1fed7d88761d4af8ec60d9e3befabf5a82dba` |
| `04_Decision/contract-freeze-v1.md` | `2df0d5ec5781939e10159091d8904ec71953b5d6cfab774334e3d47cbeabec1e` |
| `04_Decision/track-a-async-foundation-v1.md` | `f0be17c3ab08f6aed64aec5d79c397ac0221e0b29da64a85723e53cb74df027a` |
| `04_Decision/track-b-adherence-v1.md` | `f39ba29f6ebffe992deed38f5040bff64748ab67103151258f2e85c408a8a6be` |
| `04_Decision/track-c-support-v1.md` | `fbc570d1e5efe60a079d040e025784159455abd64dd6b8be266ae71362b28cc9` |
| `04_Decision/track-d-otc-v1.md` | `8be062fac354710593d48dfb5ba166081d3eea1e43b6a43340aae3b068c6f742` |
| `04_Decision/track-e-ocr-regression-v1.md` | `218bff1813281d7c612d933b42b76bf0724691472a68c5f01e9fa99c90ae00bf` |
| `04_Decision/track-f-rag-citation-safety-v1.md` | `0aedd599d4a2ab9e791291b5b264e104f871ed904f35b898869fec572b0b19fe` |
| `04_Decision/external-approval-register-v1.md` | `9935518b53eb9796082f61c060fd2998504674f01cbe6338713bc0444b058877` |
| `05_Architecture/System_Architecture_v2.md` | `f70313954a32547da2b4dbc46dedab27823130fdfe3c2f91bd4833493f9cdbff` |
| `03_Planning/Post-MVP 구현 완료 기준.md` | `54845198ee146490f6b173fccb222140165d5767c9b17edb6a0ff7956c5be4e1` |
| `02_Requirements/요구사항_정의서.xlsx` | `e49216739505aa6c5d22b7ea2d3d116d36ab1f2e2fdd759b26c79bb5aac54fba` |

## 충돌과 승격 규칙

1. 원본과 저장소 목표 계약이 다르면 구현을 중단하고 차이를 기록한다. 값을 추정하거나 두 계약을 혼합하지 않는다.
2. 계약 변경은 Decision 또는 Contract Freeze version, 관련 요구사항 ID, API·migration·테스트와 함께 관련 영역의 지정 리뷰어 검토를 받는다.
3. 목표 계약을 Current로 승격하려면 동일 경로에서 구현 PR, migration, OpenAPI/DTO, 계약·통합 테스트와 실행 증빙을 연결하고 상태를 갱신한다. 폴더 이동으로 상태를 표현하지 않는다.
4. 외부 승인과 공개 flag는 구현 완료와 별도로 관리한다. 상세 조건은 [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)를 따른다.
