# #139 Track C Frontend API 연결 검증

- 날짜: 2026-09-16
- 기준: `origin/develop`의 `a542bcc2` (#608 Support/Plan 생성, #618 Plan lifecycle 병합 포함)
- 연결 Issue: [#139](https://github.com/AI-HealthCare-05/AH_05_04/issues/139)
- 이번 연결분 구현: 권가빈. 단일 책임 리뷰어: 남한솔.
- 리뷰 범위: Frontend 진입·상태·접근성, Track C API 소비, 멱등 재시도, Safety 차단, 약 ID 전달과 공개 경계. 전체 #139의 남은 UI·후속 범위 담당 배정은 이 문서로 변경하지 않는다.
- 공유 계약 변경 없음. Backend·DB·Provider 구현 변경 없음.

## 연결 범위

개발 서버에서 확정된 `NOT_TAKEN` 복약 기록의 **이유와 도움 찾기**로 진입한다.
날짜·회차를 다시 조회하고 현재 check-in ID/revision을 사용한다. `PENDING`,
`UNCONFIRMED`, `TAKEN`, 취소된 회차는 진입할 수 없다.

| 순서 | 소비하는 기존 API | UI 경계 |
| --- | --- | --- |
| Safety | `POST /api/v1/safety-assessments` | 사용자가 증상 없음을 직접 확인하면 `symptom_codes: []`. `ROUTINE/NORMAL`만 계속 진행 |
| Barrier | `PUT /api/v1/medication-checkins/{id}/barrier-response` | 여섯 코드 중 하나 또는 명시적 `DECLINED/null`. 미제출은 요청 없음 |
| Offer | `GET /api/v1/barrier-responses/{id}/supports` | 서버의 0·1개 제안과 승인된 `support_copy` 표시. 빈 응답은 `NO_ELIGIBLE_SUPPORT` 확인 |
| Plan 생성 | `POST /api/v1/support-action-plans` | 확인 체크와 저장 클릭 후에만 생성. 서버의 rule/copy version 그대로 제출 |
| Plan 조회 | `GET /api/v1/support-action-plans/{id}` | 생성 응답의 snapshot 대신 현재 상태 재조회. URL 재진입·새로고침도 조회만 수행 |
| Plan 종료 | `PATCH /api/v1/support-action-plans/{id}` | 명시적 완료/취소 확인 후 저장하고 GET으로 재확인 |

기준 계약은 [Safety/Barrier](../contracts/proposed/track-c-safety-barrier-api-193.md),
[Support/Plan 생성](../contracts/proposed/track-c-support-plan-api-194.md),
[현재 Plan lifecycle](../contracts/current/track-c-plan-lifecycle-617.md)와 병합된 DTO·테스트다.
Proposed 문서의 미구현 부분까지 구현된 것으로 간주하지 않는다.

- Reminder는 저장된 `prescription_version_medication_id`를 기존 일정 화면으로 전달한다. 정확히 일치하는 약의 확인·설정 버튼을 제공하며, 현재 처방에 없으면 대체 약으로 연결하지 않는다.
- 일정 화면 열기·계획 생성만으로 완료하지 않는다. 사용자가 해당 일정을 확인했거나 저장했다고 명시적으로 확인해야 완료 요청을 보낸다.
- 조회 DTO에는 frozen copy 본문이 없으므로 재진입한 계획은 지원 종류와 현재 상태를 표시한다. 최신 Offer 문구를 과거 계획의 문구로 대신하지 않는다.
- 수정 가능한 자유 입력·임의 실행 설정·Citation·RAG 결과를 만들지 않는다.

## 오류와 재진입

메모리의 기존 `LogicalMutationAttempt`를 재사용한다. 대상·본문·revision이 같은 요청은
응답 유실 뒤 같은 키로 재시도한다. 중복 클릭을 잠그고 요청 결과가 불명확할 때 입력을
보존한다. token 이외의 건강 정보·멱등 키를 Web Storage에 추가하지 않는다.

401은 세션을 정리하고 로그인으로 이동한다. 403/404는 일반적인 접근 불가 안내,
409는 이전 흐름 차단을 표시한다. 422·네트워크·5xx는 원문을 숨기고 같은 요청의 재시도를
제공한다. 다른 check-in/revision/Safety에 속한 응답은 사용하지 않는다.

**현재 한계:** Safety/Barrier GET이 없어 새로고침 후 기존 revision의 진행 상태를 복원할 수
없다. 초기 expected revision은 0이며, 이미 생성된 흐름이면 409로 멈춘다. 임의 revision을
추측하거나 check-in을 자동 정정하지 않는다. 알려진 Plan URL의 조회·취소는 별도로 가능하다.

## 공개 및 미구현 경계

`import.meta.env.DEV`로 route·lazy import·진입 버튼을 제한했다. Production 번들에서
Track C route, API adapter, 진입 문구가 제거되는 것을 확인했다. 공개 승인이나 운영 활성화
완료를 의미하지 않는다. [외부 승인 게이트](../release-gates/post-mvp-1-external-approvals.md)는 유지한다.

현재 Safety foundation에는 승인된 증상 코드와 임상 안내 카탈로그가 없다. 증상이 있거나
불확실하면 코드를 만들지 않고 일반 도움 흐름을 중단한다. 서버가 non-ROUTINE 또는
non-NORMAL을 반환해도 Barrier/Support를 호출하지 않는다. 이 화면은 합성 데이터 연결
검증용이며 임상 triage UI가 아니다.

Follow-up, RAG/Citation, 승인된 임상 코드·문구, Safety/Barrier 복원·정정 UX는 남아 있다.
**#139 전체 완료나 종료로 처리하지 않는다.**

## 디자인·접근성

### PR #629 리뷰 반영 (2026-09-16)

QA clone만 대조했던 한계를 수정하고 아래 최신 원본의 design context와 screenshot을
직접 확인했다. 파일 내 일부 원본 이름에는 WIP/후보 표기가 남아 있으므로 디자인 조회가
API 또는 임상 계약 승인을 뜻하지 않는다.

| 화면 | 대조 원본 | 반영 |
| --- | --- | --- |
| Safety | [SAFETY-01](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=1487-80) | 공통 Dosey Header·5개 탭, 질문·설명·증상 유무 CTA, 36px 상단 여백·64px 선택 버튼 |
| Barrier | [C-02A](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=1489-524) | 26/36px 제목, 여섯 radio, 8px 선택 간격·56px 최소 높이, 선택/건너뛰기 CTA |
| Support | [SUPPORT-F01](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=506-7) | 공통 shell·카드·하단 실행 영역. 서버의 단일 제안·승인 copy·명시적 확인 유지 |
| Plan | [C-07](https://www.figma.com/design/hT9J1Rq8R1ynS4aCa4Zk55/?node-id=1519-80) | 상태→제목→방법 카드→하단 실행 영역. 현재 GET 상태와 완료/취소 확인 유지 |

개발 환경 안내 문구는 제품 화면에서 제거했다. DEV-only route와 Production 제외 조건은 유지한다.
기존 MobileShell·Dosey mascot·Button·Card를 재사용하고 스타일은 Track C 내부로 제한했다.
하단 메뉴의 다섯 버튼은 기존 실제 경로로 연결하며 일정 context를 표시한다.

**계약을 보존한 차이:** 디자인의 알림 시점·일상 행동 편집, 언제/어디서·다시 살펴볼 때는
현재 DTO에 없어 추가하지 않았다. Support 제목·본문·CTA는 서버 승인 copy를 그대로 쓴다.
계획 생성만으로 실제 실행을 단정하지 않도록 `진행 중 / 내 실천 계획`으로 표시한다.
MEDICATION_CONCERN에는 기존 `약에 대한 걱정이 있었어요`를 유지한다. 디자인의 과거 증상까지
포함한 문구로 바꾸면 빈 symptom_codes로 확인된 Safety 뒤에 증상 입력을 일반 지원으로
보내는 의미가 될 수 있어, 임상 계약 없이 이 부분을 확대하지 않았다.
Safety의 불확실한 경우는 `증상이 있어요` 선택 안내로 기존 차단 경로를 유지한다.
따라서 원본 전체의 1:1 구현이나 #139 완료를 주장하지 않는다.

320·390·412px browser 검증에서 공통 shell, 여섯 radio와 방향키 단일 선택, 단계 전환 focus,
긴 서버 문구 줄바꿈·세로 스크롤·가로 overflow 부재, 명시적 확인과 lifecycle을 확인했다.
기존 shell 최대 폭 390px를 유지한다. Safety·Barrier·Offer·Plan screenshot은 로컬
`frontend/test-results/track-c-*.png`이며 직접 시각 검토했고 생성물은 commit하지 않는다.

### 리뷰 반영 후 재검증

- Node 24.19.0 Frontend 전체 Vitest: **650 PASS**.
- 전체 요구사항 browser: **37 PASS**, Track C 320/390/412px **3 PASS** 포함.
- 새 격리 PostgreSQL·Redis와 이 PR Backend/Frontend 실제 왕복: **1 PASS**.
  API interception 없이 생성→새로고침→약 ID 일정 전달→완료/취소→GET snapshot 보존 확인.
- Frontend lint·TypeScript·production build 및 production Track C 제외: PASS.
- 기존 500kB main chunk 경고는 남아 있다.
- Python 구현·계약 변경이 없어 아래 최초 검증의 Python suite는 재실행하지 않았다.

## 최초 연결 검증 결과

- Node 24.19.0: Frontend Vitest **650 PASS**.
- Frontend lint, TypeScript, production build PASS. 기존 main chunk 500kB 경고는 유지.
- Playwright mock API: 320·390·412px 명시적 생성→재조회→새로고침→취소 **3 PASS**.
- 전체 요구사항 브라우저 회귀 **37 PASS** (위 3개 포함), 별도 이메일 인증 회귀 **1 PASS**.
- 실제 API 왕복: 새 격리 PostgreSQL·Redis, 최신 migration, 기존 합성 Report fixture와
  최신 FastAPI/Vite를 사용. API interception 없이 복약 기록→Safety→Barrier→Offer→Plan 생성,
  새로고침, 정확한 약의 일정 전달, 완료·취소, API 재조회와 snapshot 불변 **1 PASS**.
- Ruff check·format PASS, Mypy **735 files PASS**.
- Python Contract/Service **442 PASS** (테스트용 설정, Docker 이미지 검사 포함).
- Python 전체 DB/Worker/AI suite는 Frontend 소비자 변경 범위 밖이므로 로컬에서 재실행하지 않았다.
  required CI의 최종 상태는 PR에서 확인한다.

실행 예시(Frontend 디렉터리, Node 24, 잠금 파일 의존성 설치 후):

```bash
VITE_API_BASE_URL=http://localhost:8000 pnpm test
pnpm lint
pnpm build
pnpm exec playwright test e2e/track-c-integration.spec.ts
```

실제 왕복 테스트는 **새 격리 `dosey_e2e` DB**에 최신 migration을 적용하고
`app.release_validation.report_round_trip_fixture`를 한 번 실행한 후 사용한다.
Backend의 CORS와 Frontend의 `VITE_API_BASE_URL`은 해당 격리 서버 주소에 맞춘다.
운영·공유 개발 DB나 실제 사용자 계정에서 fixture를 실행하지 않는다.

```bash
TRACK_C_E2E_SYNTHETIC=1 \
TRACK_C_E2E_API_URL=http://127.0.0.1:18339 \
REAL_STACK_WEB_URL=http://127.0.0.1:4179 \
pnpm exec playwright test --config=playwright.real-stack.config.ts \
  e2e-real-stack/track-c-round-trip.spec.ts
```
