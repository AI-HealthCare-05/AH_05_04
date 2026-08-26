# 시스템 아키텍처

## 목적과 범위

현재 MVP의 실제 실행 구조와 Post-MVP 목표 구조를 구분해 기록합니다. 현재 MVP는 FastAPI Backend가 외부 AI 제공자를 직접 호출하는 동기 one-cycle 구조입니다. Redis 기반 비동기 AI Worker, RAG, 인용·NLI 검증과 AI 평가는 아직 실행 경로에 포함되지 않습니다.

## 현재 MVP 구성요소

| 구성요소 | 현재 책임 |
| --- | --- |
| Frontend | 회원가입·로그인, 처방전 업로드·OCR 조회, OCR 필드 검수·수정, 처방 확정, Guide 생성·조회, Chat 세션·이력·메시지를 실제 API에 연결하고 각 실제 화면에 표시 |
| Nginx | FastAPI 요청을 전달하는 리버스 프록시 |
| FastAPI Backend | 인증·인가, 파일·처방·대화 상태 관리, 동기 OCR·가이드·챗봇 orchestration |
| `backend/app/services/ocr.py` | 같은 HTTP 요청 안에서 CLOVA OCR 호출, 결과 정규화·저장, 오류 매핑 |
| `backend/app/services/guide_ai/` | 확정 처방만 입력받아 OpenAI 복약 가이드 생성 |
| `backend/app/services/chat_ai/` | 현재 질문과 확정 약물 목록만 입력받아 OpenAI 단일 응답 생성 |
| PostgreSQL | 사용자, 의료문서, OCR 결과, 확정 처방, 가이드, 채팅 상태 저장 |
| 로컬 파일시스템 | `STORAGE_DIR` 아래 처방전 원본 저장. 현재 Compose의 영속 volume과 기본 경로가 일치하지 않아 배포 전 확인 필요 |
| Redis | Compose에 준비되어 있으나 현재 MVP AI 처리 경로에서는 사용하지 않음 |
| AI Worker | 실행 진입점만 있는 placeholder이며 현재 MVP 요청을 처리하지 않음 |

Backend는 SQLAlchemy asyncio와 `asyncpg`를 사용합니다. OpenAI 클라이언트는 FastAPI lifespan에서 프로세스 단위로 생성하고 가이드·챗봇이 공유합니다.

## 현재 동기 데이터 흐름

1. 로컬 Frontend는 기본적으로 `http://localhost:8000`의 FastAPI를 직접 호출합니다. 배포 환경에서는 Nginx의 `/api/` proxy를 통해 FastAPI에 전달합니다.
2. FastAPI가 사용자 권한을 확인하고 처방전 파일과 메타데이터를 저장합니다.
3. OCR 실행 API는 CLOVA OCR을 같은 요청 안에서 호출하고 작업 상태와 추출 필드를 PostgreSQL에 저장합니다. 응답 상태가 `202 Accepted`여도 현재 구현은 queue나 Worker에 위임하지 않습니다.
4. 사용자가 OCR 필드를 검수·수정한 뒤 확정 처방을 생성합니다.
5. Frontend는 처방 확정 응답의 `prescription_id`로 가이드 생성 API를 호출합니다. Backend는 확정 처방을 읽고 OpenAI를 직접 호출한 뒤 생성 결과를 저장하고 `201 Created`로 응답하며, Frontend는 응답의 `guide_id` 화면으로 이동합니다.
6. 챗봇 메시지 API는 USER 메시지를 저장하고 OpenAI 단일 응답을 생성한 뒤 ASSISTANT 메시지를 저장하고 `201 Created`로 응답합니다. 같은 세션의 요청은 DB row lock으로 직렬화합니다.
7. timeout, 제공자 장애 또는 응답 처리 실패는 정해진 API 오류로 변환하고 실패 상태를 저장합니다.

현재 AI 입력에는 기능 수행에 필요한 최소 데이터만 전달합니다. 처방전 이미지, OCR 원문·미검수 값, 사용자·세션 식별자와 이전 대화는 가이드·챗봇 AI 경계를 넘지 않습니다.

## 구현 수준 구분

- **Backend MVP 구현**: 인증, 업로드, 동기 OCR, OCR 필드 수정, 처방 확정, 가이드와 챗봇 API
- **Frontend API 연결**: 인증, 처방전 업로드, OCR 실행·조회, OCR 필드 검수·수정, 처방 확정, Guide 생성·조회, Chat 세션·이력·메시지
- **PostgreSQL 실제 E2E 완료 범위**: 회원가입 → 로그인 → 업로드 → OCR → 검수·수정 → 처방 확정
- **전체 AI E2E 확인 필요**: Guide 생성 → Guide 조회 → Chat 진입과 실제 OpenAI 응답까지의 전체 흐름은 최종 완료로 표시하지 않음
- **Schema-only Post-MVP 골격**: `knowledge_document`, `knowledge_chunk`, `guide_citation`, `chat_citation` 모델과 migration은 존재하지만 repository·service·API 실행 경로에는 연결되지 않음
- **미구현 Post-MVP 실행 영역**: RAG 검색, Citation/NLI 검증, AI 평가, OTC와 비동기 Worker

## Post-MVP-1 목표 구조 — Approved target / Not implemented

아래 구조와 계약은 승인됐지만 현재 실행 경로에는 연결되지 않았다.

- OCR·Guide·Chat을 공통 `AI_JOB`의 `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE` 상태로 처리한다.
- API는 PostgreSQL transaction에서 Job과 Transactional Outbox를 함께 commit하고, publisher가 Redis Stream에 at-least-once로 전달한다. Worker는 lease·fencing token을 사용하며 결과 DB commit 뒤에만 ACK한다.
- 결과는 불변 `prescription_version_id`에 귀속하고 active version이 아니면 `STALE`로 공개를 차단한다.
- Track B는 사용자가 확인한 schedule에서 occurrence를 생성하고 Check-in·감사 이력을 관리한다. Track C는 `NOT_TAKEN` 뒤 Safety assessment → Barrier → Support → ActionPlan 순서를 따른다.
- Track D는 사용자가 확정한 OTC 제품 또는 성분을 구조화 rule과 승인 source version으로 동기 평가한다.
- Track F는 승인 Source의 RAG, claim별 Citation, Safety 검증과 `PASS|LIMITED|REJECTED|STALE` 공개 결정을 분리한다.
- `ASYNC_OCR`, `ASYNC_GUIDE`, `ASYNC_CHAT`으로 신규 접수 경로를 단계 전환한다. `PUBLIC_TRACK_C`, `PUBLIC_TRACK_D`, `PUBLIC_TRACK_F`는 별도 외부 승인 게이트 전까지 닫는다.

현재 동기 one-cycle은 각 전환 조건이 충족될 때까지 Current다. 목표를 구현하는 PR은 관련 계약, migration, OpenAPI/DTO, 계약·통합 테스트와 운영 증빙을 함께 갱신해야 한다.

## 주요 결정

- MVP는 가이드와 챗봇의 기존 동기 one-cycle API 계약을 유지합니다.
- RAG·인용·NLI·AI 평가·OTC를 구현된 기능이나 MVP 배포 게이트로 표시하지 않습니다.
- Redis와 AI Worker의 존재만으로 비동기 처리가 구현됐다고 간주하지 않습니다.
- RAG·Citation 테이블의 존재만으로 검색·인용 기능이 구현됐다고 간주하지 않습니다.
- 운영 모델·timeout·동시 생성량·DB pool 수용량·외부 전송 데이터 승인은 `docs/deployment.md`에 실제 배포값으로 기록합니다.
