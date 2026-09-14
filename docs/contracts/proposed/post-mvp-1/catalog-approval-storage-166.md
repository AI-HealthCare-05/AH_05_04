# #166 실제 승인·철회·감사 저장소 연결 구체안

- 상태: **Proposed / 구현 전 검토안**. 테이블·API·권한·잠금 변경을 아직 구현하지 않았다.
- 조사 기준: develop `cacdd3e2`, 문서 브랜치 `ba41bb16` 이후.
- 작성: 김지혜. 후속 DB·Security 구현의 담당 리뷰어 제안: 송은영 1명.
- 승인 대상 의미·Candidate/Runtime 소비 경계는 정현우와, 외부 승인 주체·용도·유효기간 정책은 PM과 확인한다.
- 이번 문서는 기존 D-03/D-05 결정 기록에 추가하는 구체안이며, 현우님 답변이나 기존 PR 승인을 이 안의 승인으로 확대하지 않는다.
- `AGENTS.md`, `CONTRIBUTING.md`를 재확인했다. 업무 로직·무결성은 Python Service/Repository와 명시적 transaction으로 관리한다.
  일반 FK·UNIQUE·CHECK·최소 권한을 사용하고 신규 RLS·DB Trigger·업무용 DB 함수는 추가하지 않는다.

## 1. 현재 구현과 재사용 범위

| 현재 코드 | 실제 역할 | 연결안 |
| --- | --- | --- |
| `CatalogApprovalVerifier.verify()` | catalog_version·export_checksum·source_refs로 receipt 또는 None 반환하는 포트 | 실제 저장소 adapter 추가. 정확한 기존 receipt 지정 및 transaction 결속은 계약 보완 필요 |
| `CatalogApprovalReceipt` | 승인 ID·Catalog 버전·export checksum·완전성·Source receipt 목록 | 기존 export/envelope의 payload 형태는 유지 |
| `SourceManagementPermission` | Source/Catalog 관리 변경 권한 | 인증·명시적 grant/revoke 패턴 참고. 관리 권한을 승인 권한으로 자동 승격하지 않음 |
| `SourceManagementAudit` | UPDATE/DELETE/GRANT/REVOKE 관리 변경·권한 이력 | 기존 목적 유지. Catalog 승인·검증 실패를 관리 UPDATE로 위장하지 않음 |
| Source/Endpoint/Operation 상태·Snapshot Receipt | 수집 상태·현재성·checksum·출처 검증 | 승인과 함께 검사. acquisition APPROVED나 checksum 일치만으로 Catalog 사용 승인 간주 금지 |
| `RagReleaseEvaluationApproval` | Runtime Bundle 평가 승인 | Catalog 구성 승인으로 대체하지 않음 |

기존 관리 감사는 target revision과 before/after 상태를 중심으로 하며, 승인 유효기간·정확한 Catalog receipt·
실패한 build attempt의 관계를 표현하지 않는다. 승인 의미가 다른 기존 테이블에 범용 JSON을 덧붙이는 대신
아래 전용 구조를 제안한다. 유지보수 비용은 모델·migration·역할 정책·운영 명령·통합 테스트 추가이며,
실제 verifier의 권한·철회·만료 조회를 구현하기 위해 필요한 분리다.

## 2. 승인 대상과 순환 의존 방지

Catalog는 승인 receipt를 받은 뒤 최종 envelope와 Set을 만든다. 따라서 **최초 승인에 Set ID나
최종 envelope hash를 필수로 요구하지 않는다.** 승인 대상은 다음을 정확히 결속하는 안이다.

- catalog_version, export_checksum, export schema/spec, 검증된 export bytes
- `(source_snapshot_id, source_version)`의 정렬된 전체 집합 및 각 Source 승인 ID
- 승인 근거 참조, 용도, 완전성 검증 자료, 유효기간

승인 서비스는 원본 export bytes를 재계산하고 실제 Snapshot Receipt/구성 범위를 대조한다.
클라이언트가 주장한 checksum·APPROVED·is_complete만으로 승인하지 않는다.
D-02 normalization_run이나 D-05 projection hash를 필수 FK로 만들지 않는다.
Source 승인은 수집 자체가 아닌 **해당 Snapshot의 Catalog 사용 용도**로 범위를 구분하는 안이며,
기관·의료·Privacy 승인을 자동 생성하는 기능이 아니다. 실제 승인 권한과 증빙 정책을 먼저 정해야 한다.

## 3. 물리 저장 구조 제안

아래 이름·필수 필드·상태는 신규 제안이며 migration 확정안이 아니다.

