# 테스트 전략

## 계층

- `app/tests/`: Backend API·서비스·DB 테스트
- `ai_worker/tests/`: OCR·RAG·LLM·평가 단위 테스트
- `tests/contract/`: OpenAPI와 모듈 간 스키마 계약
- `tests/integration/`: Backend·Redis·AI Worker·DB 통합
- `tests/e2e/`: 핵심 사용자 여정
- `evals/`: OCR·검색·생성·안전·OTC 품질 게이트

## MVP 핵심 시나리오

1. 가입과 필수 동의
2. 처방전 업로드와 비동기 작업 접수
3. OCR 성공·부분 성공·실패 및 사용자 검수
4. 확정 처방 기반 일정과 가이드 생성
5. 다섯 가지 복약 상태 Check-in
6. Barrier 확인과 안전한 Support
7. OTC 성분 식별과 처방약 상호작용 확인
8. 근거 부족·고위험 질문의 안전한 Fallback

## 배포 차단 기준

- Ruff·Mypy·자동 테스트 실패
- OpenAPI와 실제 응답 계약 불일치
- 처방과 가이드 불일치
- 주요 의료 주장에 출처 누락
- 위험 사례에서 정상 답변 생성
- 미확인 결과를 안전하다고 단정
