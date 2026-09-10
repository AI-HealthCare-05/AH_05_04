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

## 내용 hash v1 (저장 직후 대조 구현)

`provider_contracts.prescription_integrity.prescription_fingerprint()`는 `medication_count`와 SHA-256을 반환한다. `spec=prescription-content@1`, ISO prescribed_date, display_order 오름차순 medications를 JSON object로 직렬화한다. JSON은 key 정렬·공백 없는 separator·UTF-8·ensure_ascii=False이며 NaN을 허용하지 않는다.

약 필드는 display_order, medication_name, strength_text, dose_value, dose_unit, frequency_per_day, timing_text, duration_days이다. 생략한 optional 필드는 null로 취급한다. null과 빈 문자열은 구분한다. 문자열의 공백·Unicode를 hash 함수에서 정규화하지 않으며 정확한 저장 값을 사용한다. dose_value는 유한한 양수·Numeric(10,3) 범위를 확인하고 소수 셋째 자리 문자열로 표현한다. 반올림이 필요한 입력은 거부한다. 날짜·내용 hash이므로 DB surrogate ID와 생성 시각은 포함하지 않는다.

Repository는 저장 전 입력을 검증하고 실제 저장 열들을 다시 읽어 입력 fingerprint와 비교한다. DB에 hash를 영구 저장하는 단계는 아직 아니며, 이 비교만으로 저장 이후의 불변성이 보장되지는 않는다. count/hash 컬럼 및 소비 검증은 후속 migration과 함께 연결한다.

정정 DTO도 display_order 1..N을 강제하여 간격이 있는 요청은 기존 validation 응답(422)으로 거부한다. API가 통과시킨 입력이 Repository의 구성 검증에서 500으로 실패하지 않도록 경계를 일치시킨다.

## Fingerprint 확장 migration

398a1b2c3d4e는 부모 medication_count/content_hash와 약 행 medication_count를 추가한다. 부모 (id, medication_count) UNIQUE, 자식 (version_id, medication_count) FK 및 1..count 슬롯 CHECK를 추가하고 기존 버전별 display_order UNIQUE를 유지한다. Repository는 새 버전과 모든 약 행에 해당 값을 명시한다.

기존 버전은 Python v1 hash로 검증·backfill한다. prescription→version→medication의 배타 잠금을 잡은 transaction에서 기존 UPDATE 방지 Trigger만 잠시 중지하고, metadata를 채운 뒤 원래 활성 상태를 복원한다. 새 Trigger를 생성하지 않는다. 잘못된 기존 데이터는 원문을 출력하거나 자동 수정하지 않고 전체 DDL·backfill을 rollback한다. downgrade는 기존 UPDATE 보호가 활성화되어 있을 때만 재계산 가능한 확장 metadata를 제거한다.

아직 nullable인 확장 단계다. 이전 생산자가 null을 기록할 수 있으므로 이 migration만으로 슬롯 봉인 완료를 주장하지 않는다. 모든 생산·소비 경로 전환과 잔여 null 재검증 뒤 NOT NULL 강화가 필요하다. 기존 assembly_xid·불변성 Trigger는 유지하며 운영 배포 전환은 미완료다.
