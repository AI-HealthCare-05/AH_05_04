# #645 Wireframe 화면 정합 검증

2026-09-16. 구현 담당 권가빈, 단일 책임 리뷰어 남한솔. Frontend 화면·상태·접근성·기존 API 소비 보존을 검토한다.

## 변경 범위

Figma `hT9J1Rq8R1ynS4aCa4Zk55`의 `02_Wireframe`을 조회하고 실제 design context와 screenshot을 대조했다. 시작(`1157:111`)의 체크·문구 색상, 메뉴(`1373:23`)의 SVG 원본, 사용자 정보(`1384:22`)의 버튼·배지·표면 색상, 알림(`1540:372`)의 브랜드 제목·카드 위계·간격을 반영했다. SVG 6개는 Figma가 반환한 원본 bytes를 저장했다.

알림의 오늘/이전 그룹은 `delivered_at`의 Asia/Seoul 날짜로 표시한다. `read_at`은 읽음 표시만, `occurrence_local_date`는 원래 복약일 표시와 기존 Check-in route 전달에 계속 사용한다. 목록 표시만으로 read PATCH나 Check-in mutation을 호출하지 않는다. Loading/empty/error/retry, pagination, Push 진입 동작을 유지한다.

Home, Guide, Chat, 업로드·검수, Schedule, Report도 현재 노드와 기존 구현을 대조했다. 이미 반영된 구조와 runtime 데이터·안전 안내는 유지했다. Home의 비교용 색상 3안과 OTC 안내 tooltip, API에 없는 알림 약 개수·식사별 제목, 숨김/참고용 화면을 새 동작이나 예시 환자 데이터로 구현하지 않았다. #614의 메뉴 `복약 일정` 라벨·동의 UI·Home/Chat spacing을 유지했다.

공통 MobileShell, 디자인 시스템 구현, Backend/API/DTO/route, public gate, dependency/lockfile 변경은 없다. Code Connect 및 Figma 링크 목록 문서화는 범위 밖이다.

## 실행 환경과 결과

- Node 24.19.0. 기존 설치본을 격리 폴더에 복사했고 저장소 `pnpm-lock.yaml`과 설치본 lockfile 일치를 확인했다.
- Frontend unit: **674 PASS**, 40 files, `vitest run --maxWorkers=2`.
- 관련 최초 unit: 73 PASS. 추가 KST 자정 경계 테스트를 포함한 NotificationsPage: 25 PASS.
- lint / TypeScript / production build / `git diff --check`: PASS. 기존 main chunk >500kB 경고 유지.
- 새 `figma-screen-sync.spec.ts`: **320/390/412px 3 PASS**. 시작·메뉴·사용자 정보·알림을 확인하며 긴 이름/이메일, 480px 높이, 34px safe-area, descendant 잘림, 마지막 콘텐츠와 navigation 경계도 검사한다. 읽음·Check-in·프로필 자동 mutation 0건.
- 기존 전체 Chromium requirements: **47 PASS + 1 flaky**. 약관 재진입 직후 뒤로가기/Escape를 보내는 테스트가 화면 준비 전에 실행됐다. 해당 테스트에 heading focus 대기를 추가한 뒤 auth 파일을 두 번 실행해 **6 PASS**, retry 없이 통과했다. 제품 약관/승인 로직은 수정하지 않았다.
- 이메일 인증 opt-in Chromium: **1 PASS**.
- 현재 알림의 키보드·역사 기록 전달·조회 실패·이탈 후 응답 무시를 포함한 기존 6개 E2E 통과. 전체 실행 중 deferred loading fixture 타이밍 실패 1회가 있었고 별도 재실행과 최종 전체 실행 모두 통과했다.

최초 전체 unit 실행에서 `VITE_API_BASE_URL`을 누락해 API 테스트가 실패했다. 설정 후 기본 병렬 실행에서는 기존 Signup 포커스 assertion 1건이 간헐 실패했고, 해당 파일 단독 32 PASS 및 전체 2-worker 실행 674 PASS를 확인했다. 최초 직접 실행한 Vite 서버에는 약관 승인 시나리오 옵션도 누락되어 인증 E2E가 실패했다. 저장소 Playwright config와 같은 `VITE_SIGNUP_TERMS_APPROVED=true`를 합성 테스트 서버에 명시한 뒤 위 결과를 얻었다. 운영 설정·승인을 변경한 결과가 아니다.

