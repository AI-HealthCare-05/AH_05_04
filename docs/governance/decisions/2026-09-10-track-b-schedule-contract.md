# Product Decision: Track B 일정 조회·Audit·revision 정합화

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-417-20260910` |
| 상태 | **Approved target** — DB #438·알림 #430·일정 API #456 병합; Current 승격 별도 |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend·DB·Security; 남한솔 (`solia142`) — 일정 상태·Frontend 소비 |
| 배정 근거 | [#417](https://github.com/AI-HealthCare-05/AH_05_04/issues/417)의 2026-09-10 사용자 지정 |
| 계약 | [일정 정합화 v1](../../contracts/targets/post-mvp-1/track-b-schedule-reconciliation-v1.md) |
| 구현 후속 | [#423](https://github.com/AI-HealthCare-05/AH_05_04/issues/423) DB·B2 보완 → [#202](https://github.com/AI-HealthCare-05/AH_05_04/issues/202) API, [#203](https://github.com/AI-HealthCare-05/AH_05_04/issues/203) 알림 연동 |

## 문제와 선택안

이 절과 아래 provenance 대조표는 Decision 작성 당시의 문제·선택 근거를 보존한다. 현재 병합 현황과 남은 인계는 [계약의 2026-09-12 현황](../../contracts/targets/post-mvp-1/track-b-schedule-reconciliation-v1.md#2026-09-12-구현-현황과-남은-인계)을 따른다.

현재 열람본의 일정 감사·물리 time retire 요구와 B1 저장 구조가 다르고, B2의 종료는 status만 변경한다.
이 세부 요구는 아래 대조표에서 repository target으로 교차 확인된 범위와 구분한다.
#202는 신규 migration을 제외하므로 API에서 audit을 생략하거나 B1을 임의 확장해서는 안 된다.
또한 명시적 사용자 확인을 아직 받지 않은 완전한 입력을 기존 네 가지 missing reason만으로 표현할 수 없다.

다음 D1–D5는 PR #424에서 승인된 delta다. 원본 Freeze 전체의 재승인이나 현재 runtime 구현 완료를 뜻하지 않는다.

| 결정 | 제안 | 대안과 선택 이유 |
| --- | --- | --- |
| D1 | 다섯 setup reason·단일 우선순위와 과거 pending 보존 시 INACTIVE 분기를 제안 계약 §2에 정의 | 모든 미설정을 `MISSING_START_DATE`로 치환하면 실제 부족값을 오표시한다. 확인 누락은 별도 표현한다. |
| D2 | append-only schedule audit 추가; 기존 time row의 retire는 revision·schedule status에서 파생 | time status 컬럼 추가는 같은 정보를 이중 저장한다. 이력 보존 의미는 유지하되 원본의 물리 저장 요구 변경으로 명시적 승인을 받는다. |
| D3 | 생성 0→1, 실제 일정 mutation 및 종료 n→n+1; 동일 키 재전송은 증가 없음 | 현재 종료의 status-only 변경은 클라이언트 revision과 감사 연결이 끊긴다. |
| D4 | Prescription부터 잠그고 일정·occurrence·미전달 알림·멱등 snapshot을 단일 transaction으로 처리 | 사후 알림 취소/독립 commit은 취소된 occurrence의 알림이 노출될 수 있다. |
| D5 | DB와 B2 보완은 #423, API는 #202, 알림 adapter는 #203 | #202·#203의 Schedule migration 제외 범위를 유지하고 후속 구현 담당을 명시한다. |

구조 비용은 audit 테이블·migration·snapshot 검증과 종료 write 보완이다. 일정 감사 저장은 열람본의
요구를 이 Decision에서 명시적으로 승인받기 위한 제안이며, Check-in 감사의 승인으로 대신하지 않는다.
새 event bus·DB trigger·외부 알림 채널은 추가하지 않는다. 명시적 Python 감사 저장·기존 최소 권한 정책 적용과 baseline 보관 매체는 [PD-423 구현 보완](2026-09-10-schedule-audit-storage.md)에서 별도로 명시하며, 이를 PR #424의 승인에 소급 포함하지 않는다.

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

### 원본 주장별 repository target 교차 확인 범위

`target 교차 확인됨`은 아래 링크의 본문에 같은 의미가 있음을 뜻하며 열람본 전체의 승인 증명이 아니다.
`열람본만`은 인용한 repository target에서 해당 세부 요구를 확인할 수 없다는 뜻이다. 승인 원본 전체에
없었다는 뜻도 아니며, 이 항목을 기존 승인 사실로 전제하지 않는다. 부분 일치는 행을 나눠 표시한다.
대조 기준은 위 develop commit이며, 이 PR에서 추가한 Proposed 링크 자체는 교차 확인 근거에서 제외한다.

| 원본 참조 절 | 확인한 주장 | 구분 | repository target의 확인 범위 / 이번 승인 요청 |
| --- | --- | --- | --- |
| Freeze §7·§14, Track B §3.1 | 기존 setup reason 4값·5개 전체 상태·INACTIVE의 pending 없음 조건 | **target 교차 확인됨** | [Check-in v1](../../contracts/targets/post-mvp-1/checkin-v1.md)의 「일정 occurrence와 Check-in 분리」「목표 DTO 요약」. 신규 reason·우선순위와 INACTIVE 조건 변경은 D1 제안이다. |
| Freeze §7, Track B §2 | schedule의 version medication unique, 종료 방식·source·상태·revision; time revision별 보존·unique | **target 교차 확인됨** | 같은 target의 「일정 occurrence와 Check-in 분리」. 이 확인은 물리 time status나 schedule audit까지 포함하지 않는다. |
| Freeze §7, Track B §2·§3.2 | time의 물리 `status=ACTIVE/RETIRED`, 변경 시 retire/create | **열람본만** | Check-in target은 time revision 보존을 요약하지만 물리 status는 규정하지 않는다. D2의 파생 retire 표현을 새로 승인 요청한다. |
| Freeze §7, Track B §2·§3.2 | schedule audit의 전후 날짜·종료 방식·time set·status·revision·actor·시각 | **열람본만** | Check-in target의 「수정과 감사」는 `checkin_audit` 규정이다. schedule audit의 근거로 대체하지 않으며 D2의 저장 필드·형식을 승인 요청한다. |
| Freeze §7, Track B §3.2 | PUT 생성·변경·CANCELLED/ENDED 재활성화, PATCH 사용자 취소, Scheduler만 ENDED 설정 | **target 교차 확인됨** | Check-in target의 「일정 occurrence와 Check-in 분리」「목표 DTO 요약」. 주체·API 입력만 확인되며 아래 revision 증가 규칙과 분리한다. |
| Freeze §7, Track B §3.2 | 최초 expected=0, 일정 변경·취소·종료의 revision 증가와 schedule audit 원자 기록 | **열람본만** | Check-in target은 일정 expected_revision·충돌 오류만 고정한다. Check-in 최초 revision=0 규정을 일정 근거로 쓰지 않는다. D3 전이표는 별도 승인 요청이다. |
| Freeze §7, Track B §2 | occurrence unique `(time_id, scheduled_at)` 및 nullable `cancelled_at` | **열람본만** | Check-in target은 이 물리 제약·컬럼을 명시하지 않는다. 현재 B1 unique 유지와 취소 시각 컬럼 보완은 제안 계약 §1·§3의 승인 대상이다. |
| Freeze §7·§14, Track B §4 | 매일 동일 시각, 사용자 확인, KST·14일 rolling·deadline·과거 보존·새 version 재확인 | **target 교차 확인됨** | Check-in target의 「일정 occurrence와 Check-in 분리」「수정과 감사」. 새 USER audit 시각을 rolling 하한으로 쓰는 규칙은 D4의 신규 제안이다. |
| Freeze §7, Track B §4 | 처방 활성화 UoW의 전역 잠금 순서와 B 동기 취소 port, 미래 pending·미전달 알림의 동일 transaction 취소 | **target 교차 확인됨** | [처방 버전 v1](../../contracts/targets/post-mvp-1/prescription-version-v1.md)의 전역 lock 순서·동기 port 문단. Check-in target의 「수정과 감사」에는 결합 방식을 후속 Decision에 남긴 문구가 여전히 있어 문서 간 차이가 있다. 그 문구까지 일치한다고 간주하지 않으며 D4 승인 후 함께 정렬한다. |
| Freeze §7, Track B §3.2·§4 | 단독 일정 write의 schedule→time→occurrence→notification 세부 잠금 순서 및 수정·취소·새 horizon의 단일 transaction | **열람본만** | 처방 버전 target은 B domain rows 내부 순서와 일정 PUT/PATCH의 전체 UoW까지 고정하지 않는다. D4에서 이 세부 순서·rollback을 승인 요청한다. |
| Freeze §14, Track B §3.2 | 일정 parent scope, 동일 hash 성공 snapshot 재현, 암호화·1MiB cap·동일 transaction 저장 | **target 교차 확인됨** | [멱등성 v1](../../contracts/targets/post-mvp-1/idempotency-v1.md)의 「동기 상태 변경 처리 규칙」. 반복 취소 no-op 등 도메인 결과는 이 확인에 포함되지 않는다. |

`열람본만` 항목은 송은영의 D2–D4·물리 저장·provenance 검토와 남한솔의 상태·revision 소비 검토 후에만
적용한다. `change_source`, audit baseline 처리, 반복 취소 no-op 등 이번에 구체화한 세부값은 기존 원본
인용으로 승인되지 않으며 Proposed 계약 자체의 검토 대상이다. 열람본 전체 또는 Freeze 전체를 승인하는
것이 아니라 이 Decision에 명시한 delta를 승인받는다.

## 승인과 인계

| 책임 리뷰어 | 요청 범위 | 승인 증빙 |
| --- | --- | --- |
| 송은영 | D1–D5, audit 필드·보존·migration, 멱등성과 잠금·rollback | [APPROVED · 5165156334](https://github.com/AI-HealthCare-05/AH_05_04/pull/424#pullrequestreview-5165156334), `4fdecc8af73a62803cd970160886115d0c91be36`, 2026-09-10 09:12:55 UTC |
| 남한솔 | D1·D3·D4, 단일 reason, INACTIVE aggregate delta, 취소/재활성화/종료와 과거 기록 표시 | [APPROVED · 5165125801](https://github.com/AI-HealthCare-05/AH_05_04/pull/424#pullrequestreview-5165125801), `4fdecc8af73a62803cd970160886115d0c91be36`, 2026-09-10 09:09:32 UTC |

김지혜 (`Jye-rookie`)의 기존 검토는 현재 코드 대조에 대한 참고 의견이며, 이 Decision의 책임 승인으로
산입하지 않는다. OCR·Worker·Source 변경을 포함하지 않으므로 세 번째 책임 리뷰 범위를 추가하지 않는다.

두 책임 리뷰어가 같은 commit을 승인했다. 송은영의 최종 리뷰는 교차 확인 표와 리뷰어 배정 blocker 해소를 확인했고, 남한솔은 D1·D3·D4와 과거 기록 표시의 추가 blocker가 없음을 확인했다. 머지 PR은 [#424](https://github.com/AI-HealthCare-05/AH_05_04/pull/424), develop merge commit은 `015571a0a928f1146654bc5a9dacccada5b361f0`이다.

승인 범위는 이 Decision의 D1–D5 및 명시된 원본 delta다. 열람본 해시를 8월 승인 해시로 대체하거나 원본 전체의 승인으로 확대하지 않는다. 계약을 `targets/post-mvp-1/`로 이동하고 기존 target과 인덱스를 정렬했다. 후속 DB #438·알림 #430·API #456은 병합됐다. 해당 구현·검증 및 책임 승인 증빙의 확인은 #424의 문서 승인과 별도이며, 이번 상태 정리에서는 `current/`로 승격하지 않는다.

#202·#203은 이 Decision의 승인 결과를 소비한다. 다른 브랜치에서 같은 계약 파일의 값을 별도로 확정하지
않는다. #202 Check-in API와 #203 저장·조회·읽음 중 이미 명확한 범위는 병행할 수 있다.
공통 Privacy Production gate와 Track C/F 공개 gate는 기존 조건을 유지한다.
