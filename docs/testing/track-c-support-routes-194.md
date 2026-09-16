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

## PR #639 책임 리뷰 반영 검증 (2026-09-16)

- 1·3: 알림 확인은 `inspectWebPushState`로 분리했다. 권한·로컬 binding·기존 구독만 읽으며
  POST/DELETE·subscribe/unsubscribe·권한 prompt·worker 등록·storage 변경이 없음을 검증한다.
  모든 비등록 상태에서 일정 확인만으로 완료할 수 있고, 저장 직전 상태가 달라지면 재동의를 받는다.
- 2: FORGOT 확인은 Copy 버전과 무관하다. 약 챙기기는 과거 2026-09-15.1만 기존 의미를 유지한다.
  활성 API fixture의 Copy 버전과 미래 합성 버전 모두에서 확인 문구와 절차가 유지되는지 검증한다.
- 4: 자료 조회 503/네트워크 실패 시 상태·취소·재조회를 유지한다. 안내 없는 완료는 허용하지 않는다.
  401/403/404/409는 이 복구 경로로 우회하지 않는다.
- 5: 상황별 매핑은 typed dictionary의 직접 조회이며 Literal 전체 멤버 커버리지를 검증한다.
- 6: 실스택 시나리오에 FORGOT을 복원했다. 실제 API로 계획 생성·새로고침·원래 일정 화면 연결·
  알림 미등록 상태 확인·일정만 확인한 명시 완료·저장 결과 조회까지 검증했다. 두 외출 분기도 유지한다.
- 7: #192 검증 문서의 내부 활성 버전을 갱신했다.

이번 수정 검증: Frontend 전체 테스트 709개, 관련 Backend 통합 테스트 21개,
Track C 브라우저 5개, 실제 API 왕복 시나리오 1개(세 사유 분기), TypeScript·빌드·oxlint,
전체 Ruff check/format, Mypy 747개 파일, `git diff --check` 통과.
빌드의 기존 500 kB chunk 경고는 남아 있다. 실제 Push 도착·임상 승인·공개 승인 증거는 아니다.

## develop 통합 및 최종 리뷰 정렬 (2026-09-16)

- `0203803b`에서 develop의 일정 화면 변경을 합칠 때 Track C 버튼에 제거된
  `setEditingMedicationId` 호출이 남아 frontend CI가 실패했다.
- `b118a71a`에서 대상 약 ID state와 현재 일정 편집 진입을 연결했다. 두 약이 있는 fixture에서
  대상 약만 표시하고 support_medication 쿼리를 보존하는 검사를 보강했다.
- 이 라운드 Frontend 전체 729개, TypeScript·oxlint·production build가 통과했고,
  [CI 35065376564](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/35065376564)의 10개 lane도 모두 통과했다.
- 이후 develop의 #638은 api.md·data-schema.md·testing.md의 동일 말미에 피드백 문서를 추가했다.
  #639의 Track C 추가 설명과 겹친 텍스트 충돌은 두 영역을 모두 보존해 해결했다. 코드 충돌은 없다.
- 책임 리뷰 요청에 따라 상황 선택·계획 자료 계약을 Current로 이동하고 참조를 정렬했다.
  #635는 병합 완료, #629는 승인됐지만 아직 OPEN이다. #629 병합 후 이번 기능 diff만 남는지
  최종 확인해야 한다. 이 PR이 #629를 대체하거나 먼저 배포하는 것으로 해석하지 않는다.
