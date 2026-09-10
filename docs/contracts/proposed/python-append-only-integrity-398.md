# PD-398: Python append-only 이력

상태: 기존 불변성 Trigger 제거·최소 권한·Check-in 원자 저장 구현. 최종 리뷰 대기.

398d4e5f6071은 Runtime 환경 전이, Check-in 감사, Evidence·Citation 이력 테이블을 배타 잠근 뒤 소유자 외 권한을 모두 회수한다. 이어서 기존 append-only Trigger 12개와 함수 3개를 제거한다. 예상 밖 Trigger나 함수 의존성이 있으면 `CASCADE`로 지우지 않고 migration 전체를 rollback한다.

역할 provisioning 뒤 Runtime 계정은 이력 테이블에 SELECT·INSERT만 가능하다. Writer 계정은 접근할 수 없다. 실제 분리 로그인으로 정상 INSERT와 UPDATE·DELETE·TRUNCATE 거부를 검증한다. 테이블 소유자는 migration과 장애 복구 경계로 취급하며 일반 애플리케이션 credential로 사용하지 않는다.

Check-in 정정은 이전 상태 감사 INSERT와 현재 상태·revision UPDATE를 Repository savepoint로 묶는다. flush 실패를 호출자가 잡아 외부 transaction을 계속하더라도 감사만 남거나 현재 상태만 바뀌지 않는다. 실패 후 같은 세션에서 재시도할 수 있고 감사 행은 정확히 하나만 생성된다.

Runtime 전이와 Evidence·Citation은 기존 승인된 Python 생성 경로로만 append한다. 보호 이력을 변경하거나 삭제하는 업무 경로는 제공하지 않는다. 이 migration은 downgrade 시 과거 Trigger를 되살리지 않으며, 되돌림은 검토된 forward-fix 또는 적용 전 백업 복구로 수행한다.
