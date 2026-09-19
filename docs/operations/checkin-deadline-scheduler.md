# Track B Check-in 기한 정기 처리 (#839)

구현·조율 권가빈 (`hazelnutflavoured`), 기술 리뷰 담당자 지정 필요.
이 문서는 기존 #430 계열 one-shot 명령(`app.commands.generate_unconfirmed_checkins`)을
소비하는 운영 설정이다. API·DTO·DB·상태 의미·잠금 순서를 변경하지 않는다.
Check-in 상태 의미와 deadline 계산의 정본은
[Check-in 계약](../contracts/targets/post-mvp-1/checkin-v1.md)과
[data-schema](../data-schema.md)이며, 리포트 표시 규칙의 정본은
[현재 리포트 계약](../contracts/current/medication-report-v1.md)이다.

## 배경

`generate_unconfirmed_checkins`는 one-shot 명령으로만 존재했고 이를 주기 호출하는
런타임이 배포 설정에 없었다. 그 결과 `confirmation_deadline_at`이 지난 occurrence가
`PENDING`으로 남아 복약 리포트에 계속 `예정`으로 표시되고, `confirmation_rate`의 분모에
`UNCONFIRMED`가 들어가지 않아 기록 확인률이 실제보다 높게 집계됐다. 리포트는 조회 시점에
상태를 바꾸지 않으므로(계약상 금지) 런타임 없이는 해소되지 않는다.

## 실행 방식과 범위

같은 Backend 이미지의 `checkin-deadline-scheduler` 서비스로 실행한다. 설정은
`infra/docker/docker-compose.prod.yml`, 실행 코드는 `app.commands.schedule_checkin_deadlines`
및 `app.commands.generate_unconfirmed_checkins`다. 알림 게시, occurrence 생성,
Worker·Redis·외부 Provider 호출을 추가하지 않는다.

이 서비스에는 opt-in profile을 두지 않는다. 기한 처리는 알림처럼 켜고 끄는 기능이 아니라
Check-in 상태 정합성에 필요한 core runtime이고, 멈춰 있으면 사용자 화면에 과거 기록이 계속
`예정`으로 남기 때문이다. `scripts/deployment.sh`는 이 서비스를 `DEPLOY_SERVICES`에 포함해
다른 핵심 서비스와 함께 기동하고, 배포 직후 running 상태를 확인해 실패 시 배포를 중단한다.
알림 스케줄러는 기존대로 `notifications` profile opt-in이며, 두 서비스는 서로의 시작 여부에
의존하지 않는다.

| 항목 | 설정·근거 |
| --- | --- |
| 첫 실행 | 컨테이너 시작 직후 |
| 주기 | 매 성공/실패 실행 종료 후 60초 대기. 같은 프로세스에서 실행이 쌓이지 않음 |
| timeout | 배치 전체 45초, asyncio 취소로 진행 중 transaction rollback |
| 처리 한도 | 한 실행당 500 occurrence (`FOR UPDATE SKIP LOCKED`). 초과분은 다음 주기 |
| 재시도 | 실패 후 60초 뒤 다음 배치. 무한 즉시 retry 없음. 단발 명령은 성공 0·실패 1 |
| 중지 | SIGTERM/SIGINT가 활성 배치를 취소·DB 정리. Compose는 15초 후 강제 종료 |
| 프로세스 장애 | `restart: unless-stopped`; 운영자가 stop한 서비스는 자동 재개하지 않음 |
| 데이터 권한 | 기존 Runtime DB 계정. Provider·Redis·Web Push·Admin·Migration 자격증명과 의료 파일 mount 없음 |
| 로그 | 성공 시 UTC 완료 시각·소요 시간·`processed_count`·`duplicate_count`, 실패 시 시각·소요 시간·고정 reason만 출력 |
| 로그 보존 | Docker json-file 10 MB × 3 rotation |

60초는 초기 운영값이며 기한 처리 SLA가 아니다. 한 주기에 500건 상한이 있으므로 최초 도입
시점에 누적된 backlog는 여러 주기에 걸쳐 소진한다. 지연을 줄이려고 deadline 계산이나 500
한도를 임의로 바꾸지 않는다.

## 최초 도입 시 backlog 소진

배포로 서비스가 기동하면 기존 backlog도 주기마다 500건씩 자동으로 소진한다. 즉시 소진이
필요하면 승인된 이미지로 단발 명령을 `processed_count=0`이 나올 때까지 반복 실행한다.

```bash
cd ~/project
docker compose run --rm --no-deps \
  --entrypoint /app/.venv/bin/python checkin-deadline-scheduler \
  -m app.commands.generate_unconfirmed_checkins
```

backlog를 줄이려고 `medication_checkin` row를 직접 INSERT하거나 occurrence 상태를 수동으로
바꾸지 않는다. 사용자가 뒤늦게 복용·미복용을 입력하면 기존 정정 계약에 따라 `UNCONFIRMED`가
audit에 남고 현재 결과만 바뀐다.

## 적용과 중지

정상 경로는 `scripts/deployment.sh` 실행이며, 아래는 장애 조사·수동 개입용이다.

```bash
cd ~/project
# 상태와 최근 결과 확인
docker compose ps -a checkin-deadline-scheduler
docker compose logs --since 10m --timestamps checkin-deadline-scheduler

# DB migration, 이미지 업데이트, 장애 조사 전 먼저 중지
docker compose stop checkin-deadline-scheduler
# 재개 또는 승인된 새 APP_VERSION 적용 후 재생성
docker compose up -d --no-deps --force-recreate checkin-deadline-scheduler
```

`scripts/deployment.sh`는 migration 전에 이 서비스를 중지하고 중지되지 않으면 배포를
차단하며, 서비스 기동 후에는 running 상태를 확인해 실패 시 배포를 중단한다. 스크립트 밖에서
migration을 수행할 때도 위 stop을 먼저 수행한다. 수동으로 stop한 서비스는 자동 재개하지
않으므로, 조사 후 위 명령이나 다음 배포로 반드시 다시 기동한다.

## 장애 감지·대응

| 신호 | 확인·대응 |
| --- | --- |
| `status=failed` 한 건 | reason=timeout/batch_error. DB 연결·잠금·migration head·Runtime 권한 확인. 예외 원문은 수집하지 않음 |
| 2회 연속 실패 또는 최근 성공이 3분 이상 없음 | 담당 운영자에게 장애로 전달. 컨테이너 재시작 횟수와 로그 확인 |
| 3회 연속 `processed_count=500` | backlog 소진 속도가 유입을 못 따라가는 상태. occurrence 생성량과 DB 잠금 조사 |
| 리포트 `overdue_pending_count`가 계속 0보다 큼 | 이 서비스의 실행 상태를 먼저 확인. 정상 동작 중이면 한 주기 뒤 감소하는지 확인 |
| stop 이후 로그 없음 | 의도한 정지인지 작업 기록 확인. 자동 재개하지 않음 |

`overdue_pending_count`는 리포트 응답에 포함되며 사용자 화면에도 지연 안내로 노출된다.
이 값이 0으로 수렴하지 않으면 사용자에게는 과거 기록이 계속 `예정`으로 보인다.

## 증빙과 인계

Commit SHA, migration head, 환경, 합성 fixture, 실행 결과·재현 명령·PR/CI 링크를
`docs/validation/track-b/`에 기록한다. 실제 배포 시에는 적용 환경·이미지 digest·승인 링크·
실행자·최초 및 다음 주기 성공·backlog 소진 결과를 남긴다. Local 통과로 Production 적용을
주장하지 않는다.