| 테이블 제안 | 핵심 필드·관계 | 의미 |
| --- | --- | --- |
| `catalog_approval_permission` | user_id FK, enabled, grant 근거, revision | 승인·철회 명령을 수행할 명시적 운영자 권한. 자체 권한 부여 금지 |
| `catalog_source_approval` | id, snapshot_id/source_version 복합 FK, purpose, actor_id, evidence_ref, valid_from/expires_at, revoked_at/by/reason, revision | 하나의 Snapshot·version·용도에 대한 불변 승인 사실과 단방향 철회 |
| `catalog_build_approval` | id, catalog_version, export_checksum, schema/spec, export_bytes 또는 검증 가능한 불변 artifact 참조, is_complete, actor_id, evidence_ref, valid_from/expires_at, revoked_at/by/reason, revision | Set 생성 전 정확한 export 구성에 대한 승인 |
| `catalog_build_approval_source` | build_approval_id FK, source_approval_id FK, snapshot_id/source_version | Catalog 승인과 전체 Source 승인 집합 결속. 중복 Snapshot 금지 |
| `catalog_approval_audit` | id, request_id, event_kind, actor_id 또는 서비스 주체, nullable 승인 FK, nullable set_id, attempt_id, reason_code, request fingerprint, 시각 | 승인·철회·권한 변경의 성공 이력과 실패 attempt 기록 |

- ID는 기존 UUIDChar 규칙을 따른다. 승인/Source link는 RESTRICT FK, 기간은 expires_at > valid_from CHECK를 제안한다.
- link의 snapshot/version이 Source approval의 대상과 일치하도록 복합 FK를 검토한다.
- 승인 payload·기간·출처 목록은 발급 후 수정하지 않는다. 철회 metadata와 revision만 명시적으로 갱신한다.
  기간 연장·재승인은 새 ID를 발급한다. 만료는 비교로 판정하며 EXPIRED로 바꾸는 scheduler를 만들지 않는다.
- 단일 enum을 추가하지 않고 revoked_at과 기간으로 유효성을 계산하는 안이다. source의 APPROVED/REVOKED enum과 혼합하지 않는다.
- 이벤트 종류 후보는 GRANT_PERMISSION/REVOKE_PERMISSION/ISSUE_SOURCE/ISSUE_CATALOG/REVOKE_SOURCE/REVOKE_CATALOG/BUILD_FAILED/VERIFY_FAILED/AUDIT_RECOVERY다.
  실제 값·길이·오류 코드·감사 보존 정책은 은영님 검토 후 확정한다.
- 동일 운영 명령의 (actor_id, request_id)는 성공 이벤트 UNIQUE로 중복 발급을 방지한다.
  실패는 attempt_id + event_kind UNIQUE로 식별해 같은 요청의 다른 실행을 덮어쓰지 않는다.
- 범위 조회용 fingerprint는 권한·대상 비교를 돕는 내부 값이다. D-05 hash나 digest 단독 FK로 사용하지 않는다.
  검색 결과는 실제 source link 집합·schema·bytes와 다시 대조한다.
- 권한 grant/revoke도 같은 감사 transaction에서 기록한다. 서비스 계정은 운영자 ID를 사칭하지 않고 별도 principal로 식별한다.

## 4. 포트·receipt 연결안

현재 verify 인자에는 receipt_id와 schema가 없다. 이 상태로 “최신 승인 한 건”을 골라 반환하면
재승인 후 과거 manifest의 receipt가 바뀌어 exact bytes 비교가 실패하거나 잘못된 승인을 선택할 수 있다.

제안: 기존 입력에 **검증할 receipt_id와 export schema/spec을 명시하는 내부 요청 계약**을 추가하고,
발급/선택 단계와 기존 receipt 검증 단계를 구분한다. 정확한 함수명·DTO는 구현 PR에서 계약·호출자·테스트를
함께 변경한다. 암묵적인 optional 기본값으로 운영 경로가 예전 동작을 계속하게 만들지 않는다.

- 최초 build: 검증한 export에 발급된 ID를 명시적으로 선택한 뒤 기존 receipt payload로 envelope를 구성한다.
- save: 동일 승인 ID·전체 Source 승인 ID를 **저장 transaction 안에서** 다시 검사한다.
- restore: manifest에 저장된 승인 ID를 지정해 해당 승인이 아직 유효한지 검사한다.
- 재승인: 과거 Set의 receipt나 bytes를 바꾸지 않는다. 새로운 receipt를 담은 export/envelope/Set 경로를 사용한다.
- 철회·만료: 과거 이력은 보존하되 현재 소비용 restore는 거부한다. 관리용 이력 조회는 별도 권한이며 Candidate 입력으로 반환하지 않는다.
- Source freshness와 승인 유효성은 별도 판정이다. CURRENT Snapshot이라는 이유로 사용 승인 없이 통과시키지 않는다.

