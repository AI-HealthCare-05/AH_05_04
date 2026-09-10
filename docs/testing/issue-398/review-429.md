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


## 추가 승인 리뷰와 #412 통합 (2026-09-10)

- 가빈님의 `5617339390` 의견은 새 BLOCKER/MUST FIX 없음과 승인 권고이며 GitHub 승인 제출은 아니다. 은영님의 `5166769939`는 DB·보안 범위의 APPROVED다. 두 의견 모두 기존 Python 경계·FK/CHECK·실제 권한 검증을 수용하며 새 업무 저장 함수 도입을 요구하지 않는다.
- 원격 PR head `3e15afa`를 보존하고 #412가 병합된 develop `23b3b59`를 통합했다. #416은 이 통합에 포함되지 않는다.
- schema 문서 충돌은 #412 Context 설명과 #398 Python 전이 설명을 함께 보존했다. 과거 Trigger 강제 서술은 복원하지 않았다.
- 기존 migration은 수정하지 않고 `3983a4b5c6d7`로 `398293a4b5c6`과 `174a1b2c3d4e`를 연결했다. 새 revision은 이력 연결만 수행한다.
- Context 3개 테이블은 Runtime SELECT·INSERT와 기존 Python Repository를 연결했다. 직접 UPDATE·DELETE·TRUNCATE와 Source Writer 접근은 거부한다. 기존 부모 Job CASCADE와 후속 API·Worker·공개 범위는 변경하지 않았다.
- #412 과거 downgrade 회귀는 별도 폐기 DB에서 실행한다. 두 번째 약물을 직접 추가하던 fixture는 정식 처방 Repository로 완전한 Version을 생성하도록 변경했다.

통합 후 로컬 검증 결과(서로 중복될 수 있으므로 합산하지 않는다):

| 검증 | 결과 |
| --- | --- |
| #412·Runtime·Source 관리·실제 제한 역할·head 회귀 | 56 passed |
| Backend 전체·계약·RAG/PostgreSQL 통합·실제 Backend 이미지 | 1,720 passed, 59 skipped |
| Worker 단위·OCR·RAG·Evaluation | 2,713 passed, 8 skipped |
| 전체 migration | 158 passed |
| 최종 head `3983a4b5c6d7` 및 DB 카탈로그 | 사용자 Trigger·RLS·제거 대상 함수 0개 |
| Ruff·format·mypy·재도입/보호 쓰기/test inventory | 통과, mypy 535개 파일 |

Backend skip은 별도 Source cleanup 환경 57개와 외부 Provider live smoke 2개이며, Worker skip 8개는 선택 의존성 `jsonschema` 부재다. 이전 단계의 별도 Source cleanup·Redis 검증 수치를 이번 재실행 결과로 표기하지 않는다. AWS·운영 DB에는 적용하지 않았다.


## 현우님 추가 리뷰: Snapshot 잠금 권한과 규칙 정합성

- 리뷰 `5166896553`의 최신 develop 요구는 `54a10a0`에서 #412를 포함하고 충돌을 해결했다.
- `verified_at`을 관리 잠금용으로 허용하던 우회를 제거했다. `3984b5c6d7e8`는 0만 허용하는 `management_lock_marker` 컬럼과 일반 CHECK를 추가한다. 새 Trigger·RLS·업무 함수는 정의하지 않는다.
- 관리 역할의 Snapshot UPDATE는 표식만 허용한다. provisioning은 기존 verified_at 컬럼 권한을 회수하며 시작 시 역할 검증도 해당 권한이 남아 있으면 거부한다. 기존 Writer 전이와 Operation → Snapshot 잠금 순서는 유지한다.
- 표식을 관리 hash에서 제외해 migration 전후 기존 expected_hash와 감사 의미를 보존한다. 실제 제한 로그인으로 CURRENT/STALE/FAILED의 verified_at 변경·seal 변경·직접 DELETE를 거부하며, 고정 표식 변경도 거부하고 미사용 PENDING 삭제는 유지한다.
- PD-398-R2, 관리·Source 계약, schema, 인수 절차와 head 검사를 함께 갱신했다. AGENTS와 CONTRIBUTING은 승인 계약으로 Trigger·RLS·업무 저장 함수 금지를 예외 처리하지 않는 동일한 규칙을 사용한다.

최종 추가 수정 로컬 검증 결과:

| 검증 | 결과 |
| --- | --- |
| Backend 전체·계약·RAG 통합·실제 Backend 이미지 | 1,720 passed, 59 skipped |
| Worker 단위·OCR·RAG·Evaluation | 2,713 passed, 8 skipped |
| 전체 migration | 158 passed |
| 별도 폐기 DB Source cleanup (Backend skip 중 57개) | 57 passed |
| 최종 head `3984b5c6d7e8` 및 DB 카탈로그 | 사용자 Trigger·RLS·제거 대상 함수 0개 |
| Ruff·format·mypy·재도입/보호 쓰기/test inventory | 통과, mypy 535개 파일 |

테스트 묶음은 중복될 수 있어 합산하지 않는다. Backend의 나머지 skip 2개는 외부 Provider live smoke이고 Worker skip 8개는 선택 의존성 `jsonschema` 부재다. 이 결과는 운영 적용·공개 승인이나 수정 후 지정 리뷰어 승인을 대신하지 않는다.
