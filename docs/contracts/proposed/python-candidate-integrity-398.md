# PD-398: Python Candidate 결과 저장

상태: 저장 원자성·최소 권한·Trigger 제거 구현. 최종 리뷰 대기.
구현: 김지혜. 검토: 송은영(Backend·DB), 정현우(Candidate), 권가빈(제품 수용).

최종화 Service는 기존 소유권 및 Prescription→약→Search 잠금과 payload 검증 후 Repository의 assemble_and_finalize_search를 호출한다. Repository는 결과 INSERT와 최종화 UPDATE를 하나의 savepoint로 묶는다. 최종화가 실패하고 호출자가 오류를 잡아 외부 transaction을 commit하더라도 부분 결과는 남지 않는다.

결과 추가·최종화는 같은 Search 부모 잠금을 취득하고 DB의 RUNNING 상태를 다시 확인한다. 최종화는 입력 개수 대신 실제 저장된 전체 결과·표시 결과 수를 집계해 대조한다. 성공 후 중복 최종화는 기존 Service의 stale 응답을 유지하며 새 결과를 추가하지 않는다. 실패 후 RUNNING 상태에서 재시도할 수 있다.

낮은 수준의 add_results/finalize_search를 따로 호출하는 도구는 외부 업무 transaction을 책임져야 한다. 사용자 최종화 경로는 결합 메서드에 연결했다. 일반 FK·UNIQUE·CHECK는 유지한다.

398e5f607182는 Candidate 부모와 결과 테이블을 배타 잠근 뒤 모든 검색의 `candidate_count`와 `displayed_candidate_count`를 실제 결과 행에서 집계해 검증한다. 불일치가 있으면 migration 전체를 rollback한다. 검증 후 기존 Trigger 2개와 count 함수 1개를 제거한다.

Runtime 역할은 Search 부모를 변경할 수 있고 결과 행에는 SELECT·INSERT만 가능하다. 생성·최종화·선택은 변경 가능한 Search 부모를 잠금 기준으로 사용하므로 불변 결과 행에 UPDATE 권한이 필요하지 않다. 실제 제한 역할 로그인으로 조회와 선택이 성공하고 결과 행 UPDATE·DELETE·TRUNCATE가 거부되는 것을 통합 검증한다.

이 migration은 downgrade 시 과거 Trigger를 다시 만들지 않는다. 되돌림은 검토된 forward-fix 또는 적용 전 백업 복구로 수행한다.
