# PD-398: Python Prescription 무결성

상태: 부분 구현. count/hash·슬롯 제약·권한·제거 migration 및 리뷰 대기.
구현: 김지혜. 검토: 송은영(Backend·DB), 정현우(후속 소비), 권가빈(제품 수용).

## 저장 경계

Repository의 최초 저장·새 버전 저장 모두 약이 최소 1개이고 display_order가 중복·누락 없는 정수 1..N인지 저장 전에 확인한다. 실제 INSERT 후 저장된 순서를 다시 조회해 전체 구성을 확인한다. 새 버전 번호 계산 전에 prescription 부모 행을 잠근다. Service의 base_version_id와 expected_revision 비교는 같은 부모 잠금 안에서 유지한다.

버전·약 목록·활성 포인터 저장을 savepoint로 묶는다. 약 INSERT나 구성 검증 실패를 호출자가 잡더라도 새 버전만 남지 않는다. 최초 처방의 비어 있지 않은 active_version_id 및 deferred FK 계약은 유지하며 외부 transaction commit 때 완성된 관계가 검증된다. 기존 버전 정정에서는 약 저장·검증을 마친 뒤 포인터를 변경한다.

Service에서 수행하는 기존 버전의 Job·일정·Outbox 무효화를 포함한 전체 업무 transaction은 호출자가 rollback해야 한다. Repository savepoint는 앞서 수행한 다른 Service 작업의 부분 commit을 허용한다는 의미가 아니다.

## 남은 전환 조건

- 부모 medication_count 및 canonical hash, 자식 count 결속·슬롯 제약과 기존 데이터 검증
- hash 직렬화 계약 및 활성화·소비 시 공통 count/hash 검증
- DB unique 기반 요청 멱등성과 동시 정정 회귀 검증
- 불변 테이블 UPDATE·DELETE·TRUNCATE 제한, Writer 실행 경로
- 기존 assembly_xid·Trigger·함수의 forward migration 제거

과거 migration은 변경하지 않는다. 이 부분 구현만으로 Trigger 제거·배포를 진행하지 않는다.
