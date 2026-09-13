# PD-398: Python append-only 이력

상태: 기존 불변성 Trigger 제거·최소 권한·Check-in 및 Runtime 전이 원자 저장 구현. 최종 리뷰 대기.

398d4e5f6071은 Runtime 환경 전이, Check-in 감사, Evidence·Citation 이력 테이블을 배타 잠근 뒤 소유자 외 권한을 모두 회수한다. 이어서 기존 append-only Trigger 12개와 함수 3개를 제거한다. 예상 밖 Trigger나 함수 의존성이 있으면 `CASCADE`로 지우지 않고 migration 전체를 rollback한다.

역할 provisioning 뒤 Runtime 계정은 이력 테이블에 SELECT·INSERT만 가능하다. Writer 계정은 접근할 수 없다. 실제 분리 로그인으로 정상 INSERT와 UPDATE·DELETE·TRUNCATE 거부를 검증한다. 테이블 소유자는 migration과 장애 복구 경계로 취급하며 일반 애플리케이션 credential로 사용하지 않는다.

Check-in 정정은 이전 상태 감사 INSERT와 현재 상태·revision UPDATE를 Repository savepoint로 묶는다. flush 실패를 호출자가 잡아 외부 transaction을 계속하더라도 감사만 남거나 현재 상태만 바뀌지 않는다. 실패 후 같은 세션에서 재시도할 수 있고 감사 행은 정확히 하나만 생성된다.

Evidence·Citation은 기존 승인된 Python 생성 경로로만 append한다. 보호 이력을 변경하거나 삭제하는 업무 경로는 제공하지 않는다. 이 migration은 downgrade 시 과거 Trigger를 되살리지 않으며, 되돌림은 검토된 forward-fix 또는 적용 전 백업 복구로 수행한다.

## Runtime 환경 전이 경계

Runtime 포인터와 상태는 `RagRuntimeEnvironmentTransitionService`를 통해 변경한다. 호출자는 인증된 actor와 앞서 평가한 Guard Decision 참조를 전달해야 한다. 이 단계는 Guard 엔진이나 외부 API를 새로 구현하지 않으며, 비어 있지 않은 Guard 참조와 actor를 요구해 내부 호출 경계를 고정한다.

Repository는 같은 transaction에서 환경 행을 `FOR UPDATE`로 잠근 뒤 호출자가 관찰한 아래 값을 현재 DB 값과 exact-match한다.

- `environment_revision`
- `safety_epoch`
- 활성 Bundle ID와 Manifest Hash 쌍
- `governance_revision_ref`

대상 Bundle도 잠근 뒤 ID·Manifest Hash, `READY` 상태와 환경의 Governance Revision을 다시 검사한다. `EMERGENCY_ROLLBACK`은 과거 적격 후보를 대상으로 할 때 `RETIRED`도 허용한다. 검사가 끝난 뒤 환경 포인터·상태·revision 증가와 append-only 전환 Event를 한 transaction에서 flush한다. Event flush가 실패하면 환경 변경도 함께 rollback한다.

지원 상태 규칙은 다음과 같다.

- `PLANNED_ACTIVATION`: 적격 대상 Bundle로 교체하고 환경을 `ACTIVE`로 둔다. 검증 실패 시 기존 포인터와 상태를 유지한다.
- `EMERGENCY_ROLLBACK`: 적격 대상이 있으면 포인터를 교체한다. 적격 후보가 없다는 Guard 결과로 대상을 생략하면 기존 포인터를 보존하고 환경을 `SUSPENDED`로 전환한다.
- `SUSPEND`: `ACTIVE` 환경만 중지하며 활성 포인터는 보존한다.
- `RESUME`: 보존된 활성 Bundle을 다시 검증한 뒤 `SUSPENDED` 환경만 `ACTIVE`로 전환한다.

모든 지원 writer가 같은 환경 부모 행을 먼저 잠그므로 이 경계에는 `SERIALIZABLE` 격리가 필요하지 않다. 같은 expected revision으로 경쟁한 요청은 첫 transaction만 성공하고, 뒤 요청은 잠금 획득 뒤 revision 불일치로 실패한다. 398f60718293 migration은 기존 중복 revision이 있으면 적용을 거부한 뒤 `(environment_id, environment_revision)` UNIQUE를 추가해 이 규칙을 스키마에도 봉인한다. 이 migration은 Trigger, PL/pgSQL 함수 또는 RLS를 만들지 않는다.

활성화 직후 DB 환경 포인터를 Identification Preflight의 observed Runtime Bundle로 사용했을 때 이전 Bundle에 고정된 요청은 `RUNTIME_RELEASE_STALE`로 fail-closed되는 회귀 테스트를 유지한다.
