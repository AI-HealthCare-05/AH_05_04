# Track C 상황별 연결 검증 기록 — #194 / #139

- 구현 담당: 권가빈. 단일 책임 리뷰어: 김지혜.
- 기반 develop: 66328a09 (#631 포함). Frontend 기반 #629(32898498), #635(2daa3a1d)는 아직 별도 리뷰 중.
- /private/tmp의 격리 작업 폴더에서 기존 #633 작업 폴더를 수정하지 않고 구현했다.
- 합성 데이터만 사용. 실제 약별 근거·임상 판정·Push 도착 또는 공개 승인을 주장하지 않는다.

## 구현 범위

- 일정 변경·외출 선택 → 단일 Offer → 명시 Plan 생성·완료.
- 원래 약·복약 기록·저장 당시 Copy 조회. SELF 및 과거 버전 보호.
- 잊음의 일정·기기 알림 설정 연결 및 완료 전 상태 재확인.
- 복용법·필요성의 기존 정보 링크와 근거 미제공 표시, 걱정의 일반 안내 중단.
- 약별 승인 설명/Citation과 임상 Safety 정책은 승인 자료·계약 결속 확인 전 미완료다.

## 검사 결과

- 신규 상황 API·설정 집중: 33 passed. 이후 legacy null/생략 멱등성 검사를 추가했다.
- Track C·설정·기존 fixture 회귀: 최초 180 passed / fixture 활성 버전 불일치 1 failed.
  fixture를 새 활성 버전으로 정렬한 뒤 해당 계약 검사 1 passed.
- 계획 자료 통합: 8 passed.
- Frontend 전체: 685 passed. API 소비 검사 추가 후 관련 2 passed (기존 1건 포함).
- Frontend lint·TypeScript·production build PASS. 기존 main chunk 500kB 경고는 남음.
- Backend/Worker Mypy: 747 source files PASS. Ruff check/format PASS.
- 브라우저 요구사항: 42 passed. Track C 320·390·412px 및 두 상황 320px 포함.
- 실제 FastAPI·PostgreSQL·브라우저 왕복: 1 passed. API interception 없음.
  일정 변경은 정확한 약의 일정 화면·완료, 약 미지참은 생성·새로고침·취소를 검증한다.
  약 챙기기 명시 완료는 API 통합·mock 브라우저·컴포넌트 테스트로 검증한다.
- 전체 `scripts/ci/run_test.sh`: exit 0. Migration 237 passed/4 skipped, Backend·계약·PostgreSQL 2,685 passed/128 skipped, Redis 통합 29 passed, Worker 3,786 passed. 합산 coverage 92% (99,636 statements, 8,440 missed).
- 새 Track C 상황 12건·자료 조회 8건은 전체 Backend 검사에 포함된다. 기존 opt-in Protected Source 등의 skip을 통과나 외부 승인 증빙으로 계산하지 않는다.
- 변경 문서의 상대 링크·Markdown 렌더링·전체 diff 및 `git diff --check` PASS.

첫 Frontend 전체 실행은 VITE_API_BASE_URL 누락으로 실패했다. 합성 localhost 값을 지정한 재실행이 위 685 passed다.
pnpm 실행은 공유 node_modules 재설치를 시도해 중단했으며, Node 24의 기존 도구 실행 파일로 검사했다.
lockfile·dependency 변경은 없다.

## 제한과 인수

실제 기기 Push 도착은 #471, 약별 Source/Citation runtime은 #196/Track F,
임상 Safety 정책은 #193 승인 자료가 필요하다. 기존 Guide 평문이나 검토 전 증상 초안으로 대체하지 않았다.
#629·#635의 승인·병합과 책임 리뷰 승인 전 이 통합 브랜치를 배포/병합하지 않는다.
