# 테스트 전략

## 범위 기준

테스트와 배포 기준은 현재 MVP와 Post-MVP를 구분합니다.

- **현재 MVP**: FastAPI 요청 안에서 OCR, 복약 가이드 생성, 복약 챗봇 응답을 완료하는 동기 one-cycle 흐름
- **Post-MVP**: 비동기 AI Worker, RAG, 출처 인용, Citation/NLI 검증, OTC 성분·상호작용 기능, AI 응답 품질 평가

Post-MVP용 디렉터리나 문서가 저장소에 있더라도 현재 MVP의 구현 완료 또는 배포 조건으로 간주하지 않습니다.

## 테스트 계층

### 현재 MVP

- `app/tests/`: Backend API·서비스·DB, OCR·가이드·챗봇 AI 어댑터 테스트
- `tests/contract/`: 현재 Backend–AI Core 경계 계약. OpenAPI 회귀 테스트는 아직 없음
- `tests/integration/`: 공통 CORS·오류 동작 검증. 현재 기본 CI 명령에는 포함되지 않음
- `tests/e2e/`: 전체 사용자 여정 테스트를 위한 준비 영역이며 현재 자동화된 E2E 테스트는 없음
- `tests/evals/ocr/`: OCR 엔진 검토 자료와 측정 결과

### Post-MVP 준비 영역

- `ai_worker/tests/`: 비동기 Worker 작업 단위 테스트
- `tests/integration/`: Redis·AI Worker를 연결한 통합 테스트
- `evals/`: RAG 검색, 생성, Citation/NLI, 안전, OTC 품질 평가와 배포 게이트

## MVP 핵심 시나리오

1. 회원가입·로그인과 인증된 사용자 확인
2. 처방전 업로드
3. 같은 요청 안에서 OCR 실행 및 성공·실패 처리
4. OCR 결과 조회, 사용자 검수·수정 및 확정 처방 생성
5. 확정 처방 기반 복약 가이드 동기 생성·저장·조회
6. 확정 처방 기반 채팅 세션 생성
7. 사용자 메시지 저장, OpenAI 단일 응답 생성, AI 메시지 저장을 한 요청에서 완료
8. 외부 AI timeout·가용성·응답 처리 실패의 안전한 오류 매핑과 민감정보 비노출

현재 프롬프트의 추측 금지, 복용 변경 금지, 정보 부족 시 확인 요청, 응급 도움 우선 안내는 MVP 안전 제약입니다. 다만 이를 별도 평가 데이터셋과 임계값으로 판정하는 **AI 응답 품질 게이트**는 Post-MVP입니다.

필수 동의 수집·철회는 디자인 프로토타입에만 있으며 현재 Backend DTO와 실제 사용자 흐름에는 연결되지 않았습니다. 구현되기 전까지 자동화된 MVP 시나리오로 간주하지 않습니다.

## 현재 자동 검증 범위

GitHub Actions와 `scripts/ci/run_test.sh`는 기본적으로 다음 명령과 동등한 Python 범위를 검증합니다.

```bash
uv run coverage run -m pytest app tests/contract
```

- `app/tests/chat_integration/`을 포함한 `app/` 아래 테스트는 기본 실행 범위에 포함됩니다.
- `tests/integration/`, `tests/e2e/`, `ai_worker/tests/`와 Frontend 테스트는 기본 실행 범위에 포함되지 않습니다.
- OpenAPI endpoint 목록은 현재 문서 검토로 대조하며 자동 contract regression test에는 연결되지 않았습니다.
- Frontend는 별도로 `pnpm lint`와 `pnpm build`를 실행합니다.
- 가이드 실호출은 `RUN_OPENAI_SMOKE=1`, 챗봇 실호출은 `RUN_OPENAI_CHAT_SMOKE=1`일 때만 실행됩니다. 기본 CI에서 skip되므로 배포 기록에는 별도 실행 결과를 남깁니다.

## MVP 배포 차단 기준

- Ruff·Mypy·현재 범위의 자동 테스트 실패
- OpenAPI, DTO와 실제 응답 계약 불일치
- 미확정 OCR 값을 확정 처방으로 사용
- 확정 처방과 가이드의 결정론적 복약 정보 불일치
- timeout·외부 서비스 실패·잘못된 AI 응답을 성공으로 저장 또는 반환
- 로그나 오류 응답에 처방전 원문, 사용자 질문, API Key 등 민감정보 노출
- `SECURITY.md`의 근거·검증 추적 원칙을 충족하지 못함. 승인표나 수동 검토만으로 예외 처리하지 않음
- 실제 OpenAI 호출 확인 및 운영 설정 승인을 포함한 `docs/deployment.md`의 배포 기록 미완료

현재 자동화되지 않은 검증 항목은 통과한 것으로 간주하지 않습니다. 배포 차단 기준에 포함하려면 수동 확인 결과를 배포 기록에 남기거나 CI에 검증 명령을 연결해야 합니다.

## Post-MVP 품질 게이트

다음 항목은 기능 구현, 데이터셋·임계값 합의, 재현 가능한 평가 실행이 완료된 뒤 배포 차단 기준으로 전환합니다.

- RAG 검색 Recall@K와 승인 지식 소스·인덱스 버전 검증
- 주요 의료 주장 Citation coverage와 출처 추적
- Citation/NLI 기반 faithfulness·entailment 검증
- 응급·고위험·정보 부족 사례의 AI 안전 평가
- OTC 성분 식별, 중복·상호작용 탐지와 정보 부족 Fallback 평가
- 모델·프롬프트·검색 인덱스 변경에 대한 회귀 평가

위 평가 체계가 Post-MVP라는 분류는 현재 의료 안전 원칙을 유예한다는 뜻이 아닙니다. 구현 전 사용자 검증은 비식별 합성 데이터와 접근 통제를 사용하는 내부 staging 데모로 제한하며 Production 승인으로 간주하지 않습니다.
