# 회원가입 이메일 인증 gate 실제 연동 검증 — #431 / #549

## 범위와 기준

2026-09-15 기준 Frontend 오류 처리·재인증은 #562에 이미 구현되어 있다. 이번 후속은
그 동작을 **실제 FastAPI와 migration된 PostgreSQL**에 연결해 검증하는 자동 실행 경로다.
Backend API·DTO·gate 구현, SMTP 계정·Secret, Profile, Production 설정을 변경하지 않는다.

- Frontend 기준: #562가 병합된 `develop`의 `e158aced`. 최초 검증은 #562 `e5313ec4`에서 수행했고 PR 게시 전 최신 develop에서 다시 통과했다.
- Backend 검증 기준: #549 `effc4792` (`feat/431-signup-email-verification-gate`)
- #549는 확인 시점 OPEN / Draft / CHANGES_REQUESTED이며, 아래 결과는 미병합 코드의
  연동 증거다. `develop`의 현재 배포 동작이나 운영 인증 강제 활성화 완료를 뜻하지 않는다.
- [현재 계정 계약](../contracts/current/user-account.md)의 signup 목적별 동의 shape를 유지한다.
- [#532](https://github.com/AI-HealthCare-05/AH_05_04/issues/532),
  [#549](https://github.com/AI-HealthCare-05/AH_05_04/pull/549)의 이메일 인증 오류 연결 기준을 따른다.

## 실행

준비물: Docker, Node/pnpm, Chromium, #549 Backend checkout과 해당 lockfile의 Python 의존성.
Backend checkout에서 `uv sync --frozen --group app --group worker`, Frontend 작업 공간에서
`pnpm install --frozen-lockfile` 및 필요 시 `pnpm exec playwright install chromium`을 실행한다.

Frontend 작업 공간의 저장소 루트에서:

```bash
AUTH_GATE_BACKEND_ROOT=/absolute/path/to/backend-checkout \
  bash scripts/e2e/signup_gate.sh
```

Python은 기본적으로 Backend checkout의 `.venv/bin/python`을 사용한다.
다른 고정 의존성 환경을 사용할 때만 `AUTH_GATE_PYTHON=/absolute/path/to/python`을 지정한다.
Backend checkout이 #549의 typed gate 설정을 지원하지 않으면 컨테이너 생성 전에 실패한다.

Runner는 매 실행마다 다음을 수행한다.

1. 기존 env 파일을 읽지 않는 임시 작업 디렉터리를 만든다.
2. 고유 이름의 PostgreSQL 컨테이너와 tmpfs DB `signup_gate_e2e`를 만든다.
   DB 비밀번호는 실행 시 생성하며 기존 개발/test DB는 사용하지 않는다.
3. 지정 Backend의 `alembic upgrade head`를 적용한다.
4. 같은 격리 DB에 gate ON(18432)·OFF(18434) FastAPI를 띄운다.
   `ENV=local`, `EMAIL_PROVIDER=noop`으로 실제 SMTP/AI를 호출하지 않는다.
5. 인증 UI가 켜진 Frontend(18433)와 Chromium 테스트를 실행한다.
6. 정상·실패 종료 시 자신이 만든 서버·컨테이너·임시 파일만 정리한다.

18432–18434는 loopback 전용이며 이미 점유되면 실패한다. 기존 서버를 재사용하지 않는다.
공용 `test` DB를 재생성하는 Python CI runner와도 DB·컨테이너가 분리되어 있다.

## 시나리오

| 시나리오 | 검증 기준 |
| --- | --- |
| gate ON + 미인증 | 실제 signup `409 EMAIL_VERIFICATION_REQUIRED`, user row 0 |
| UI 미인증 제출 | signup 요청 0, 인증 안내·요청 버튼 focus |
| gate OFF + 미인증 | 실제 signup 201, 선택 목적 0개면 consent row 0 |
| 인증 완료 기록 만료 | 기존 완료 UI에서 signup 409, 완료 표시·코드 초기화, 재요청 가능 |
| 재인증 후 선택 동의 가입 | 실제 인증 API 재요청·확인 후 signup 201, 선택한 GUIDE/CHAT만 GRANTED 저장 |
| 선택 동의 0개 | 인증 완료 후 브라우저 signup 201, payload `consents: []`, consent row 0 |
| 인증 후 다른 요청이 먼저 가입 | `409 CONFLICT` + email/ALREADY_EXISTS, 이메일 focus, 인증 완료 상태 유지 |

응답을 Playwright route mock으로 대체하지 않는다. 만료만 재현을 위해 runner 소유 DB의
합성 인증 row `expires_at`·`created_at`을 과거로 이동한다. 인증 성공 상태·user·consent row를
직접 주입하지 않으며 새 HTTP 테스트 endpoint도 만들지 않는다.

인증 코드는 LOCAL에서만 제공하는 실제 요청 응답을 테스트 메모리에서 읽어 입력한다.
제품 Frontend가 해당 필드를 소비하도록 변경하지 않는다. 코드가 응답에 포함되는 환경이므로
이 테스트의 trace·video·screenshot은 저장하지 않는다. 실제 메일 수신·SMTP 지연·재시작
경계(#494/#561), non-local 가입 여부 비노출 검증은 이번 결과에 포함되지 않는다.

## 실행 결과

- 위 Backend/Frontend 기준으로 실제 연동 4개 테스트 통과.
- `VITE_API_BASE_URL=http://localhost:8000 pnpm exec vitest run tests/SignupPage.test.tsx tests/AuthApi.test.ts`: 29개 통과.
- `pnpm lint`, `pnpm build`, `bash -n scripts/e2e/signup_gate.sh`, `git diff --check`: 통과.
- build는 500 kB 초과 번들 경고를 출력했다. 이번 변경은 제품 코드나 의존성을 변경하지 않는다.
- Backend 단위 테스트·전체 Python CI는 실행하지 않았다. 이 후속은 Backend를 수정하지 않고
  실제 migration·HTTP 요청·DB 결과를 위 E2E에서 확인한다.
- 초기 실행은 기존 Python 환경의 `pgvector` 미설치로 실패했고, #549 lockfile의 app/worker
  그룹을 갖춘 별도 환경에서 해결했다. runner 개발 중 발견한 서버 PID 종료 처리도 보완했다.

## 완료와 남은 gate

- 이 테스트가 통과해도 #549 승인·병합이나 운영 gate 활성화를 자동 수행하지 않는다.
- #431 종료에는 #562/#549 병합·리뷰 상태와 승인된 운영 Provider/UI 배포 조건을 별도로 확인한다.
- #532는 #562 책임 리뷰와 병합 후 종료 여부를 판단한다.
- Profile 목적별 동의 관리 작업은 별도 작업으로 유지한다.
