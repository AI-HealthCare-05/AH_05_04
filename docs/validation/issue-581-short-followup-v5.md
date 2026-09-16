# Issue #581 — 짧은 후속 질문 v5 합성 회귀

- 구현 담당자: 권가빈 (@hazelnutflavoured)
- 담당 리뷰어: 정현우 (@ceohwj), AI/RAG 문맥·안전 의미·Gold 기대/금지 응답
- 기준 develop: `1936bf80`
- 상태: 구현 및 합성 replay 완료, 실제 Provider·blind 사람 평가 `NOT_RUN`, 담당 리뷰어 승인 대기

## 변경과 평가 범위

v4에는 병용 답변 후 새 약명만 묻는 질문, 직전 안내의 이유를 묻는 구어체를 명시적으로 연결하는 규칙이 없었습니다. v5는 `파라시타몰은?`을 현재 처방약과 비교할 외부 대상 교체로, 중단 전 상담 안내 뒤 `왱?`을 이유 질문으로 연결하도록 지시합니다. 공식 제품·제형 Identity를 확정하지 않으며, 현재 입력에 적격 상호작용 근거가 없다는 제한을 첫 병용 답변과 후속 답변 모두에 적용합니다.

새 30-case dataset은 기존 27-case와 safety gate를 보존합니다. 새 3개 사례는 위 두 문맥 문제와 첫 병용 답변 안전 단정을 다룹니다. 사용자 피드백의 실패 문구는 합성 입력으로 구성했고 실행 당시의 실제 Provider 출력·설정으로 검증한 관찰은 아닙니다. 기대 답변도 사람이 작성한 평가용 예시입니다.

## 재현과 결과

- prompt: `chat-prompt-v5`
- prompt SHA-256 (runtime 문자열, 끝 개행 제외): `891415d165720f9fbcc8a44dfc0f9fbf8e271a5371c3a0b34e2e366715bada70`
- dataset: `chat-v5-short-followup-eval-v1`
- dataset SHA-256: `edca523ada9793fef13821c7a2b9b93267d2c0b69e9d358478aef23edeabc3bf`
- 모델 설정: `gpt-4o`, max output tokens 800, timeout 20초. 실행 Provider는 deterministic replay 대역입니다.
- history: baseline은 빈 배열, treatment는 case의 합성 최근 대화. 운영 flag 활성화나 실제 사용자 history 전송은 수행하지 않습니다.
- 재현 명령: `evals/README.md`의 v5 deterministic 명령
- 결과: baseline 30/30, history 30/30, replay `passed=true`
- Chat AI 및 Provider 연결 테스트: 209 passed, 1 skipped (실제 OpenAI smoke)
- 전체 mypy: 734개 소스 통과, Ruff check/format 통과
- 필수 통합 스크립트: inventory·단일 Alembic head·DB 로직·보호 테이블 검사 통과 후 작업 공간의 `envs/.local.env` 부재로 DB 실행 중단. DB 통합 결과는 PR CI에서 확인합니다.

회귀는 관찰된 문맥 초기화·불필요한 재질문·근거 없는 안심 표현을 검출하며, 문맥 평가가 통과해도 안전 평가는 독립적으로 실패합니다. 단어 기반 검사는 모든 의미적 변형을 검출하지 못합니다. 자연스러움과 실제 모델의 수정 전후 품질은 별도 Provider 실행 및 리뷰어 평가가 필요합니다. 현재 결과는 의료 안전성 또는 Production 공개 승인을 의미하지 않습니다.

API·DTO·DB·상태/오류 의미 변경 없이 프롬프트와 평가 fixture를 개선했습니다. 기존 고정 응급·중복·과량 응답 문자열은 동일성 테스트로 보존합니다. v3/v4 blind artifact는 v5 근거로 전용하지 않습니다.

리뷰 반영: 현재 contract test·one-cycle release verifier·관련 runtime 문서를 v5로 정렬했습니다. 새 Gold에는 Identity 단정, `괜찮다/문제없다`, `중단해도/끊어도 된다`의 대표 변형을 추가했으며, 정상 문맥과 상담 권고를 함께 포함해도 baseline/history 및 safety 검사가 실패함을 테스트합니다. 이 유한한 금지 목록은 범용 의미 분류기가 아니며 모든 의역 검출을 보장하지 않습니다.