## 5. transaction·경합·소비 시점

현재 `save_build()`는 adapter가 transaction을 소유하고, `load_build()`는 읽기 transaction을 끝낸 뒤
별도 verifier를 호출한다. 따라서 포트만 실제 DB 조회로 교체하는 것으로 동시 철회 문제가 해결되지 않는다.

### 제안하는 잠금 순서

운영 명령은 actor 권한을 먼저 잠그고, 그다음 아래 공통 자원 순서를 따른다.

`Source → Endpoint → Operation → Snapshot → Source approval → Catalog approval → Catalog 구성원/Set`

각 종류 안에서는 ID 오름차순으로 잠근다. 필요한 상위 자원만 잠그되 이미 가진 잠금보다 앞 단계로
돌아가지 않는다. 기존 Source writer의 Operation→Snapshot 순서와 Catalog 구성원/Set의 advisory lock 위치를
모든 writer와 대조한 뒤 확정한다. 이 순서가 이미 적용됐거나 전역 deadlock이 검증됐다고 주장하지 않는다.

- Catalog save owner는 기존 adapter로 유지한다. 같은 session에서 승인 잠금·재검증·구성원/Set 저장을 수행한다.
  승인 확인 후 신규 session을 열어 저장하지 않는다. 승인 전용 writer는 이 순서로 철회 row를 갱신한다.
- 철회가 먼저 commit되면 save/소비는 거부한다. save가 먼저 잠금을 획득했다면 저장을 마친 뒤 철회가 진행되며,
  이미 저장한 Set이라도 후속 소비에서는 새로 유효성을 확인한다.
- load의 단순 읽기와 승인 검사를 한 DB 경계로 정렬하는 adapter 변경이 필요하다.
  현재 READ ONLY repeatable-read 그대로는 승인 행 FOR UPDATE를 추가할 수 없으므로,
  잠금 가능한 최소 권한 검증 transaction 또는 revision 기반 소비 검증 중 구현안을 선택해야 한다.
  우선 잠금 가능한 같은-session 검증을 제안하되 Source writer와의 경합을 통합 테스트한다.
- 반환된 Python 객체를 영구 사용 허가로 간주하지 않는다. Candidate 저장/게시 및 Runtime 사용 확정 경계에서
  같은 receipt ID와 현재 상태를 다시 검사해야 한다. 이 후속 소비 연결 없이 “철회 즉시 모든 사용 차단 완료”를 주장하지 않는다.
- 만료는 UTC의 valid_from <= 검사 시각 < expires_at이다. transaction 시작 시각 고정값만 쓰지 않고
  사용 확정 직전 실제 시각을 재검사한다. commit/외부 소비 이후까지 시간 정지를 보장한다고 표현하지 않는다.
- 권한 철회는 새 승인 명령을 차단한다. 이미 발급한 승인의 일괄 자동 철회 여부는 별도 정책이며 현재 안에서는
  명시적인 승인 철회 명령과 구분한다. 이 정책 역시 담당자 확인 대상이다.

## 6. 실패 감사와 commit 불명확 처리

- 승인 발급/철회 성공과 성공 감사는 같은 transaction이다. 감사 저장 실패 시 승인 변경도 rollback한다.
- Catalog 실패 감사는 Catalog transaction rollback 뒤 별도 감사 전용 transaction에서 append한다.
  원문 artifact·Alias·SQL·exception 문자열을 저장하지 않고 attempt_id와 고정 reason 및 비민감 참조만 기록한다.
- 승인/구성원이 rollback된 경우 존재하지 않는 FK를 만들지 않는다. nullable 대상 FK와 attempt 식별자로 기록한다.
- 감사 자체가 실패하면 원래 작업을 성공으로 바꾸지 않는다. 원래 실패와 감사 실패를 구분해 운영자에게 전달하고,
  stable attempt_id로 감사 재시도할 수 있게 한다. 새 큐/Outbox를 자동 신설하지 않는다.
- commit 응답이 끊겨 성공 여부가 불명확하면 확정 승인 ID/request_id/Set key로 새 connection에서 재조회한다.
  확인 전에는 BUILD_FAILED 확정 기록이나 재발급을 하지 않는다. 확인 결과에 따른 이벤트를 별도로 남긴다.
- 별도 감사 transaction 방식은 프로세스 종료 순간의 감사 유실 가능성이 있다. 전수 실패 감사가 요구되면
  작업 전 attempt 기록 등 내구성 경계를 추가 설계해야 하며, 현재 안으로 무손실 감사까지 주장하지 않는다.

## 7. 최소 권한·이행

