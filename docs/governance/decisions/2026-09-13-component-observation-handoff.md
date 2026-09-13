# D-04 Component 관찰 출처·인계 계약 변경안

- 상태: Proposed — 현우님 방향 동의에 따라 구현한 구체안, PR 승인 전.
- 날짜: 2026-09-13
- Issue: #166, 선행 PR: #464
- 구현: 김지혜. 담당 리뷰어: 정현우. DB·migration 전문 검토: 송은영.

## 문제와 결정 제안

기존 Component의 단일 Snapshot FK와 제품별 순서 UNIQUE로는 제품 목록과 상세 출처를
각각 보존하거나 같은 제품의 상세 재수집 이력을 함께 저장할 수 없다. 기존 v2는 총량 그룹을
표현하지 못한다. 출처를 재라벨링하거나 FK를 제거하는 방식은 사용하지 않는다.

Component 자체 상세 출처, Product 참조 출처, Ingredient 참조 출처를 분리하고 각 실제 행을
composite FK로 결속한다. 관찰 버전은 상세 Snapshot으로 구분한다. Python 저장·복원·Candidate
검증이 원료코드·품목코드·출처·원문 함량의 일치를 검사한다. 관찰에 상세 checksum·canonicalization
버전을 보존하고, 저장 잠금 이후 및 조회 시 실제 DB Receipt와 대조해 원문을 결속한다.

관찰 메타데이터가 있는 자료는 `medication-catalog-v3`, 없는 기존 자료는 v2로 내보낸다.
기존 v2 golden bytes와 envelope 계산 규칙을 보존한다. 별도 projection/runtime hash가 아니다.
MFDS 상세 입력 canonical JSON은 `mfds-component-observations-v1` 내부 제안으로 구분한다.
Source 자동 수집·공식 순서 확정은 포함하지 않는다.

기존 두 참조 출처만 기존 FK 근거로 이행하며 과거 총량·원본 키를 추론하지 않는다.
Migration `166f30415263`의 downgrade는 출처·관찰 버전·원문 정보가 손실되면 거부한다.
신규 RLS·Trigger·DB 업무 함수를 추가하지 않는다.

## 유지보수와 검증

추가되는 두 FK 열과 관찰 JSON, v2/v3 호환 검증이 유지보수 대상이다. 기존 저장 transaction,
Source Receipt 검증, 승인 포트 및 Candidate builder를 재사용하며 새 실행 모델을 만들지 않는다.

[계약과 필드](../../contracts/proposed/post-mvp-1/catalog-db-integration-v2.md),
[구현 범위·실입력 조건](../../designs/jye-rookie/issue-166-d04-loader-handoff.md),
[검증 결과](../../testing/mfds-loader-handoff-166.md)를 함께 리뷰한다.
