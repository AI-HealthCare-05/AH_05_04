# PR #429 리뷰 수정 증빙

## 1. Runtime 최신 상태와 Source 수집 권한

- Runtime Environment·Bundle 행 잠금에서 ORM에 이미 있던 객체도 최신 DB 값으로 갱신한다. 두 세션으로 사전 로드 후 상태·revision 변경을 재현해 잘못된 활성화가 거부됨을 확인했다.
- 전이 종류 4개를 명시하고, 미지원 종류는 서비스와 Repository에서 거부한다. 미래 enum의 기본 활성화 경로는 없다.
- Source 수집은 Source별 내장 transaction advisory lock으로 직렬화한다. 사용자 정의 DB 함수나 Trigger는 추가하지 않는다. 실제 Writer 로그인에서 Source UPDATE 권한 없이 잠금·저장·재실행 및 동시 실행 거부를 확인했다.
- 검증: Runtime Repository, Source Snapshot lifecycle, Snapshot adapter 단위 테스트 **67 passed**.

이 문서는 단계별 검증 기록이다. 처방 멱등성, Snapshot 직접 삭제 방어, 문서 정합성과 병합된 #404 통합 검증은 다음 단계에서 기록한다. AWS·운영 DB에는 적용하지 않았다.

## 2. 처방 성공 재시도

- SYNC_MUTATION 공통 경계에 확정·정정을 연결했다. 같은 요청 재현, 다른 내용 충돌, 소유권 재확인, 동시 정정의 버전 중복 방지, 응답 저장 실패 rollback을 검증했다.
- 기존 처방 Service/API/동시성 테스트 47 passed. 추가 회귀를 포함한 Service·동시성 재검증 19 passed.
- OpenAPI operation_id와 저장 scope가 일치함을 확인했다. 필수 헤더·DTO는 추가하지 않는다. PD-398-R1에 응답 의미 변경과 지정 리뷰 범위를 기록했다.