- Catalog Writer는 승인 payload/권한을 발급·수정할 수 없다. 조회·검증 잠금에 필요한 최소 권한만 허용한다.
- 잠금에 UPDATE 권한이 필요하면 기존 lock marker 패턴처럼 CHECK로 고정된 전용 컬럼을 검토한다.
  승인 상태 컬럼 전체 UPDATE 권한을 Writer에 주지 않는다. 구체 컬럼 추가는 migration 리뷰 대상이다.
- 승인 운영자는 업무 승인 권한을 검사하는 서비스/명령만 사용하고, 권한 grant는 분리된 운영 경계에서 수행한다.
- 감사 writer는 append-only INSERT, consumer는 허용된 SELECT만 사용한다. 직접 DML 우회와 역할 상속을 검증한다.
- 기존 #398 권한·감사 정책을 임의 수정하지 않는다. 별도 구조 비용을 줄일 재사용 가능성이 있으면 의미·제약 대조 후 결정한다.
- 현재 Set/receipt에서 승인 주체·기간·근거를 추론해 backfill하지 않는다. 증빙 없는 과거 Set은 승인된 것으로 노출하지 않는다.
- 적용된 migration 이력은 보존한다. 새 revision은 구현 시 최신 develop head 뒤에 연결하고 데이터가 있는 downgrade는 손실 없이 처리 가능할 때만 허용한다.
- 기본 만료 기간, 증빙 보관 위치·보존/삭제 정책, 허용 승인자 명단은 임의 생성하지 않는다. 운영 발급 전에 확정한다.

## 8. 구현 순서·확인 항목

1. 은영님: 전용 5개 저장 역할과 기존 #398 재사용 경계, 동일-session 검증·잠금·최소 권한·감사 실패 처리 검토.
2. PM/실제 승인 주체: 승인 용도·권한·유효기간·근거·보존 정책 확인. 권한을 부여하는 사람이 내용의 승인자와 같다고 가정하지 않는다.
3. 정현우: 기존 receipt 고정·재승인 시 새 Set·Candidate/Runtime 사용 확정 시점과 구버전 처리 확인.
4. 위 결과를 Decision과 계약에 반영하고 migration·repository·관리 명령·실제 verifier를 구현한다.
5. save/load와 실제 소비의 같은-session 또는 재검증 경계를 연결하고 아래 테스트 증빙을 PR에 첨부한다.

D-02·D-05 완료를 일괄 선행 조건으로 두지 않는다. 현재 v2·관찰 v3 대상만 연결하고 미확정 실행/hash 필드를 만들지 않는다.
MFDS 상세 수집은 별도 Source 생산 경계이며 이 승인 저장소가 API 수집기를 대신하지 않는다.

## 9. 검증 계획 — 미실행

- 타인/무권한/철회된 운영자 명령 차단, 권한 재부여 없이 self-grant 불가.
- export bytes/checksum·schema·전체 Source 집합·Source receipt 불일치 및 누락/초과 출처 거부.
- 유효기간 시작/종료 경계·Catalog 철회·Source 철회·Source 부적격 각각 거부.
- 새 승인 발급 후 과거 manifest receipt 자동 치환 0건, 같은 승인 재조회 bytes 일치.
- 명령 멱등성·동시 발급·상충 fingerprint·철회 중 재시도 검증.
- 실제 2개 DB connection으로 save↔철회, load/소비↔철회, Source 상태 변경 경합과 잠금 순서 검증.
- 승인/Set 중간 실패·성공 감사 실패 rollback, 실패 감사의 별도 commit, 감사 실패 보고 검증.
- commit 불명확 상황에서 확정 키 조회 후 판단, 중복 승인·거짓 실패 기록 0건.
- DB 최소 권한·append-only 감사·직접 승인 변경 금지·역할 상속 검증.
- 기존 v2·관찰 v3 export/Candidate 회귀, migration upgrade/downgrade·기존 무증빙 데이터 처리 검증.

이번 작업에서는 Markdown·참조·diff만 검증한다. 위 시나리오나 실제 DB/권한 검사를 실행한 것으로 표시하지 않는다.

## 코드 근거

- [승인 포트·receipt](../../../../ai_worker/tasks/rag/catalog/approval.py)
- [현재 receipt 비교 복원](../../../../ai_worker/tasks/rag/catalog/restore.py)
- [Catalog transaction adapter](../../../../ai_worker/adapters/sqlalchemy_catalog_write_support.py)
- [기존 관리 권한·감사 모델](../../../../backend/app/models/source_management.py)
- [관리 서비스 잠금·감사](../../../../backend/app/admin/source_management_service.py)
- [권한 부여 운영 명령](../../../../backend/app/admin/source_management_permissions.py)
- [Source 모델](../../../../backend/app/models/rag_source.py)
- [Runtime 평가 승인 모델](../../../../backend/app/models/rag_runtime.py)
