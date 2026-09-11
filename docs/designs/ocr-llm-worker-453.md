# #453 OCR LLM Worker 이관 설계

상태: Worker 연결·Local 합성 검증 구현. 동의·전송 개정안 리뷰 및 실제 저장소 연결 대기. 계약 승인 또는 완료 선언 아님.

기준: develop `93ef882`, #453 본문, AGENTS.md, CONTRIBUTING.md.
구현 김지혜 / 리뷰 송은영.

## 현재 문제와 이관 방식

#258/#268은 CLOVA 및 규칙 기반 구조화만 Worker에 연결했다. Backend의
`LlmPrescriptionStructurer`는 실제 비동기 OCR 요청에서 호출되지 않는다.

기존 client/prompt/schema/validator/structurer 구현을 `ocr_runtime.llm`으로 이동한다.
Backend에는 호환 re-export를 남긴다. Worker는 Backend 설정·ORM을 import하지 않는다.
이는 이미 존재하는 Backend와 Worker 두 소비자가 같은 검증을 재사용하기 위한 이동이며,
별도의 플러그인·큐·Job·DB schema를 도입하지 않는다.

기존 OCR Job 안에서 CLOVA 다음 구조화기를 실행하고 기존 결과 adapter 및
fenced transaction, commit-before-ACK를 재사용한다. LLM client 자체 retry는
중첩하지 않고 공통 Worker 재시도를 사용한다. model/prompt 기록은 기존 필드로 보존한다.

## 실제 활성화 전에 해결할 차이

| 항목 | 현재 구현/계약 | #453 처리 조건 |
| --- | --- | --- |
| 외부 입력 | 기존 LLM은 모든 CLOVA token을 전송 | 목표 allowlist·환자명/생년월일 등 금지정보 제거 기준 확정 필요. 출력 schema 제한만으로 입력 최소화 완료라고 주장하지 않음 |
| 동의 | PD-207 Proposed는 CLOVA 직전 동의만 정의하며 LLM 연결 시 문구·범위 재검토를 명시 | 동의 저장소·Worker Gate 구현과 LLM 호출 전 재확인 연결 조건을 담당자와 정렬. 동의를 합성하거나 임의 승인 처리하지 않음 |
| provenance | 기존 raw/normalized/confirmed 및 model/prompt 필드 | 별도 rule snapshot·LLM draft·validator version의 영속 보존은 기존 이관만으로 충족되지 않음. 새 schema 필요 시 사전 범위 확정 |
| 실패 복구 | 현재 계약은 LLM 실패의 규칙 기반 자동 fallback 금지 | 자동 fallback을 추가하지 않음. 기존 실패·재시도 경계 유지 |

공용 코드 이동과 합성 Provider를 통한 이관 검증은 독립적으로 진행할 수 있다.
실제 호출 활성화 조건은 위 차이의 해소 또는 명시적인 합성 검증 범위 합의 이후 적용한다.
새 이관 코드를 실제 환자 데이터나 Production에 활성화하지 않는다.

## 검증 계획

- 기존 Backend client·grounding·구조화 테스트로 이동 전후 비퇴행 확인
- Backend 설정이 없는 process에서 공용 LLM import 및 합성 실행 확인
- Worker 구조화 결과의 모델/프롬프트 보존, 실패 시 성공 결과 미저장 확인
- 실제 Redis·PostgreSQL과 합성 Provider로 접수/결과/ACK 연결 검증
- 필수 Ruff·format·Mypy·전체 테스트, 사용자 Trigger·RLS 신규 정의 없음 확인

## 사용자 확인 범위

기존 검수 화면과 약물 수동 추가·수정·확정을 유지한다. 약품 영역 선택 UI는 추가하지 않는다.
Frontend 변경이 필요하면 지혜님이 구현하지 않고 담당자 인계로 정리한다.
동의·전송 계약은 #453에서 개정안으로 함께 검토하며 #207 미구현 저장소를 완료로 간주하지 않는다.
