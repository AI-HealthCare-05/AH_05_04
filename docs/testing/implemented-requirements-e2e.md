# 현재 구현 기반 요구사항 E2E 추적표

## 기준선과 판정 원칙

- 코드 기준: `origin/develop` `0b84670ed1c55aab91e54f76f1e4722301f10c84`
  (`🐛 fix: Worker runtime에 Outbox Publisher 연결 (#370) (#371)`,
  2026-09-09 10:03:59 KST)
- 요구사항 원본: [Google Drive 요구사항 정의서 v8](https://drive.google.com/file/d/1MYc21OadMpPQrF4PNUtRw0_0akR0cByf/view?usp=drive_link)
  `요구사항_정의서_v8_릴리즈분류_2차검토_2026-09-04.xlsm`
  (file ID `1MYc21OadMpPQrF4PNUtRw0_0akR0cByf`, 최종 수정
  2026-09-06T07:12:44.493Z)
- 원본 SHA-256:
  `45f4efa994a2f50bb7eb7508ae02d4c039a30321d1dafc0377901c4f64781b1a`

요구사항의 릴리즈 분류만으로 구현 완료를 선언하지 않는다. 위 코드 기준선에서 Frontend,
OpenAPI/Pydantic DTO, migration, 서비스·Worker와 기존 자동 테스트가 함께 뒷받침하는 현재
동작만 Playwright 대상으로 삼는다. 아래의 `직접`은 해당 AC의 정상 경로를 이 시나리오에서
관찰한다는 뜻이고, `부분`은 AC 일부만 관찰한다는 뜻이다. 요구사항 전체 통과 선언이 아니다.

## Mock 기반 Frontend 회귀

`frontend/e2e/`는 모든 `/api/v1/**` 요청을 비식별 합성 응답으로 가로챈다. Frontend의 요청,
화면 상태와 다음 행동을 빠르게 검증하지만 Backend·DB·Worker·외부 Provider 증거는 아니다.

| 시나리오 | 요구사항 연결 | 관찰 범위 | 미관찰 범위 |
| --- | --- | --- | --- |
| 회원가입 성공 | `REQ-USR-007` 부분 | 유효 입력 제출과 성공 후 로그인 화면 이동 | DB 저장, hash, 중복·동시 요청, 로그 |
| 인증 경계·프로필 수정·로그아웃 | `REQ-USR-010`, `REQ-USR-019`, `REQ-USR-020` 부분 | 보호 route 선차단, 현재 사용자 표시, PATCH 성공 뒤 완료 표시, 로컬 token 제거와 재진입 차단 | 서버 token/refresh cookie 무효화, 만료·교차 사용자 fixture |
| 처방전 one-cycle | `REQ-USR-001`, `REQ-DOC-001/003/005/006/007/009/010`, `REQ-OCR-004` 부분 | 업로드, Job polling UI, 필수 placeholder 직접 입력, 필드 저장, 명시적 확인, 처방 확정, Guide·Chat 화면 연결 | 실제 파일·OCR·DB·OpenAI, 동시성·권한·실패 rollback, provenance |
| 업로드 실패 안전 표시 | `REQ-DOC-004`, `NFR-SYS-007` 부분 | Provider 상세 비노출, 이해 가능한 오류와 재선택 행동 | 실제 Provider 실패 분류, 재업로드 성공·멱등성 |
| OCR STALE | `REQ-OCR-002`, `NFR-SYS-003` 부분 | STALE을 성공으로 표시하지 않고 최신 정보 확인 행동 제공 | 서버 상태 전이, ownership, Retry-After·failure code 전체 |
| 현재 데이터 재발견 | `REQ-HIS-009` 부분과 현재 runtime 회귀 | 브라우저 임시 상태 없이 현재 처방의 Guide를 찾고 기존 Chat 메시지 조회 | 처방·가이드 전체 이력, 목록·검색, 교차 사용자 |
| 빈 상태 | `NFR-SYS-009` 부분 | 처방·Guide가 없을 때 오류와 구분된 다음 행동 표시 | 모든 의료기록·일정·대화 화면의 권한·API 실패 분기 |

예상하지 않은 API 요청은 테스트 실패로 기록해 mock 시나리오가 조용히 다른 계약으로
확장되지 않게 한다.

## Real-stack CLOVA·OpenAI one-cycle

`frontend/e2e-real-stack/ocr-handoff.spec.ts`는 API interception과 dependency override를
사용하지 않는다. `docker-compose.real-stack-e2e.yml`의 격리 환경에서 다음을 확인한다.

1. 승인 manifest `ai-one-cycle-clova-openai-v1.json`과 합성 PNG의 SHA-256 일치
2. 실제 회원가입·로그인과 처방전 업로드
3. OCR `202 Accepted`, `Location`, opaque `status_url`, PENDING 및 COMPLETED polling
4. Outbox·Redis Stream·AI Worker를 거친 실제 CLOVA 결과 8개와 manifest 값의 화면 일치
5. 원본 iframe, 처방일·약물별 검토 완료, 사용자 명시 확인과 처방 version 생성
6. 실제 OpenAI Guide의 COMPLETED, 비어 있지 않은 본문, 실제 model ID,
   `guide-prompt-v3`, 기준 처방 값 및 Guide 화면 표시
7. 처방에 연결된 Chat session, 실제 OpenAI Chat의 COMPLETED, 실제 model ID,
   `chat-prompt-v2`, 질문의 횟수·시점 사실 및 사용자·AI 메시지 화면 표시

| 요구사항 | E2E 증거 | 판정 |
| --- | --- | --- |
| `REQ-USR-001` AC-01 | 로그인 사용자의 업로드→검수→확정→Guide 화면 정상 흐름 | 직접 |
| `REQ-DOC-001` AC-01 | 승인 합성 PNG의 실제 업로드와 성공 반영 | 부분: JPG·PDF 미실행 |
| `REQ-DOC-003` AC-01 | 접수·처리 후 검수 화면 전환 | 부분: 모든 실패·중복 상태 미실행 |
| `REQ-DOC-005` AC-01 | 같은 검수 화면에서 원본 iframe과 추출 필드 표시 | 부분: LLM 초안·원문 위치 구분 미확인 |
| `REQ-DOC-006` AC-01 | 8개 필드 값과 섹션 검토 상태 표시 | 부분: provenance version·오류 상태 미확인 |
| `REQ-DOC-010` AC-01 | 필드별 검토와 checkbox 확인 뒤 처방 및 `prescription_version_id` 생성 | 직접 정상 경로 |
| `REQ-OCR-001` AC-02 | 202, Location과 동일 `status_url` 검증 | 직접 |
| `REQ-OCR-002` AC-04 | COMPLETED 뒤 Backend가 준 결과 경로로 검수 화면 전환 | 부분: ownership·실패·Retry-After 미실행 |
| `REQ-OCR-004` AC-01 | 각 field_id의 원본 표시값을 사용자가 검토해 확정 전 저장 | 부분: 수정값·직접 입력 출처 구분 미실행 |
| `REQ-GEN-001` AC-01~02 | 실제 OpenAI 결과의 본문·model·prompt와 원본 처방 사실 검증 | 직접 정상 경로 |
| `REQ-GEN-002` AC-01 | 최종 Guide 사실이 확정 처방 값과 일치 | 직접 정상 경로 |
| `REQ-GEN-003` AC-01·03 | 단일 입력 약물이 한 번 표시되고 약명·함량·횟수·시점 일치 | 부분: 불일치 차단 fixture 미실행 |
| `REQ-GEN-019` AC-01~02 | COMPLETED Guide의 content·model·prompt와 저장 식별자, 재조회 화면 | 직접 정상 경로 |
| `REQ-GEN-020` AC-01 | Backend Guide의 약명·용량·횟수·시점과 주의사항을 화면에 표시 | 부분: AC-02~04 상태 조합 미실행 |

실제 Chat 호출은 현재 구현 연결성과 처방 context 전달을 확인한다. 그러나 원본 요구사항의
`REQ-CHT-*`, RAG·Citation·Safety Result·OTC는 대부분 post-MVP-1 승인 목표다. 현재 동기
OpenAI 응답을 그 목표의 완료 증거로 표기하지 않는다. `REQ-HIS-009`도 대화 이력 조회의
mock 정상 경로만 연결하며 전체 요구사항 완료로 보지 않는다.

## 의도적으로 완료로 주장하지 않는 범위

- 동의 수집·철회, 계정 탈퇴·비밀번호 재설정, 일정·알림과 운영자 화면
- OCR 정확도·성능 평가, JPG/PDF·손상·품질 경계, 실패·재시도·중복·교차 사용자 matrix
- Guide 실패·불일치·누락 입력 차단의 전체 negative matrix와 의료 품질 평가
- MFDS Identity, RAG Retrieval, Citation, Safety Result, OTC, 외부 승인 및 production gate
- 전체 처방·Guide·Chat 이력과 prescription version 전환·STALE 공개 차단

## 실행

Mock Frontend 회귀는 CI에서 실행한다.

```bash
cd frontend
pnpm run test:e2e:requirements
```

실제 Provider one-cycle은 credential과 외부 비용이 필요한 Local opt-in이다.

```bash
RUN_REAL_STACK_AI_E2E=1 bash scripts/e2e/real_stack.sh test
```

2026-09-09 10:57 KST에 위 코드 기준선에서 기록한 결과는 다음과 같다.

- Mock Playwright: 7개 통과, 10.6초
- Real-stack Playwright: 1개 통과, 10.4초(테스트 본 실행 9.7초)
- 실제 저장 메타데이터: Guide `gpt-4o-mini-2024-07-18` / `guide-prompt-v3`,
  Chat Assistant `gpt-4o-mini-2024-07-18` / `chat-prompt-v2`, 모두 COMPLETED·본문 존재
- Frontend Vitest: 17 files, 271개 통과
- Frontend build·lint와 `git diff --check`: 통과

성공은 위 단일 승인 합성 fixture의 연결성 증거다. 의료 안전성, OCR 정확도, 외부 승인 또는
production 공개 승인을 대신하지 않는다.
