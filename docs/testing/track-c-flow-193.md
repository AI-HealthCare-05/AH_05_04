# #193 Safety → Barrier API 검증 기록

- 기준일: 2026-09-15
- 상태: 구현 브랜치 Local 합성 검증, 지정 리뷰어·외부 승인·Production 검증 전
- 구현 담당: 김지혜 인계 범위
- 책임 리뷰: Issue #193에 지정된 Backend·Transaction·Security 리뷰 필요

## 구현 범위

- `POST /api/v1/safety-assessments`
- `PUT /api/v1/medication-checkins/{checkin_id}/barrier-response`
- SELF Check-in parent chain과 타인/미존재 동일 404
- 현재 `NOT_TAKEN` 및 Check-in revision 검증
- Safety·Barrier revision별 append-only 저장
- 최신 `ROUTINE` Safety가 있을 때만 Barrier 허용
- non-`ROUTINE` Safety 정정 시 이력을 보존하고 ACTIVE ActionPlan 동기 취소
- `Idempotency-Key` 중복 재현·상이 payload 충돌·동시 최초 요청 직렬화
- 자유 텍스트 증상과 `ANSWERED`/`DECLINED` 조합 검증

## 현재 정책 경계

Approved Contract Freeze v4는 빈 `symptom_codes=[]`를 증상 없음 확인과 `ROUTINE`으로
고정하지만, 비어 있지 않은 code 목록·위험도 매핑·고정 안내 문구와 version 자료는
아직 저장소에 승인 artifact로 연결되지 않았다. 구현은 빈 목록을 `ROUTINE/NORMAL`로
처리하고 그 밖의 목록을 `UNKNOWN/UNKNOWN_RISK`로 fail-closed 처리한다. 합성 foundation의
`message_code`, `copy_version`, `source_version`은 Production 승인 자료가 아니다.

## 실행 결과 (최신 develop #580 반영 후 재검증)

```text
pytest backend/app/tests/track_c/test_track_c_api.py \
  backend/app/tests/medication_checkins/test_medication_checkin_api.py \
  tests/services/test_track_c_handler_config.py \
  tests/services/test_track_c_operational_config.py tests/contract -q
444 passed

pytest backend/app/tests -q --tb=short
1658 passed, 2 skipped

ruff check . / ruff format . --check
passed

mypy backend/app ai_worker
675 source files passed
```

전용 임시 컨테이너 `codex-193-api-test`, loopback port 15493, PostgreSQL 17 + pgvector,
tmpfs의 `test` DB 및 비식별 합성 fixture를 사용했다. 공유 개발 DB는 변경하지 않았다.
테스트 env는 `DB_PORT=15493`, `DB_EXPOSE_PORT=15493`, DB 계정·암호는 폐기 가능한 synthetic 값이다.
기존 의존성 venv를 사용했으며 contract subprocess에는 같은 `UV_PROJECT_ENVIRONMENT`와
`UV_NO_SYNC=1`을 전달했다. 초기 실행의 잘못된 테스트 멱등 키와 subprocess 의존성 누락을
수정한 뒤 위 결과를 확인했다.
전체 Backend 최초 실행은 일부 독립 engine 테스트의 `DB_PORT` 미설정으로 2 failures·15 errors가
발생했다. 같은 전용 포트로 통일한 재실행에서 위 1,658 passed·2 skipped를 확인했다.
이 결과는 배포 환경 검증, 실제 환자 데이터 검증, 의료·Privacy·Safety 승인 또는
`PUBLIC_TRACK_C` 해제 근거가 아니다.

추가 회귀는 Safety/Barrier 각각 같은 키·다른 키의 동시 요청, snapshot 실패의
assessment·Plan 취소 rollback, Check-in 정정 이후 최초 snapshot 재현과 새 흐름 재시작,
배열 안 자유 텍스트 거부, URGENT/EMERGENCY/UNKNOWN의 Barrier 차단을 포함한다.

## 남은 차단 조건

- 승인된 non-empty symptom code 목록과 `URGENT|EMERGENCY|UNKNOWN` 판정표
- 승인 고정 문구, `message_code`, `copy_version`, `source_version` 정본
- 지정 Backend·Transaction·Security 리뷰와 Frontend 소비 계약 확인
- 배포 환경 검증 (전체 Backend·contract 로컬 합성 검증은 위 실행 완료)
- #195 Check-in 정정 transaction의 실제 Track C invalidation adapter 연결
