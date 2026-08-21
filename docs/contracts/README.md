# 공통 데이터 계약

Frontend, Backend, OCR과 RAG·LLM이 공유하는 의미와 상태를 관리합니다. 실제 구현 스키마의 기준은 FastAPI OpenAPI와 Pydantic DTO이며, 이 디렉터리에는 경계 설명과 예시를 둡니다.

## 계약 문서

- [복약 가이드 Backend–AI 계약](./medication-guide-ai-backend.md): 확정 처방 입력, AI 생성 결과, 오류와 조립 책임
- [복약 챗봇 Backend–AI Core 계약](./medication-chat-ai-backend.md)
- [OCR 약품명 정규화 계약](./ocr-medication-normalization.md): OCR 원문, 정규화 참고값 및 사용자 확정값의 역할과 정규화 규칙

## 우선 확정할 계약

- Patient/Prescription State와 버전
- OCR 필드, 신뢰도, 오류와 `REVIEW_REQUIRED`
- 비동기 Job 상태: `PENDING → PROCESSING → COMPLETED | FAILED | REVIEW_REQUIRED`
- 복약 상태: 예정, 완료, 지연 완료, 미복용, 불확실, 미확인, 계획된 중단
- AI 결과: 답변, 인용, NLI·안전 검증, 모델·프롬프트 버전
- 오류: `code`, `message`, `details`, `trace_id`

계약 변경은 관련 요구사항 ID, API 명세, 구현, 테스트와 함께 한 PR에서 갱신합니다. 필드 삭제·이름/타입 변경·필수 필드 추가는 Breaking Change로 취급합니다.
