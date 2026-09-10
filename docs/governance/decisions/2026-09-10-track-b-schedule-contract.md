# Product Decision 제안: Track B 일정 조회·Audit·revision 정합화

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-417-20260910` |
| 상태 | **Proposed · 도메인 승인 대기 · Not implemented** |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend·DB·Security; 남한솔 (`solia142`) — 일정 상태·Frontend 소비 |
| 배정 근거 | [#417](https://github.com/AI-HealthCare-05/AH_05_04/issues/417)의 2026-09-10 사용자 지정 |
| 계약 | [일정 정합화 제안 v1](../../contracts/proposed/track-b-schedule-reconciliation-v1.md) |
| 구현 후속 | [#423](https://github.com/AI-HealthCare-05/AH_05_04/issues/423) DB·B2 보완 → [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202) API, [#203](https://github.com/AI-HealthCare-05/AH_05_04/issues/203) 알림 연동 |

## 문제와 선택안

Approved v4가 요구한 일정 감사·time retire와 B1 저장 구조가 다르고, B2의 종료는 status만 변경한다.
#202는 신규 migration을 제외하므로 API에서 audit을 생략하거나 B1을 임의 확장해서는 안 된다.
또한 명시적 사용자 확인을 아직 받지 않은 완전한 입력을 기존 네 가지 missing reason만으로 표현할 수 없다.

다음은 **승인 요청안**이며 현재 코드의 의미나 Approved v4를 즉시 대체하지 않는다.

| 결정 | 제안 | 대안과 선택 이유 |
| --- | --- | --- |
| D1 | 다섯 setup reason·단일 우선순위와 과거 pending 보존 시 INACTIVE 분기를 제안 계약 §2에 정의 | 모든 미설정을 `MISSING_START_DATE`로 치환하면 실제 부족값을 오표시한다. 확인 누락은 별도 표현한다. |
| D2 | append-only schedule audit 추가; 기존 time row의 retire는 revision·schedule status에서 파생 | time status 컬럼 추가는 같은 정보를 이중 저장한다. 이력 보존 의미는 유지하되 원본의 물리 저장 요구 변경으로 명시적 승인을 받는다. |
| D3 | 생성 0→1, 실제 일정 mutation 및 종료 n→n+1; 동일 키 재전송은 증가 없음 | 현재 종료의 status-only 변경은 클라이언트 revision과 감사 연결이 끊긴다. |
| D4 | Prescription부터 잠그고 일정·occurrence·미전달 알림·멱등 snapshot을 단일 transaction으로 처리 | 사후 알림 취소/독립 commit은 취소된 occurrence의 알림이 노출될 수 있다. |
| D5 | DB와 B2 보완은 #423, API는 #202, 알림 adapter는 #203 | #202·#203의 Schedule migration 제외 범위를 유지하고 후속 구현 담당을 명시한다. |

구조 비용은 audit 테이블·migration·snapshot 검증과 종료 write 보완이다. 감사 보존은 기존 승인 목표의
요구이므로 도입 이유가 있고, 새 event bus·DB trigger·외부 알림 채널은 필요하지 않다.

## 근거와 provenance

코드 대조 기준은 `develop` commit `ebcb21ea04ce7bd5f43d4c3bd7f0629ef70e438a`다.
#199/PR #317, #200/PR #394, #201/PR #402의 병합된 모델·서비스·migration·테스트를 대조했다.
#202/PR #413 및 #203의 진행 중 구현은 현재 runtime 증거로 사용하지 않는다.

원본은 별도 `FinalProject Documents/04_Decision/`의 로컬 artifact다. 저장소에 없는 공개 URL은 만들지 않는다.
아래 열람 해시는 [2026-08-27 승인 기록](../post-mvp-1-document-authority.md)의 해시와 다르다.
따라서 현재 열람본 전체가 당시 승인됐다고 간주하지 않으며, 겹치는 내용은 저장소 target과 교차 확인하고
차이는 이 Decision의 승인 대상으로 남긴다. Freeze 파일명은 v1이지만 본문 표제는 Approved v4다.

| 원본 | 2026-09-10 열람 SHA-256 | 참조 절 |
| --- | --- | --- |
| `contract-freeze-v1.md` | `b1c606ed853000ab96632502ae46f37d45a2e7a278023dd5b18aa813a4b9c34a` | §7, §14 |
| `track-b-adherence-v1.md` | `d31d3919c29405b2200a8d7fd6147b4adbed054acd8ffb0a0ca0436f2fd24deb` | §2, §3.1–3.2, §4 |

## 승인과 인계

| 책임 리뷰어 | 요청 범위 | 승인 증빙 |
| --- | --- | --- |
| 송은영 | D1–D5, audit 필드·보존·migration, 멱등성과 잠금·rollback | **대기** — review URL/ID·대상 commit·시각 미수집 |
| 남한솔 | D1·D3·D4, 단일 reason, INACTIVE aggregate delta, 취소/재활성화/종료와 과거 기록 표시 | **대기** — review URL/ID·대상 commit·시각 미수집 |

두 리뷰어의 검토·승인과 blocking comment 해소 후 승인 대상 commit 및 review 증빙을 연결하고 상태를
갱신한다. 원본 해시 차이의 적용 범위도 이때 확인한다. Proposed 계약은 한 파일만 유지하고 승인 시
`targets/post-mvp-1/`로 이동해 인덱스·관련 참조와 기존 target의 delta 안내를 함께 갱신한다.
실제 구현·migration·OpenAPI/DTO·계약/통합 테스트·담당 리뷰어 승인 증빙이 갖춰진 구현 PR에서만
`current/` 승격을 판단한다. 문서 작성이나 PR 생성으로 #417의 승인 완료 조건을 체크하지 않는다.

#202·#203은 이 Decision의 승인 결과를 소비한다. 다른 브랜치에서 같은 계약 파일의 값을 별도로 확정하지
않는다. #202 Check-in API와 #203 저장·조회·읽음 중 이미 명확한 범위는 병행할 수 있다.
공통 Privacy Production gate와 Track C/F 공개 gate는 기존 조건을 유지한다.
