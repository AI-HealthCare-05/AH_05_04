# D-04 Component 관찰 출처·인계 계약 변경안

- 상태: Proposed — 구현 구체안에 대한 정현우 담당 범위 리뷰 승인 기록 연결. 병합·전문 검토·운영 승인은 별도다.
- 날짜: 2026-09-13
- Issue: #166, 선행 PR: #464
- 구현: 김지혜. 담당 리뷰어: 정현우. DB·migration 전문 검토: 송은영.

## 합의와 검토 근거

- 최초 방향 합의: [D-04 실제 MFDS Loader 연결 답변](https://discord.com/channels/@me/1545029651477307442/1548581753495494657). 사용자 제공 원문 링크이며 DM 접근 권한이 필요하다.
- 공개 구현 검토: [PR #477 정현우 승인 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/477#pullrequestreview-5190458759), 검토 HEAD `c58f09686d3a72567793ee237abeb500ab04e710`.
- 확인 범위: MFDS 매핑 의미·한계, 반복 성분·총량 그룹 보존, 출처 분리, Candidate 인계.
  리뷰에서 UNIQUE·FK·migration 및 v2 호환 검증을 함께 확인했다. 이를 송은영의 별도 전문
  검토나 MFDS 공식 의미·실수집·Runtime 활성화 승인으로 확대하지 않는다.

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