스크린샷은 `frontend/test-results/requirements/figma-645-{start,menu,profile,notifications}-{320,390,412}.png`로 생성하고 로컬 전달 폴더에 보존했다. 시작 320px, 메뉴·사용자 정보 390px, 알림 320/390px 캡처를 Figma와 시각 대조했다.

## 선행 PR 호환성

화면 수정은 #614가 포함된 develop `f672c780`에서 분리했다. 갱신된 develop `91b6062d`와 다음 head들을 별도 로컬 checkout에서 검증했다.

- #629 `e6c250a1`: 최신 develop과 자동 결합 성공. 이번 화면 패치 적용 충돌 0. 관련 unit **136 PASS**, TypeScript PASS, Track C·신규 화면·인증 브라우저 **9 PASS**.
- #639 `b118a71a`: 이번 화면 패치 적용 충돌 0. 관련 unit **159 PASS**, TypeScript PASS, Track C·사유별 분기·신규 화면 브라우저 **8 PASS**.
- **두 PR의 동시 최종 결합은 미완료**: 최신 develop+#629에 #639를 합치면 `docs/testing.md`, `frontend/e2e-real-stack/track-c-round-trip.spec.ts`, `frontend/src/pages/SchedulePage.tsx`, `frontend/src/pages/TrackCPage.tsx`, `frontend/tests/SchedulePage.test.tsx`에서 선행 PR끼리 충돌한다. 임시 결합을 취소했고 해당 PR·담당자 변경을 덮어쓰지 않았다. 각각의 PASS를 세 PR 동시 결합 PASS로 표현하지 않는다. 선행 PR 정렬·병합 후 CI와 관련 E2E 재확인이 필요하다.

## 재현

`frontend`에서 Node 24와 저장소 lockfile 설치 후:

```bash
VITE_API_BASE_URL=http://localhost:8000 pnpm exec vitest run --maxWorkers=2
pnpm lint
pnpm exec tsc -b
pnpm build
pnpm exec playwright test e2e/figma-screen-sync.spec.ts e2e/notification-closeout.spec.ts
pnpm exec playwright test e2e/auth-and-account.spec.ts --repeat-each=2
pnpm run test:e2e:requirements
```

로컬 검증은 다른 작업의 4173/4174 서버를 재사용하지 않도록 전용 14645~14648 포트와 일시적 config로 실행했다. 일시적 config는 커밋하지 않는다. API는 합성 fixture로 가로챘으며 실제 Backend/DB/Provider/기기 Push 검증은 아니다. Python·DB·의료 AI 전체 suite는 Frontend 표시 변경 범위 밖이라 재실행하지 않았다. 지정 리뷰어 승인과 CI는 별도이며 병합·배포하지 않는다.

## PR #646 리뷰 보완: 텍스트 대비

남한솔 리뷰어의 WATCH 코멘트를 재확인했다. `#607d8b`와 흰색의 대비는 4.3717015837:1로, 17px/700 알림 CTA와 18px/800 프로필 CTA 등 일반 크기 텍스트의 WCAG AA 4.5:1 기준을 충족하지 않았다. 시작 화면 보조 문구와 링크, 프로필 배지, 알림 읽음 상태·날짜에도 대비 부족이 있었다.

Figma 원본 SVG·브랜드·장식용 action 색은 유지하고 시작·프로필·알림의 흰색 CTA 표면에만 기존 계열 `#5d7a88`(흰색 대비 4.5604989042:1)을 적용했다. 작은 강조·보조 글자는 기존 pressed 색 `#526b78`로 보정했다. 활성/비활성 버튼 동작과 크기는 유지한다.

[WCAG 2.2 Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)의 상대 휘도 계산을 사용했다. 새 모바일 E2E는 실제 computed foreground/background와 상속된 표면을 읽어 반올림 없이 4.5:1 이상인지 검사한다. 320/390/412px 화면·대비 및 기존 알림 E2E **9 PASS**, lint·TypeScript·git diff --check PASS. CSS와 E2E 검사만 보완하여 전체 unit suite는 재실행하지 않았다. 보완 후 캡처 12개를 갱신했다.
