# #628 최신 처방 일정 조회 검증

- 이슈: [#628](https://github.com/AI-HealthCare-05/AH_05_04/issues/628)
- 구현 담당: 권가빈. 단일 책임 리뷰어: 송은영 (Backend/API·소유권·Frontend 소비 범위).
- 상태: 로컬 합성 검증 완료, 전체 runner 진행 중; 책임 리뷰·병합·Production 재검증은 별도.
- 변경 계약: [일정 API v1](../contracts/proposed/track-b-schedule-api-v1.md).

## 재현과 변경

실제 ASGI API와 격리된 PostgreSQL에 이전 처방 약 3개, 최신 처방 약 2개를 만든다.
수정 전에는 latest가 2개를 반환하고 schedule_items에 이전 약 3개가 추가되어
신규 회귀 2건(생성 시각 상이/동일)이 정확히 집합 불일치로 실패했다.

조회 서브쿼리는 SELF 처방을 `created_at DESC, id DESC LIMIT 1`로 선택한다.
외부 join은 그 처방의 active_version_id에만 연결한다. occurrence 조회와 쓰기
경로는 바꾸지 않는다. 여러 처방으로 PARTIAL을 만들던 기존 fixture는 한 처방의
두 약(설정됨/미설정)으로 바꿔 PARTIAL 의미 검증을 유지했다.

## 실행 결과

- 일정 API + occurrence 원래 약 인계 + Check-in 통합: **62 passed**.
- 신규 회귀: 3+2 → 최신 2개, 생성 시각 동률 ID 정렬, 타인의 더 최신 처방 제외,
  최신 약별 일정 PUT → occurrence 생성 → 원래 약 GET → TAKEN Check-in 성공.
- 이전 별도 처방의 occurrence/약 snapshot/Check-in 보존과 최신 SETUP_REQUIRED/READY
  집계 확인. 기존 비활성 version 제외·멱등·revision·소유권 회귀도 통과.
- 전체 Ruff check/format 통과 (974 files). 전체 Mypy 통과 (735 source files).
- `git diff --check` 통과. 전체 diff와 Markdown 참조/상태 경계 검토.
- `bash scripts/ci/run_test.sh`: 전용 PostgreSQL·Redis에서 진행 중.

테스트는 비식별 합성 fixture만 사용했다. 운영 ID 3개의 실제 처방 소속과 배포 SHA는
조회하지 않았다. Production E2E 통과나 배포 완료를 주장하지 않는다. 기존 다른 처방의
알림/일정 자체를 자동 취소하지 않으며, 두 GET 사이 처방 변경 시 Frontend의 기존
ID 불일치 차단은 유지한다.
