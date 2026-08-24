# 시스템 아키텍처

## 목적과 범위

현재 MVP의 실제 실행 구조와 Post-MVP 목표 구조를 구분해 기록합니다. 현재 MVP는 FastAPI Backend가 외부 AI 제공자를 직접 호출하는 동기 one-cycle 구조입니다. Redis 기반 비동기 AI Worker, RAG, 인용·NLI 검증과 AI 평가는 아직 실행 경로에 포함되지 않습니다.

## 현재 MVP 구성요소

| 구성요소 | 현재 책임 |
| --- | --- |
| Frontend | 회원가입·로그인, 처방전 업로드와 OCR 결과 요약까지 API 연결. 검수·처방 확정·가이드·챗봇은 디자인 프로토타입 또는 미연결 상태 |
| Nginx | FastAPI 요청을 전달하는 리버스 프록시 |
| FastAPI Backend | 인증·인가, 파일·처방·대화 상태 관리, 동기 OCR·가이드·챗봇 orchestration |
| `app/services/ocr.py` | 같은 HTTP 요청 안에서 CLOVA OCR 호출, 결과 정규화·저장, 오류 매핑 |
| `app/services/guide_ai/` | 확정 처방만 입력받아 OpenAI 복약 가이드 생성 |
| `app/services/chat_ai/` | 현재 질문과 확정 약물 목록만 입력받아 OpenAI 단일 응답 생성 |
| MySQL | 사용자, 의료문서, OCR 결과, 확정 처방, 가이드, 채팅 상태 저장 |
| 로컬 파일시스템 | `STORAGE_DIR` 아래 처방전 원본 저장. 현재 Compose의 영속 volume과 기본 경로가 일치하지 않아 배포 전 확인 필요 |
| Redis | Compose에 준비되어 있으나 현재 MVP AI 처리 경로에서는 사용하지 않음 |
| AI Worker | 실행 진입점만 있는 placeholder이며 현재 MVP 요청을 처리하지 않음 |

Backend는 SQLAlchemy asyncio와 `asyncmy`를 사용합니다. OpenAI 클라이언트는 FastAPI lifespan에서 프로세스 단위로 생성하고 가이드·챗봇이 공유합니다.

## 현재 동기 데이터 흐름

1. 로컬 Frontend는 기본적으로 `http://localhost:8000`의 FastAPI를 직접 호출합니다. 배포 환경에서는 Nginx의 `/api/` proxy를 통해 FastAPI에 전달합니다.
2. FastAPI가 사용자 권한을 확인하고 처방전 파일과 메타데이터를 저장합니다.
3. OCR 실행 API는 CLOVA OCR을 같은 요청 안에서 호출하고 작업 상태와 추출 필드를 MySQL에 저장합니다. 응답 상태가 `202 Accepted`여도 현재 구현은 queue나 Worker에 위임하지 않습니다.
4. 사용자가 OCR 필드를 검수·수정한 뒤 확정 처방을 생성합니다.
5. 가이드 생성 API는 확정 처방을 읽고 OpenAI를 직접 호출한 뒤 생성 결과를 저장하고 `201 Created`로 응답합니다.
6. 챗봇 메시지 API는 USER 메시지를 저장하고 OpenAI 단일 응답을 생성한 뒤 ASSISTANT 메시지를 저장하고 `201 Created`로 응답합니다. 같은 세션의 요청은 DB row lock으로 직렬화합니다.
7. timeout, 제공자 장애 또는 응답 처리 실패는 정해진 API 오류로 변환하고 실패 상태를 저장합니다.

현재 AI 입력에는 기능 수행에 필요한 최소 데이터만 전달합니다. 처방전 이미지, OCR 원문·미검수 값, 사용자·세션 식별자와 이전 대화는 가이드·챗봇 AI 경계를 넘지 않습니다.

## 구현 수준 구분

- **Backend MVP 구현**: 인증, 업로드, 동기 OCR, OCR 필드 수정, 처방 확정, 가이드와 챗봇 API
- **Frontend API 연결**: 인증, 처방전 업로드, OCR 실행·결과 요약
- **Frontend 미연결**: OCR 필드 검수·수정, 처방 확정, 가이드와 챗봇 사용자 여정
- **Schema-only Post-MVP 골격**: `knowledge_document`, `knowledge_chunk`, `guide_citation`, `chat_citation` 모델과 migration은 존재하지만 repository·service·API 실행 경로에는 연결되지 않음
- **미구현 Post-MVP 실행 영역**: RAG 검색, Citation/NLI 검증, AI 평가, OTC와 비동기 Worker

## Post-MVP 목표 구조

다음은 별도 설계·계약·운영 기준이 승인된 뒤 도입합니다.

- Redis queue와 장기 실행 AI Worker를 통한 비동기 처리
- 승인 의료 지식 소스의 수집·버전 관리와 RAG 검색
- 의료 주장별 citation 생성·추적과 Citation/NLI 검증
- 재현 가능한 데이터셋·지표·임계값 기반 AI 평가 및 배포 게이트
- OTC 성분 식별과 처방약 중복·상호작용 확인

비동기 전환 시 HTTP 상태·작업 상태·재시도·멱등성·오류 의미가 달라질 수 있으므로, 관련 `docs/contracts/`, API·스키마 문서와 계약·통합 테스트를 함께 갱신해야 합니다.

## 주요 결정

- MVP는 가이드와 챗봇의 기존 동기 one-cycle API 계약을 유지합니다.
- RAG·인용·NLI·AI 평가·OTC를 구현된 기능이나 MVP 배포 게이트로 표시하지 않습니다.
- Redis와 AI Worker의 존재만으로 비동기 처리가 구현됐다고 간주하지 않습니다.
- RAG·Citation 테이블의 존재만으로 검색·인용 기능이 구현됐다고 간주하지 않습니다.
- 운영 모델·timeout·동시 생성량·DB pool 수용량·외부 전송 데이터 승인은 `docs/deployment.md`에 실제 배포값으로 기록합니다.
