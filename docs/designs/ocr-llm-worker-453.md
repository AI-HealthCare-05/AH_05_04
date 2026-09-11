# #453 OCR LLM Worker 이관 설계

상태: Worker 연결 구현·합성 검증 완료, 담당 리뷰 대기.

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

## 확정한 구현 범위

기존 전체 OCR 텍스트 토큰 입력과 prompt·검증 로직을 유지한다. 원본 이미지를 LLM에
추가 전송하지 않는다. Local 전용 제한은 두지 않고 기존 feature flag 기본값 false를
유지한다. 활성화에는 Worker의 flag=true, API key, 모델 및 유효한 timeout 설정이 필요하다.
운영 Compose에도 동일 설정을 전달한다. 기존 공통 공개 게이트는 변경하지 않는다.

새 동의 저장소·철회 검사·전송 최소화·Frontend 동의 안내는 이번에 추가하지 않는다.
리뷰에서 필요하다고 판단하면 별도 이슈에서 진행하며 #453 신규 완료 조건으로 두지 않는다.
기존 #207 및 목표 계약을 구현 완료로 주장하지 않는다. LLM 실패 시 규칙 기반 자동
fallback은 추가하지 않고 기존 실패·재시도 경계를 유지한다.

## 검증 계획

- 기존 Backend client·grounding·구조화 테스트로 이동 전후 비퇴행 확인
- Backend 설정이 없는 process에서 공용 LLM import 및 합성 실행 확인
- Worker 구조화 결과의 모델/프롬프트 보존, 실패 시 성공 결과 미저장 확인
- 실제 Redis·PostgreSQL과 합성 Provider로 접수/결과/ACK 연결 검증
- 필수 Ruff·format·Mypy·전체 테스트, 사용자 Trigger·RLS 신규 정의 없음 확인

## 사용자 확인 범위

기존 검수 화면과 약물 수동 추가·수정·확정을 유지한다. 약품 영역 선택 UI는 추가하지 않는다.
Frontend 변경이 필요하면 지혜님이 구현하지 않고 담당자 인계로 정리한다.
기존 검수는 전송 동의가 아니며, 동의 기능 추가는 리뷰에서 필요 시 별도 이슈로 진행한다.
