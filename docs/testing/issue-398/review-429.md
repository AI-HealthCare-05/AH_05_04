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

## 3. Snapshot 삭제 방어와 병합된 #404

- #404 병합 commit `2922c25`를 포함했다. 최종 #404에는 새 Trigger 정의가 없으며 인증 Python 경로를 유지한다.
- merge revision `39818293a4b5`와 seal revision `398293a4b5c6`을 추가했다. #404/#398 어느 기존 head에서 시작해도 단일 head로 전환하며 과거 검증 상태·내용을 보존하는 것을 테스트했다.
- 실제 management 로그인으로 CURRENT/STALE/FAILED 직접 DELETE가 FK 위반으로 차단된다. 미검증·미참조 PENDING 관리 API 삭제는 성공한다. 다른 Snapshot의 seal 차용, seal 제거, Verification 삭제·TRUNCATE도 거부한다.
- Runtime의 인증 테이블 권한은 SELECT/INSERT와 rotation/used_at 컬럼 UPDATE로 한정했다. 실제 제한 계정의 signup/login/rotation/reset/relogin과 재설정 token 재사용 거부를 검증했다.
- 관련 Source/Writer/배포 역할/정적 정책·head 테스트 90개 통과. 인증 실제 역할과 #404 migration 별도 재검증 5 passed. Backend RAG와 Source/관리 통합 묶음은 107 passed, 1 skipped(명시 컨테이너 환경이 필요한 bootstrap; 이후 명시 환경으로 별도 통과).
- 과거 revision 테스트는 해당 시점의 컬럼·fixture 권한을 사용한다. 최신 ORM·배포 provisioning은 head 이후에만 실행한다. 새로운 production bypass 옵션은 추가하지 않았다.
- Ruff 전체 및 mypy Backend/Worker 통과. AWS·운영 DB에는 적용하지 않았다.

## 최종 회귀 검증

최종 코드와 병합된 #404를 기준으로 다음을 실행했다. 서로 중복될 수 있는 테스트 묶음은 합산하지 않는다.

| 검증 | 결과 |
| --- | --- |
| Backend 전체·계약·RAG/PostgreSQL 통합·실제 Backend 이미지 | 1,716 passed, 59 skipped |
| 별도 폐기 DB Source cleanup (위 skip 중 57개) | 57 passed |
| Worker 단위·OCR·RAG·Evaluation | 2,713 passed, 8 skipped |
| PostgreSQL 전체 migration | 154 passed |
| 별도 임시 Redis와 Outbox·Worker 실행/복구 | 23 passed |
| 최종 head `398293a4b5c6` 및 DB 카탈로그 | 사용자 Trigger·RLS·제거 대상 함수 0개 |
| Ruff·format·mypy | 통과, Backend/Worker 534개 파일 |
| DB 로직 재도입·보호 테이블 쓰기·test inventory | 통과 |

Backend의 나머지 skip 2개는 실제 외부 Provider 호출이 필요한 smoke다. Worker skip 8개는 선택 의존성 `jsonschema` 부재에 따른 검사다. 외부 Provider live 실행, AWS·운영 DB 적용, 원격 CI/담당 리뷰 승인을 로컬 테스트 통과로 대체하지 않는다.

종합 검증 중 발견한 합성 one-cycle 및 과거 migration fixture의 멱등성 기록 정리 누락을 보완했다. Source target 문서 수정에 맞춰 합성 계약 receipt의 local target hash와 canonical hash를 재생성했고, 외부 승인·공개 게이트 상태는 변경하지 않았다. 정적 검사와 실제 DB 권한 검증의 보장 범위를 문서에 구분했다.
