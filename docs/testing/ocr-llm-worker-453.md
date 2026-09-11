# #453 OCR LLM Worker 연결 검증과 인계

## 이번 변경

기존 Backend의 OCR LLM client·prompt·schema·grounding validator를 공용 패키지로
이동하고 호환 import를 유지했다. 실제 Worker factory에서 명시적 Local opt-in 시
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

## 남은 계약·담당 인계

### 송은영: #207 동의 저장소와 실행 Gate

현재 develop에는 목적별 동의 DB 모델·조회·철회 API가 없다. #207 팀 합의는
동의 schema를 먼저 확정한 뒤 Worker가 동일 판정 fixture에 맞춰 연결하도록 한다.
#453 개정안은 OCR 목적에 LLM 전송을 명시하고, Backend 접수/CLOVA 직전/LLM 직전
검사, 최신 policy version, 철회 시 STALE/BLOCKED 및 OCR 실패 저장을 제안한다.

동의 DB schema·정책 버전·실제 조회 연결 및 차단 저장 구현은 현재 변경의 완료 항목이 아니다.
Local flag는 동의의 대체물이 아니다. 이 경계를 해결하기 전 #453 전체를 닫지 않는다.

### 남한솔: 필요한 Frontend 변경의 인계

약품 영역 선택·crop UI는 이번 범위에 추가하지 않는다. 기존 검수·약물 추가·직접 수정
화면을 유지한다. #207 동의 화면 작업과 연결할 때에는 OCR 안내에 CLOVA와 OpenAI 사용을
함께 설명하고, policy 갱신 및 동의 내역·철회 경로를 맞춘다. 확정되지 않은 새 API를
Frontend에서 추측해 구현하지 않도록 Backend 계약 확정 후 인계한다.

Frontend 코드는 이번 작업에서 수정하지 않았다. 인계 문서 작성이 실제 담당자 통보나
리뷰 승인·작업 수락을 의미하지 않는다.

### 전송 최소화 및 provenance

현재 이관은 기존 전체 토큰 구조화를 유지한다. JSON 필드 제한·store=False·grounding은
환자명·생년월일이 token.text 안에서 제거됐다는 증빙이 아니다. 실제 환자 데이터를 사용하지
않는 기존 프로젝트 범위와 Local 검증 제한을 유지하며, 의미상 최소화 계약은 별도 확정이 필요하다.
raw/rule/LLM draft/corrected/confirmed 별도 영속 보존도 기존 model/prompt 저장과 구분한다.

계약 개정안: `docs/contracts/proposed/ocr-llm-worker-consent-453.md`.
