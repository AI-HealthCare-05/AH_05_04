# Source Artifact·REJECTS 보존·삭제 정책 및 수동 정리 설계 (#335)

- 문서 상태: **Proposed / PM 의견 반영·통합 검토본**. 승인된 Target 또는 현재 실행 계약이 아니다.
- 문서 초안 작성: 김지혜 (`@Jye-rookie`)
- 정책 결정: 권가빈 (`@hazelnutflavoured`)
- DB·보안 검토: 송은영 (`@phina-io`), Source·provenance 검토: 정현우 (`@ceohwj`)
- 관련 작업: [#335](https://github.com/AI-HealthCare-05/AH_05_04/issues/335), [#165](https://github.com/AI-HealthCare-05/AH_05_04/issues/165), [#323](https://github.com/AI-HealthCare-05/AH_05_04/pull/323), [#166](https://github.com/AI-HealthCare-05/AH_05_04/issues/166)
- 제안 기록: [Source 보존·삭제 정책 초안 기록](../../../governance/decisions/2026-09-08-source-artifact-cleanup-proposal.md)
- 정책 버전 제안: `source-artifact-retention-v1` (문서 승인 전 제안 식별자).
- 2026-09-08: [가빈님 정책 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5580028895)과 은영님·현우님 기술 의견을 반영했다. PM 답변 대기 상태는 해소됐으며, 후속 구현은 #347에 연결했고 김지혜가 담당한다. 통합 문서의 DB·Source 검토와 운영 실행 인계는 남아 있다.

이번 PR은 정책 문서 작업이다. 정기 자동 삭제는 구현 범위에서 제외하고, 필요한 최소 후속 작업은 후보 산출·일회성 수동 정리·감사 기록으로 제한한다. 코드·migration·삭제 기능·운영 설정은 변경하지 않는다. 정책 반영은 #323 병합을 막지 않으며 실제 Source Runtime 활성화 승인도 아니다.

## 기존 구현 조사 요약

아래 구현 조사는 PR #323의 최종 병합 커밋 `2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd`를 기준으로 갱신했다. #323은 2026-09-08 06:38:52 UTC에 병합됐으며 최종 PR head는 `61b1adeab9f5f774d056a95169e50aa7328a21a7`이다. 해당 코드는 develop에 병합되었고 이 문서 브랜치에도 반영했다. 병합은 운영 배포나 실제 Source Runtime 활성화 승인을 의미하지 않는다.

| 확인한 동작 | 정책·설계에 미치는 영향 | 코드 근거 |
| --- | --- | --- |
| RAW_RESPONSE·REJECTS는 파일을 먼저 보존한 뒤 DB 참조 저장 | DB rollback이나 뒤 파일의 저장 실패 후 선행 객체가 남을 수 있음 | [persistence](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/ai_worker/tasks/rag/source_ingestion/persistence.py) |
| Local은 root 아래 SHA-256 key, S3는 bucket/prefix 아래 SHA-256 key 사용 | Run·종류와 무관하게 객체 공유 가능. namespace 결속 필요 | [Local store](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/ai_worker/adapters/local_private_source_artifact_store.py), [S3 store](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/ai_worker/adapters/s3_private_source_artifact_store.py) |
| Artifact→Run→Snapshot, Catalog/Verification→Snapshot. FAILED Run은 Snapshot NULL, NO_CHANGE Run은 Snapshot 필수 | NO_CHANGE·FAILED Run 참조와 간접 provenance도 보호 | [lifecycle](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py) |
| Artifact DB 행은 append-only, 객체 key index는 비unique | 공유 참조를 없애려고 DB 행을 먼저 삭제하지 않음 | [DB 모델](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/backend/app/models/rag_source.py) |
| 보존 만료·cleanup 목록/삭제 포트·Source cleanup 감사 기능 없음 | 기존 저장소 기능과 후속 cleanup 구현 구분 | [Artifact 포트](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/ai_worker/tasks/rag/source_ingestion/artifacts.py) |

Source Artifact는 RAW_RESPONSE·REJECTS를 포괄하는 표현이며 별도의 세 번째 종류 enum이 아니다. 물리 bucket/root·운영 객체·S3 lifecycle/versioning/Object Lock/IAM은 이번 조사에서 조회하지 않았다. S3 immutable metadata를 실제 Object Lock으로 해석하지 않는다.

최종 병합 커밋을 별도 checkout하여 저장소/factory/acquire 테스트 **45건**, 이를 포함한 Source ingestion 단위 테스트 **272건**을 2026-09-08에 재실행해 통과했다. 과거 조사 수치를 재사용한 것이 아니며 두 수치는 중복 합산하지 않는다. [재현 명령·검증 범위](../../../testing/source-artifact-policy-335-merged323.md)를 참고한다. 합성 파일·모의 S3·단위 테스트 결과이며 cleanup 구현 또는 실제 운영 저장소 검증을 의미하지 않는다.

이전 조사 이후 추가된 `165f90716263` migration은 FAILED Run의 Snapshot NULL 및 NO_CHANGE Run의 Snapshot 필수를 CHECK로 강제한다. migration 테스트는 상태별 허용·거부와 실제 `configure-app-role.sql` provisioning 질의를 사용한 Runtime 권한 검증을 포함한다. [최종 migration](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/backend/alembic/versions/165f90716263_check_ingestion_run_snapshot.py), [최종 DB 테스트](https://github.com/AI-HealthCare-05/AH_05_04/blob/2fa814ad88bd86f05cf44c11e3e8ca2daf720bfd/tests/migration/test_rag_source_catalog_migration.py)를 코드로 대조했으며, 이번 문서 수정에서 PostgreSQL 테스트를 재실행하지는 않았다. 실제 파일과 DB rollback을 함께 재현하는 cleanup 통합 검증은 #347 후속이다.

## 기술 검토 의견 반영표

| ID | 검토 의견 | 출처 | 초안 반영 및 미확정 부분 |
| --- | --- | --- | --- |
| R1 | 개별 실행/행이 아니라 동일 객체 전체 참조 확인 | 은영·현우 | `(storage_backend, object_key)` 전체 참조를 확인. 실제 bucket/root 결속 방법은 구현 설계에서 구체화 |
| R2 | 참조가 있거나 확인할 수 없으면 삭제하지 않음 | 은영·현우 | 조회 실패·불완전 판정도 삭제 금지. 최종 상태 enum은 미확정 |
| R3 | Snapshot·Catalog·검증 이력과 downstream provenance 보호 | 은영·현우 | 현우님이 명시한 Rule·Knowledge Chunk·Runtime Bundle·Citation·평가 증빙까지 범위에 포함. 실제 조회/FK 연결은 별도 확인 |
| R4 | 삭제 직전 참조 재확인, 새 참조 발생 시 중단 | 은영·현우 | 후보 판정과 실행 직전 판정을 분리. 검사 직후 새 참조와의 경합을 막는 방식은 아직 설계되지 않음 |
| R5 | rollback 잔존 객체에 유예기간 필요 | 현우 | 진행 중 수집과 분리. 생성 후 30일 유예를 PM 의견으로 반영. 신뢰할 생성 시각과 활성 수집 판정 방법은 구현 검토 대상 |
| R6 | 성공 건 유지, 실패 객체만 멱등 재시도 | 현우, 은영은 재시도 정책 결정 요청 | 실패 때문에 DB 참조/메타데이터를 먼저 제거하지 않음. 최대 횟수·간격·종료 조건은 미확정 |
| R7 | 민감정보 없는 감사 기록 | 은영·현우 | 아래 최소 후보 필드 반영. payload·인증정보·Provider 원문 오류는 제외 |
| R8 | 삭제 기록 append-only, 관련 provenance가 유지되는 동안 보존 | 현우 | 기술 보존 방향으로 반영. 관련 provenance 유지 기간에는 함께 보존. 종료 후 보존 장소·관리자·종료 기준은 배치 실행 전 지정 |
| R9 | Source 전용 cleanup/audit 이력 | 은영 | account_deletion_request 재사용 및 AI_JOB/OCR_JOB/GUIDE/CHAT_MESSAGE 상태 대체 제외. 테이블명·schema는 확정하지 않음 |
| R10 | OCR 업로드·Guide/Chat 결과 삭제는 별도 범위 | 은영 | #335는 RAG Source RAW_RESPONSE/REJECTS에 한정 |

## PM 의견 반영 정책

| ID | 항목 | 통합 문서에 반영한 기준 | 근거·남은 경계 |
| --- | --- | --- | --- |
| P1 | 미참조 Artifact 보존 | 객체 생성 후 30일 유예가 지난 경우에만 후보 검토 | PM 1·2번. 30일 경과만으로 삭제하지 않음 |
| P2 | 기산점 | 해당 물리 객체의 생성 시각 | Run 생성/완료 시각으로 임의 대체하지 않음. 생성 시각을 증명할 수 없으면 보류 |
| P3 | 참조 중인 RAW_RESPONSE·REJECTS | 서비스 운영 기간에는 삭제하지 않음 | Snapshot·Catalog·검증·Runtime Bundle·Citation·평가 및 RAG가 명시한 Rule·Knowledge Chunk 보호. 유형별 별도 만료기간은 도입하지 않음 |
| P4 | 서비스 종료 | 참조 관계와 필요한 증빙을 최종 확인한 뒤 일회성 수동 정리 | 종료가 참조 0건 규칙의 예외는 아님. 참조 객체의 일괄 강제 삭제를 허용하지 않음 |
| P5 | 주체·절차 | Backend·운영 실행, DB·보안 검토 후 권가빈 PM이 배치 승인 | 후보 산출 → DB·보안 검토 → PM 승인 → 삭제 직전 재확인 → 삭제. 정기 스케줄 구현 제외 |
| P6 | 실패 처리 | 성공 건 유지, 실패 대상만 재확인 후 재시도 | 성공 전 DB 참조·메타데이터 선삭제 금지. 자동 무한 재시도 도입 없음 |
| P7 | 감사 | 원문 없는 append-only 기록, 관련 provenance 유지 동안 보존 | 종류·checksum 또는 안전한 식별자·정책 version·참조 확인 결과·승인자·실행자·시간·성공/실패·안전한 오류 코드 |
| P8 | 정본 위치·후속 | 이 문서를 단일 정책 본문으로 사용하고 결정 기록·문서 index에서 연결 | 리뷰 승인 전 Proposed 유지. 승인 후 동일 문서를 targets로 이동하고 링크 갱신, 중복 정본 생성 금지 |

30일은 보존 유예 기준이며 자동 삭제 시각이 아니다. 구현 시 정확한 시간 비교·경계 테스트를 명시하고, 시간 오차나 객체 재생성으로 기산점이 불확실하면 보류한다. 참조 중인 객체에는 30일 만료 삭제를 적용하지 않는다.

## 담당과 실행 전 인계 사항

| 역할 | 반영한 책임 | 남은 지정 |
| --- | --- | --- |
| 정책·배치 승인 | 권가빈 (`@hazelnutflavoured`) | 실제 삭제 배치별 승인 증빙 |
| DB·보안 검토 | 송은영 (`@phina-io`) | 최종 참조 판정·감사·접근 통제 검토 |
| Source·provenance 검토 | 정현우 (`@ceohwj`) | 보존 기준 검토. 매 배치 실행/승인 역할 아님 |
| 삭제 실행 | Backend·운영 담당 | 실행할 개인과 종료 후 인계 담당은 후속 이슈/배치에 지정 |
| 정책 정리·후속 코드 구현 | 김지혜 (`@Jye-rookie`) | #347 구현 담당. 실제 운영 삭제 실행자는 별도 지정 |

아래 항목은 PM 답변을 다시 기다릴 정책 질문이 아니라 후속 구현·실행 전에 채울 인계 정보다.

- 실제 종료 일정, 실행자와 종료 후 감사 기록 관리자·보관 위치·보존 종료 기준.
- 신뢰할 객체 생성 시각, bucket/root namespace, 전체 참조 조사 범위와 수집 경합 방지 수단.
- 배치 목록·승인 결속, 실패 재시도 간격/한도와 장기 실패 인계 방식.
- 감사 보존 기간의 고정 숫자는 아직 없다. 관련 provenance가 유지되는 동안 함께 보존하고, 그 이후의 삭제를 이번 문서가 승인하지 않는다.

`retain_until`, 감사 테이블명, 상태 enum, 잠금 방식은 확정하지 않는다. 정책 v1 문서 검토와 실제 실행 준비 완료를 구분한다.

## 적용 범위와 해석

대상은 RAG Source의 RAW_RESPONSE·REJECTS 객체와 그 보존·삭제 감사 이력이다. OCR 업로드 파일, Guide/Chat 결과, 계정 탈퇴 처리와 Job 상태 관리는 포함하지 않는다.

[은영님 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579761899)의 참조 확인·Source 전용 감사 경계와 [현우님 의견](https://github.com/AI-HealthCare-05/AH_05_04/issues/335#issuecomment-5579812614)의 공유 객체 보호·재확인·실패 재시도를 설계 입력으로 사용했다. 아래 배치 결속, 경합 차단, 결과 불명확 처리의 세부 흐름은 이를 만족하기 위한 **설계 제안**이며 추가 합의가 필요한 부분은 별도로 표시한다.

현재 자동 삭제와 실제 Source Runtime은 DISABLED로 유지한다. 정책·구현·검증 완료 전에는 아래 흐름을 실제 MFDS Source·운영 S3·사용자 데이터에 실행하지 않는다. 정책이 없는 상태에서는 삭제 가능으로 판정하지 않는다.

## 흐름 요약

```text
정책·대상 저장소·실행 범위 확인
  → 객체 후보 조사
  → 보존/유예기간·진행 중 수집·전체 참조 검사
  → 검토할 배치 목록 고정
  → DB·보안 검토
  → 권가빈 PM의 배치 승인
  → 실행 권한·승인 유효성·정책 재확인
  → 신규 참조 생성과 경합하지 않는 경계 확보
  → 삭제 직전 전체 참조·대상 객체 재확인
  → 실행 의도 영속 기록
  → 객체별 삭제 요청과 결과 확인
  → 결과 이력 append
  → 실패/결과 불명확 건만 재확인 후 후속 처리
```

검사 실패·정보 부족·새 참조 발생 시 해당 객체를 보류한다. 승인은 참조 보호나 기간 검사를 생략할 권한이 아니다. 아래의 “보류”, “실패”, “결과 불명확”은 설명용 용어이며 DB enum이나 오류 코드가 아니다.

## 단계별 입출력과 중단 조건

| 단계 | 입력·작업 | 결과 | 보류/중단 조건 |
| --- | --- | --- | --- |
| S1 정책·환경 확인 | 승인된 정책 version, 객체 생성 시각·30일 유예·참조 보호 기준, 저장소 식별, 실행·승인 책임 확인 | 이번 조사의 범위 | 정책 누락·미승인, 저장소 위치 불명확, 실제 환경 실행 승인 부재 |
| S2 후보 조사 | 확정 범위 내 객체 식별자·checksum·종류·시간 근거 조사 | 아직 삭제 자격을 부여하지 않은 목록 | 목록 조회 실패/불완전, 종류·시간 근거를 판정할 수 없음 |
| S3 자격 검사 | 보존/유예 만료, 진행 중 수집 여부, 전체 객체 참조 검사 | 검토 후보 및 제외/보류 사유 | 기간 미경과, 참조 존재, 조회 실패, 진행 중 여부 불명확 |
| S4 배치 검토·승인 | 후보 목록, 정책 version, 참조 검사 범위·시각, 안전한 요약 제공 | 정확한 대상 범위에 결속된 승인 근거 | 승인자 미지정·무권한, 승인 범위/유효성 불명확 |
| S5 실행 전 재검사 | 승인·정책·대상 식별 재확인, 쓰기/삭제 경합 방지, 최신 참조 검사 | 해당 객체의 실행 가능 여부 | 새 참조·진행 중 쓰기, 정책/대상 변경, 경합 보호 불가 |
| S6 실행 의도 기록 | 배치·객체·시도 식별, 승인 근거, 검사 결과를 영속 저장 | 재시작 시 확인 가능한 실행 의도 | 기록 실패 시 삭제를 호출하지 않음 |
| S7 삭제·확인 | 승인한 정확한 객체에 삭제 요청, 저장소 의미에 맞는 결과 확인 | 성공·실패·결과 불명확 | timeout 등을 성공으로 간주하지 않음 |
| S8 감사·후속 처리 | 결과를 덮어쓰지 않고 append, 남은 건의 상태 확인 | 감사 가능한 진행 결과 | 감사 기록 실패 시 완료 선언 금지, 의도 기록 기준으로 복구 |

## #347 구현 요구사항: 배치 namespace 결속

현재 `storage_backend`는 adapter 종류 상수이며, S3 `object_key`에는 prefix만
포함되고 bucket은 없다. Local 행에도 root가 없다. 따라서 서로 다른 저장소가
동일한 `(storage_backend, object_key)`를 가질 수 있으며 이 두 값만으로 물리 객체를
식별하거나 DB 조회 결과를 다른 저장소에 적용할 수 없다.

#347의 최소 구현은 **배치가 대상 namespace를 명시하고 고정하는 방식**으로 한다.
새 bucket/root 컬럼을 이 문서에서 확정하지 않으며 다음 사항을 구현·검증한다.

1. 배치 manifest에 조회 DB의 식별 정보(비밀정보 제외), 대상 저장소의 안전한
   namespace 식별자, S3 bucket/prefix 및 endpoint가 있으면 endpoint, 또는 Local의
   정규화된 절대 root를 결속한다. 실제 설정값은 접근 통제된 배치 정보에서 관리하고
   일반 로그에 자격증명·민감 경로를 노출하지 않는다.
2. 후보 산출·전체 참조 조회·PM 승인·삭제 실행이 같은 manifest를 사용한다.
   실행 adapter의 실제 config가 이 namespace와 일치하지 않으면 삭제를 호출하지 않는다.
   DB와 저장소가 같은 환경의 데이터라는 근거를 확인하며, 과거 config 변경·공유 DB 등으로
   귀속이 불명확하면 참조 0건을 근거로 삭제하지 않고 보류한다.
3. 삭제 직전 동일 namespace에서 실제 객체의 `raw_checksum`과 `byte_size`를
   후보 manifest와 재대조한다. key 문자열·ETag·metadata 주장만으로 대체하지 않는다.
   객체 교체·재생성 여부와 생성 시각도 다시 확인하고, 확인 불가·불일치 시 보류한다.
4. checksum·크기가 같아도 서로 다른 bucket/root의 객체는 동일 삭제 대상이 아니다.
   내용 동일성 검사는 namespace/DB 결속 또는 신규 참조 생성 경합 차단을 대신하지 않는다.
5. namespace·DB·대상 목록이 달라지면 기존 승인을 재사용하지 않는다. 최종 검사는
   아래 쓰기/삭제 경합 방지 경계 안에서 수행한다.

## #347 구현 요구사항: 전체 참조 조회 기준선

조사 기준 병합 코드에서 객체를 직접 참조하는 테이블은
`rag_source_ingestion_artifact` 하나다. 현재 직접 조회는 해당 테이블의 동일
`(storage_backend, object_key)` **전체 행**을 대상으로 하며 Run 상태나 최신 Snapshot
조건으로 걸러내지 않는다. namespace 컬럼이 없으므로 동일 key의 기존 행을 임의로
다른 namespace 소유라고 제외하지 않는다.

간접 관계는 Artifact → ingestion run → snapshot을 기준으로 Catalog·Verification 및
구현된 downstream 관계를 추적한다. 이는 보호·감사 근거이며 직접 참조가 있는 객체를
삭제 가능으로 바꾸는 조건이 아니다. FAILED Run은 Snapshot이 NULL이어도 직접 참조로 보호한다.

#347은 실제 실행 스키마 기준으로 참조 테이블·관계 목록과 조회 범위를 기록한다.
새 직접 참조 테이블이 추가되면 이 목록과 테스트를 갱신해야 한다. 관계 미구현·문자열 ref·
외부 증빙 또는 조회 권한 부족으로 범위를 확정할 수 없으면 “참조 없음”으로 처리하지 않는다.

## 객체와 참조 판정

- 개별 Run·Snapshot 하나가 아니라 같은 `(storage_backend, object_key)`를 가리키는 전체 행을 확인한다. 실제 bucket/root/endpoint namespace도 정확히 결속해야 한다. 현재 Artifact DB 행만으로 과거 저장소 namespace를 알 수 있다고 가정하지 않는다.
- FAILED Run의 snapshot_id가 NULL이어도 Artifact 참조가 있으면 보호한다. NO_CHANGE 실행의 참조와 다른 Run의 공유 참조도 포함한다.
- Snapshot·Catalog·Verification·Rule·Knowledge Chunk·Runtime Bundle·Citation·평가 증빙의 관련성을 확인한다. 아직 구현되지 않은 관계나 문자열 ref를 자동으로 “참조 없음”으로 취급하지 않는다.
- 객체 목록만으로 RAW_RESPONSE/REJECTS 종류가 구분되지 않으면 Source 소유 객체라는 근거 없이 삭제하지 않는다. 현재 내용 주소 key에는 종류가 없고 여러 종류로 공유될 수 있다. 이번 정책은 유형별 기간을 나누지 않으며 하나라도 참조가 있으면 보호한다.
- 현재 Artifact 참조 행은 append-only다. cleanup을 가능하게 만들기 위해 참조 행을 먼저 지우거나 무효화하는 절차를 추가하지 않는다. 참조가 남아 있는 객체의 향후 보존 종료 처리는 별도 정책 검토가 필요하다.
- 보존기간 경과와 참조 0건은 각각 필요한 검사다. 어느 하나가 다른 검사를 대체하지 않는다.

## 승인 후 변경과 동시 수집

배치 승인은 고정된 대상 목록·정책 version·저장소 범위에 연결하는 방식으로 제안한다. 정확한 목록 식별 방식과 승인 유효기간은 미확정이다. 승인 뒤 새 객체를 자동 추가하지 않는다. 대상이나 정책이 달라지면 기존 승인으로 계속하지 않고 재검토한다.

삭제 직전 SELECT를 한 번 더 수행하는 것만으로는 충분하지 않다. 검사 이후 삭제 사이에 새 참조가 생기거나, DB commit 전 파일이 먼저 저장될 수 있기 때문이다. 유예기간만으로 이 경합이 해소됐다고 보지 않는다.

구현 전에 DB·Source 담당과 다음 불변식을 만족할 방식을 정해야 한다.

1. 같은 객체에 대해 cleanup과 신규 참조 생성/객체 재사용이 동시에 완료되지 않는다.
2. 진행 중인 업로드·수집을 잔존 객체로 삭제하지 않는다.
3. 보호 장치의 장애·소유권 상실 시 삭제를 계속하지 않는다.
4. 같은 key의 객체 교체/재생성이 발생하면 과거 후보 조사나 승인을 새 객체에 적용하지 않는다.

객체별 claim/lease, 수집 경로와 공유하는 잠금, 저장소 조건부 삭제 등은 검토 가능한 수단일 뿐 채택된 구현이 아니다. DB lock이 Local/S3 삭제까지 자동으로 원자화하지는 않는다. 저장소별 지원 범위와 장애 시 복구를 포함해 검증해야 한다.

## 삭제 결과·감사·재시도

DB와 객체 저장소는 하나의 transaction이 아니다. 삭제된 파일을 DB rollback으로 복원할 수 있다고 설계하지 않는다. 삭제 전 의도를 영속 기록하고, 결과가 불명확하면 해당 기록으로 조사·복구하는 방식을 제안한다.

| 상황 | 설계 제안 |
| --- | --- |
| 일부 객체만 성공 | 성공 결과는 유지. 실패/불명확 객체만 후속 확인 |
| 삭제 API timeout | 성공 또는 실패로 단정하지 않고 정확한 대상의 존재·버전을 재확인 |
| 이미 없는 객체 | 이전 실행 결과·대상 식별이 일치하는지 확인. 없다는 이유만으로 이번 실행이 삭제했다고 기록하지 않음 |
| 삭제 성공 후 감사 저장 실패 | 의도 기록을 남긴 상태로 복구 대상 처리. 배치 전체 성공으로 선언하지 않음 |
| 재시도 전 새 참조 발생 | 재시도를 보류하고 삭제하지 않음 |
| 같은 key에 객체 재생성 | 과거 성공/실패를 그대로 적용하지 않음. 새 객체를 구분할 근거가 없으면 보류 |
| S3 versioning/삭제 marker | 삭제 응답만으로 바이트의 영구 제거를 단정하지 않음. 실제 설정과 정책의 삭제 의미를 먼저 확정 |
| Local 임시 파일 | 진행 중 쓰기 보호와 잔존 판정이 확정되기 전 `.pending-*`도 일괄 삭제하지 않음 |

감사 이력 후보는 앞의 리뷰 의견에 배치·객체별 시도 연결과 실행 의도/결과 구분을 더한 것이다. 대상 식별·checksum·종류·정책 version·승인 근거·검사 결과·실행자·시각·안전한 결과 코드만 다룬다. 원문 payload·credential·Provider 원문 오류는 기록하지 않는다.

감사 이력은 Source 전용이며 기존 계정 삭제 요청이나 Job 상태를 대체하지 않는다. 재시도 간격·한도·승인 재사용 조건·장기 실패 담당은 후속 실행 절차에서 정한다. 감사 기록은 관련 provenance 유지 동안 함께 보존하고, 종료 후 관리자·보관 위치·보존 종료 기준은 별도로 인계한다. 기록을 삭제하거나 수정하는 복구를 전제하지 않는다.

## 검증 시나리오

아래는 **후속 구현을 위한 테스트 명세**이며 이번 단계에서 실행한 테스트가 아니다. 30일 유예와 운영 기간 중 참조 보호를 검증한다. 자동 스케줄러는 구현하지 않는다. 승인된 정책과 합성 입력을 사용한다.

| ID | 조건/입력 | 기대 결과 | 근거 |
| --- | --- | --- | --- |
| T01 | 정책·기간·기산점 중 하나 누락 | 삭제 호출 0건, 미확정 이유 표시 | P1·P2, S1 |
| T02 | 자동 삭제 DISABLED | 스케줄/삭제 실행 0건 | 기존 진행 조건 |
| T03 | 같은 객체를 두 Run이 공유 | 한 Run만 확인해서 후보 승인하지 않음 | R1 |
| T04 | snapshot_id=NULL인 FAILED Run이 객체 참조 | 삭제 호출 0건 | `165f90716263`의 `chk_rag_ingestion_run_snapshot_status`가 FAILED ⇒ Snapshot NULL 보장; Artifact 직접 참조 보호 |
| T05 | NO_CHANGE Run 또는 과거 Catalog가 객체 참조 | 삭제 호출 0건 | R1·R3 |
| T06 | Citation·평가 증빙 등 downstream 참조 존재 | 삭제 호출 0건 | R3 |
| T07 | DB 조회 실패·관계 해석 불가·목록 불완전 | 해당 범위 삭제 호출 0건, 보류 | R2 |
| T08 | 생성 후 30일 미경과·경계 시각 또는 생성 시각 근거 없음 | 삭제 호출 0건 | P1~P3 |
| T09 | 파일 생성 후 DB commit 전 수집 진행 중 | 객체 보호, 삭제 호출 0건 | R5 |
| T10 | rollback 잔존, 종류·namespace 확인, 생성 후 30일 유예 경과·전체 무참조·PM 배치 승인 충족 | 나머지 실행 조건 충족 시 해당 객체만 삭제 및 기록 | R1·R5, S1~S8 |
| T11 | 승인 후 대상 목록·정책·저장소 범위 변경 | 기존 승인으로 삭제하지 않음 | S4·S5 제안 |
| T12 | 승인 누락·만료·무권한 | 삭제 호출 0건 | P5, S4 |
| T13 | 승인 후 삭제 직전 새 참조 발생 | 해당 객체 삭제 중단 | R4 |
| T14 | 최종 검사 직후 신규 참조가 경쟁 | 삭제와 참조 생성이 충돌 없이 조정됨. dangling reference 0건 | 5절 불변식 |
| T15 | 동일 객체를 두 cleanup 실행이 처리 | 중복 시도가 구분되고 결과를 일관되게 재현 | R6, S6~S8 |
| T16 | 여러 객체 중 일부 삭제 실패 | 성공 건 유지, 실패만 재검토·멱등 재시도 | R6 |
| T17 | 삭제 timeout 또는 재시작 시 객체 부재 | 결과 불명확 해소 전 성공 단정 금지 | 6절 제안 |
| T18 | 실행 의도 기록 실패 | 저장소 삭제 호출 0건 | S6 제안 |
| T19 | 삭제 성공 후 결과 기록 실패·프로세스 재시작 | 의도 기록으로 복구하며 기존 이력 수정 없이 결과 추가 | S8 제안 |
| T20 | 재시도 직전 참조 생성·객체 교체 | 과거 승인/시도를 이용한 삭제 금지 | R4·R6 |
| T21 | 승인/결과 이력 UPDATE·DELETE 시도 | 확정된 감사 불변성 경계에서 거부 | R8 |
| T22 | Provider 오류에 원문·인증정보 포함 | 감사·로그·응답에 원문/비밀정보 없음 | R7 |
| T23 | 같은 backend/key/checksum/크기지만 bucket/root 또는 조회 DB가 다름 | 기존 승인으로 삭제 호출 0건; 환경 결속 불명확 시 보류 | #347 배치 namespace 결속 |
| T24 | 객체 종류 불명확 또는 여러 종류의 참조 공유 | Source 소유 범위 불명확 또는 공유 참조 존재 시 삭제하지 않음 | 4절·P1 |
| T25 | 서비스 종료 및 참조 객체 존재 | 종료만을 근거로 즉시 삭제하지 않음 | P4·R3 |
| T26 | 운영 S3/실제 데이터가 합성 데모에 입력 | 실행 범위 검사로 차단 | 기존 데모 범위 |
| T27 | S3 삭제 marker만 생기고 과거 version 존재 | 영구 삭제 완료로 과장하지 않음 | 6절 제안 |
| T28 | 승인 뒤 adapter config의 bucket/prefix/root 변경 | manifest와 불일치하여 삭제 호출 0건 | #347 배치 namespace 결속 |
| T29 | 삭제 직전 실제 checksum·byte_size 불일치 또는 검증 실패 | 해당 객체 삭제 호출 0건 | #347 객체 동일성 재검사 |
| T30 | 직접 참조 행 존재 또는 새 참조 테이블이 조회 목록에서 누락 | 참조 보호 또는 범위 불완전으로 삭제 호출 0건 | #347 전체 참조 기준선 |

정상 저장/rollback·Local/S3 adapter 테스트와 위 cleanup 테스트는 별개다. 후속 검증에서는 실제 비특권 역할·독립 transaction·동시 실행·장애 주입으로 DB와 저장소 사이의 실패 경계를 확인해야 한다. 이번 문서 작성으로 해당 검증을 완료한 것은 아니다.

## 현재 코드와 후속 구현의 차이

| 영역 | 현재 | 필요한 후속 확인/구현 |
| --- | --- | --- |
| 객체 보존 | Local/S3 put·재사용·무결성 검증 존재 | namespace가 결속된 목록 조사·대상 식별 |
| 참조 조사 | Artifact 객체 index와 Run/Snapshot 연결 존재 | 전체 참조·downstream 관계 판정 |
| 기간 판정 | created_at 등 원시 시각 존재 | 객체 생성 후 30일과 신뢰 가능한 시각 근거 반영 |
| 동시성 | Source acquisition lock 존재 | 객체별 cleanup/쓰기 경합 조정. 기존 lock으로 충족한다고 가정하지 않음 |
| 승인·감사 | Source cleanup 전용 기능 없음 | 고정 배치 승인, 의도·결과 이력, 비특권 접근 통제 |
| 삭제·복구 | cleanup 포트 없음 | 저장소별 삭제 의미·실패 복구·재시도 |

후속 구현은 [#347 Source Artifact 일회성 수동 정리·감사 구현](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)에서 김지혜 (`@Jye-rookie`)가 담당한다. migration 테이블명·컬럼·enum·함수 signature는 이 문서에서 확정하지 않는다.

## 최소 후속 이슈와 완료 기준

후속 이슈: [#347 Source Artifact 일회성 수동 정리·감사 구현](https://github.com/AI-HealthCare-05/AH_05_04/issues/347)

범위: 위 배치 namespace·DB 결속 및 삭제 직전 checksum/크기 재검사, 직접·간접 참조 조회 목록 명시, 읽기 전용 후보 산출, 30일·전체 참조 검사, 고정 배치와 PM 승인 인계, 실행 직전 재확인 및 경합 차단, Local 합성 객체의 수동 삭제·append-only 감사·실패 재시도 검증, 서비스 종료 작업 runbook. 정기 자동 삭제·운영 S3 실행·Runtime 활성화·OCR/Guide/Chat 삭제는 제외한다. 실제 사용할 저장소 adapter는 실행 환경과 담당을 지정한 뒤 결정한다.

#347은 사용자가 생성했으며 구현 담당은 김지혜 (`@Jye-rookie`)다. DB·보안 검토는 송은영, Source·provenance 검토는 정현우, 정책·배치 승인은 권가빈이 맡는다. 공유 DB migration은 은영님과 범위를 협의한다. 실제 Backend·운영 삭제 실행자와 종료 후 감사 인계는 실행 전에 별도 지정한다. #165·#323의 인계 댓글은 게시 전 초안으로 구분한다.

| #335 완료 기준 | 이번 문서에서 처리 | 남은 일 |
| --- | --- | --- |
| 대상별 보존·기산점·삭제 조건 | PM 30일·생성 시각·전체 참조 0건·참조 보존 반영 | 통합 문서 리뷰 |
| 서비스 종료 기준·담당 | 일회성 수동·증빙 확인·Backend/운영 실행 반영 | 실제 실행자·종료 후 감사 인계 지정 |
| 실행 주체·주기·승인 | 정기 자동화 제외, PM 배치 승인 | 배치별 실행 증빙은 후속 |
| DB·Source 검토 | 두 검토 의견과 PM 의견 대조 완료 | 통합안의 최종 검토 |
| 감사 기록 범위·보존 | 최소 필드·append-only·provenance 연동 보존 반영 | 종료 후 보관·관리·종료 기준 인계 |
| 정본·결정 근거 | 본문·index·세 댓글·제안 기록 연결 | 승인 시 상태 승격 |
| 후속 이슈·담당 | #347 연결, 구현 김지혜 지정 | 운영 실행자·감사 인계는 #347에서 지정 |
| #165·#323 인계 | 붙여넣기용 댓글 초안 준비 | 사용자 게시 |

문서 작성 단계는 PM 의견 반영과 완료 기준 점검까지 마쳤다. 위 연결·검토가 남아 있으므로 #335는 Open으로 유지한다. 자동 삭제·Source Runtime은 계속 DISABLED이며 정책 완료만으로 활성화하지 않는다.


## #347 Local 합성 구현 증빙 (정책 상태 변경 없음)

#347의 내부 합성 실행 모델은 [검증 기록](../../../testing/source-artifact-cleanup-347.md)의
4단계에 기술한다. 직접 생성한 임시 파일에 한해 의도/결과 파일 기록·삭제·복구를 검증한다.
실제 승인 저장소·Source writer 공유 잠금·운영 감사 DB/권한·운영 재시도 정책을 확정하거나
이 Proposed 문서를 Current로 승격하는 변경이 아니다. 관련 담당·공유 계약 검토 조건은 유지한다.
