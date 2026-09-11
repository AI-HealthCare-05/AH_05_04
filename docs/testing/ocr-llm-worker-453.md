# #453 OCR LLM Worker 연결 검증과 인계

## 이번 변경

기존 Backend의 OCR LLM client·prompt·schema·grounding validator를 공용 패키지로
이동하고 호환 import를 유지했다. 실제 Worker factory에서 feature flag 활성화 시
CLOVA 다음 LLM 구조화를 실행한다. 검수 payload의 형태와 약물 추가·수정·확정 API는 유지한다.

LLM client retry는 0이고 기존 Worker attempt 재시도를 사용한다. 외부 실행은 Worker의
전체 deadline 안에 있으며 client를 성공·실패·취소 시 닫는다.
DB 결과 저장과 ACK는 기존 Service/Repository의 fencing·transaction을 재사용한다.
DB schema·RLS·Trigger·업무용 DB 함수는 추가하지 않았다.

## 집중 증빙

- Backend OCR LLM client·validator·structurer: 67 passed
- Worker OCR·runtime 조립: 64 passed
- Backend 설정·credential 없는 subprocess의 공용 LLM import: 통과
- Redis·PostgreSQL one-cycle 두 경로: 2 passed (규칙 기반, 실제 Worker factory + 합성 CLOVA/OpenAI)
- LLM 경로 DB 조회에서 `model_version=synthetic-llm-model`, 기존 prompt version 보존 확인
- 결과 commit·AI Job/OCR 완료, Outbox 발행, Pending delivery 0건 확인

외부 Provider는 합성 응답으로 대체했다. 실제 OpenAI 호출 또는 사용자 화면 E2E 실행 증빙이 아니다.
집중 검증은 전체 검사와 중복될 수 있으므로 수치를 합산하지 않는다.

## 전체 로컬 검사 (2026-09-11)

| 검사 | 결과 |
| --- | --- |
| Migration | 198 passed, 3 skipped |
| Backend·계약·PostgreSQL | 1855 passed, 65 skipped |
| Redis | 24 passed |
| Worker 전체 재검사 | 2985 passed, 8 skipped |
| Ruff·format·Mypy | 통과 (Mypy 577개 파일) |
| base + worker 의존성만 설치한 환경의 공용 LLM import | 통과 |

최초 전체 CI 스크립트는 공용 Worker 파일의 해시를 기록한 기존 인프라 증빙 1건이
오래되어 실패했다. 기존 생성기로 config/runtime 파일 해시와 증빙 self hash만 갱신하고,
해당 검사 3건과 Worker 전체를 다시 실행해 통과했다. 전체 스크립트를 수정 후 다시
실행한 결과는 아니며, 원격 CI 결과도 아니다. 기존 증빙의 승인·활성화 상태는 유지했다.

## 범위 정정 후 검증

Local 전용 활성화 제한을 제거하고 local/staging/production 설정에서 동일한 Worker
factory를 사용할 수 있도록 검증한다. API key·모델·timeout 검증과 기본 비활성화는 유지한다.
운영 Compose에 flag·API key·모델·timeout 전달을 추가했다. 실제 배포나 외부 호출은 수행하지 않았다.

수정 후 `scripts/ci/run_test.sh` 전체 재실행이 통과했다 (2026-09-11).

- Migration: 198 passed, 3 skipped
- Backend·계약·PostgreSQL: 1855 passed, 65 skipped
- Redis: 24 passed
- Worker: 2987 passed, 8 skipped
- Ruff·format·Mypy: 통과
- local/staging/production 각각 실제 Worker 조립 후 합성 OCR → LLM 호출·모델 기록·client 종료 확인
- DB 최종 head `166f20314253`, 사용자 Trigger·RLS·제거 대상 함수 0개

이 결과는 수정 후 로컬 전체 검사이며 원격 CI나 실제 Provider 호출 증빙은 아니다.

## 리뷰와 후속 처리

#453은 기존 OCR LLM 구조화의 Worker 이관과 회귀 검증으로 마무리한다.
새 동의 저장소·철회 검증·전송 최소화·Frontend 동의 안내는 추가하지 않는다.
리뷰어 송은영이 필요하다고 판단하면 별도 이슈로 진행한다. 위 항목을 #453 종료의
신규 선행조건으로 두지 않으며 기존 #207·목표 계약을 완료로 표시하지 않는다.
Frontend 코드와 기존 검수·약물 추가·수정 흐름을 유지한다.

LLM 입력은 기존 전체 OCR 텍스트 토큰이다. 이름·생년월일 등이 텍스트에서 제거됐다는
증빙은 아니며 사용자 검수를 전송 동의로 간주하지 않는다.
