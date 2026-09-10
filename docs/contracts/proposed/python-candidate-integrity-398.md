# PD-398: Python Candidate 결과 저장

상태: 부분 구현. DB 권한·Trigger 제거 migration·최종 통합 검증 대기.
구현: 김지혜. 검토: 송은영(Backend·DB), 정현우(Candidate), 권가빈(제품 수용).

최종화 Service는 기존 소유권 및 Prescription→약→Search 잠금과 payload 검증 후 Repository의 assemble_and_finalize_search를 호출한다. Repository는 결과 INSERT와 최종화 UPDATE를 하나의 savepoint로 묶는다. 최종화가 실패하고 호출자가 오류를 잡아 외부 transaction을 commit하더라도 부분 결과는 남지 않는다.

결과 추가·최종화는 같은 Search 부모 잠금을 취득하고 DB의 RUNNING 상태를 다시 확인한다. 최종화는 입력 개수 대신 실제 저장된 전체 결과·표시 결과 수를 집계해 대조한다. 성공 후 중복 최종화는 기존 Service의 stale 응답을 유지하며 새 결과를 추가하지 않는다. 실패 후 RUNNING 상태에서 재시도할 수 있다.

낮은 수준의 add_results/finalize_search를 따로 호출하는 도구는 외부 업무 transaction을 책임져야 한다. 사용자 최종화 경로는 결합 메서드에 연결했다. 일반 FK·UNIQUE·CHECK는 유지한다. 직접 DB DML 권한 제한 및 최종 migration 후 전체 결과 불변성 검증은 아직 남아 있다.
