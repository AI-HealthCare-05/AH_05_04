# #372 Catalog v2 마무리 범위와 담당자 확인

- 구현 담당: 김지혜. PR 책임 리뷰어: 송은영(`phina-io`) 1명.
- 근거: 김지혜가 제공한 2026-09-11 12:02 Discord 답변 캡처. 저장소 댓글 URL은 제공되지 않았다.
- 상태: D-03a 조건부 동의·Crosswalk 범위 방향 동의. 이 기록은 최종 코드 승인이나 공개 승인이 아니다.

## 확인된 범위

송은영은 D-03a Alias schema 전환을 #166 / PR #372에 포함하는 데 동의했다.
기존 `rag_medication_alias` 행이 한 건이라도 있으면 migration은 fail-closed로 중단한다.
`is_approved`를 `PENDING` / `REJECTED` / `APPROVED`로 추론·백필하지 않는다.
`target_identity_id`, `alias_source`, `review_status`, `record_status`, `is_effective`를
migration·ORM·테스트·문서에서 함께 유지한다. Catalog 적재를 위한 schema 전환이며,
Runtime 활성화나 Candidate Search 공개 계약 완료를 뜻하지 않는다.

정현우는 지혜의 제안 방향으로 진행하는 데 동의했고, 구현 후 현재 P0 Candidate 입력 기준의
범위 충족 여부를 PR에서 확인하겠다고 답했다. 이번 구현에서는 승인 매핑 범위가 없는
Crosswalk / Crosswalk Set을 만들지 않고 후속으로 유지한다. 공식 코드를 복사하여 매핑을
추론하거나 빈 READY Set을 만들지 않는다. 방향 동의와 구현 완료 후 의미 검토를 구분한다.

## 이번 PR 구현 경계

- 기존 v2 export/envelope 계약, Source Receipt 검증, Catalog Set/member/hash DB 저장·복원,
  Candidate 인계와 별도 Catalog Writer 로그인 연결까지 포함한다.
- D-02는 기존 미확정 상태를 유지한다. 합의된 결정 기한은 없으며 이번 PR의 새 차단 사안으로
  재요청하지 않는다. ingestion ID나 hash로 normalization run을 대신하지 않는다.
- 실제 승인·철회·감사 저장소, 실행/Publication 모델, Crosswalk와 새 projection/Runtime hash는 후속이다.
- PR 병합만으로 #166 전체를 자동 종료하지 않는다.

## Writer 구현 검토 사항

사용자가 요청한 Writer 연결을 위해 Source Writer·Runtime·관리 역할과 분리된 Catalog 로그인을
사용한다. 기존 Source 잠금 marker 방식을 재사용해 Product/Alias에 `catalog_lock_marker`
정수 컬럼을 추가한다. 기본값과 CHECK는 0이며 업무 상태를 나타내지 않는다. PostgreSQL의
`SELECT FOR UPDATE` 권한 요건을 충족하면서 원문·검토 상태 UPDATE 권한을 부여하지 않기 위함이다.
Snapshot은 기존 `management_lock_marker`를 재사용한다. Python 저장소의 row lock 순서는 유지한다.
이 Writer 세부 구현은 이번 PR 책임 리뷰 대상이며 Discord에서 세부 코드까지 승인받았다고 해석하지 않는다.

신규 RLS·Trigger·업무 DB 함수는 없다. Python 검증과 transaction, 일반 FK·UNIQUE·CHECK 및
최소 권한을 적용한다. 저장 성공이 승인·CURRENT·Runtime 활성화를 발생시키지 않는다.
