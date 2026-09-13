# Track B 알림 정기 실행·복구 (#434)

구현·조율 권가빈 (`hazelnutflavoured`), 기술 리뷰 송은영 (`phina-io`).
이 문서는 기존 #430 명령을 소비하는 운영 설정이다. API·DTO·DB·게시 조건·잠금 순서를
변경하지 않는다. [알림 계약 문서](../contracts/proposed/track-b-notifications-v1.md)는
저장소에 여전히 Proposed로 남아 있으며 이 PR에서 상태를 승격하지 않는다.

## 실행 방식과 범위

기존 배포는 Backend 이미지를 EC2의 `~/project/docker-compose.yml`에 조립한다.
같은 이미지의 `notification-scheduler` 서비스를 `notifications` profile로 명시적으로
시작한다. 설정은 `infra/docker/docker-compose.prod.yml`, 실행 코드는
`app.commands.schedule_notifications` 및 `app.commands.process_notifications`다.
일반 배포 스크립트는 이 서비스를 자동 시작하지 않는다. Worker·Redis·외부 Provider 호출이나
알림 이외의 occurrence 생성·UNCONFIRMED 스케줄링을 추가하지 않는다.

| 항목 | 설정·근거 |
| --- | --- |
| 첫 실행 | 컨테이너 시작 직후 |
| 주기 | 매 성공/실패 실행 종료 후 60초 대기. 같은 프로세스에서 실행이 쌓이지 않음 |
| timeout | 생성·게시 전체 45초, asyncio 취소로 진행 중 transaction rollback. 정리 시간은 별도 |
| 처리 한도 | 기존 각 단계 500 occurrence. 한 occurrence에 최초·재알림이 있어 게시/취소 row 수는 500 초과 가능 |
| 재시도 | 실패 후 60초 뒤 다음 배치. 무한 즉시 retry 없음. 단발 명령은 성공 0·실패 1 |
| 중지 | SIGTERM/SIGINT가 활성 배치를 취소·DB 정리. Compose는 15초 후 강제 종료 |
| 프로세스 장애 | `restart: unless-stopped`; 운영자가 stop한 서비스는 자동 재개하지 않음 |
| 데이터 권한 | 기존 Runtime DB 계정에 notification_record SELECT·INSERT·UPDATE만 부여(DELETE·TRUNCATE 차단). Provider·Redis·Admin·Migration 자격증명과 의료 파일 mount 없음 |
| 로그 | 성공 시 UTC 완료 시각·소요 시간·생성/게시/취소 건수, 실패 시 시각·소요 시간·고정 reason만 출력 |
| 로그 보존 | Docker json-file 10 MB × 3 rotation. 장기 증빙은 접근 통제된 운영 저장소로 수집 |

60초는 앱 내부 알림의 초기 운영값이며 전달 SLA가 아니다. 45초를 모두 쓰면 다음 시작까지
약 105초와 정리 시간이 걸린다. 501개 동시 도래 대상은 잠금 경합이 없는 합성 검증에서
500→1→0으로 소진한다. 생성은 기존 UUID 순서로 미래 occurrence도 대상으로 하므로
대량 미래 대상 유입·잠금 경합에서는 due backlog를 별도 확인해야 한다. 처리 지연을
해소하려고 게시 조건·deadline·500 한도를 임의로 바꾸지 않는다.

## 적용 전 확인

Local 검증과 실제 배포는 별개다. Production에서는 #230의 실행자·승인자·접근권한,
기존 공개/Privacy 게이트와 #430 병합·migration 적용 증빙을 먼저 확인한다.
[배포 절차](../deployment.md)의 이미지·migration head·역할 권한 검증 후에만 시작한다.
아래 명령은 승인된 EC2 작업 디렉터리 `~/project` 기준이다. 환경파일은 기존 `.env`를
사용하며 내용을 터미널·Issue·PR에 출력하지 않는다. `docker compose config` 전체 출력도
자격증명을 포함할 수 있어 공유하지 않는다.

```bash
cd ~/project
# 승인된 APP_VERSION 이미지를 준비한 후 서비스만 시작
# --no-deps는 위 migration/DB 검증 완료를 전제로 한다.
docker compose --profile notifications up -d --no-deps notification-scheduler
# 상태·최근 결과·마지막 성공 시각 확인
docker compose ps -a notification-scheduler
docker compose logs --since 10m --timestamps notification-scheduler
# 시작 직후 success 한 건을 확인한 뒤 다음 주기의 새 완료 시각을 확인
```

## 중지·재실행·업데이트

```bash
# DB migration, 이미지 업데이트, 장애 조사 전 먼저 중지
docker compose stop notification-scheduler
# 정지 상태 확인
docker compose ps -a notification-scheduler
# 같은 승인 이미지로 단발 재실행: 아래 명령의 exit code도 확인
docker compose --profile notifications run --rm --no-deps \
  --entrypoint /app/.venv/bin/python notification-scheduler \
  -m app.commands.process_notifications
# 재개 또는 승인된 새 APP_VERSION 적용 후 재생성
docker compose --profile notifications up -d --no-deps --force-recreate notification-scheduler
```

`restart`만으로는 변경된 이미지/환경을 적용하지 않는다. 기존 `scripts/deployment.sh`는
알림 서비스를 migration 전에 중지하고 실행 중이면 배포를 차단한다. 스크립트 밖에서
migration을 수행할 때도 위 stop을 먼저 수행하고, DB head·권한·API 확인 후 재생성한다. 이전 버전으로 복구할 때도 schema 호환 여부를 담당
리뷰어와 확인한다. `notification_record` 삭제, attempt 초기화, DELIVERED→PENDING 변경,
deadline 연장으로 재발송하지 않는다. 이력은 그대로 두고 같은 명령을 재실행한다.

생성 커밋 이후 장애가 나면 PENDING이 남는다. 게시 transaction이 실패/취소되면 그 단계는
rollback되고 다음 실행이 재검증하여 게시 또는 취소한다. 게시 커밋 직후 로그가 유실돼도
재실행은 이미 DELIVERED인 row를 재게시하지 않는다. 여러 프로세스가 겹쳐도 기존
occurrence 잠금·SKIP LOCKED·unique constraint를 사용한다. 운영에서는 한 서비스로 유지한다.

## 장애 감지·대응

| 신호 | 확인·대응 |
| --- | --- |
| `status=failed` 한 건 | reason=timeout/batch_error, DB 연결·상태·잠금·migration head·Runtime 권한 확인. 예외 원문을 수집하지 않음 |
| 2회 연속 실패 또는 최근 성공이 3분 이상 없음 | 담당 운영자에게 장애로 전달. 컨테이너 종료/재시작 횟수와 로그 확인, 필요 시 stop 후 단발 복구 |
| 3회 연속 생성 500건, 또는 due backlog/최고 지연이 3분 이상 지속 | 아래 집계 확인. 미래 생성량·DB 잠금·처리 속도 조사. 성공 0건만으로 backlog 없음이라고 판단하지 않음 |
| ineligible pending 잔존 | 다음 주기 취소 건수 및 감소 확인. 3분 이상 지속 시 잠금·실패 조사 |
| stop 이후 로그 없음 | 의도한 정지인지 작업 기록 확인. 마지막 성공이 오래됐다는 이유만으로 자동 재개하지 않음 |

현재 확인 수단은 Docker 상태·로그와 집계 SQL이다. 중앙 alert/수집기 연결은 자동으로
생기지 않는다. 적용 환경 운영자가 위 기준을 기존 관제에 연결하고 수신 담당자를 지정해야
한다. 마지막 성공 시각은 가장 최근 `status=success completed_at=...`이며 프로세스가
running인 것만으로 배치 성공을 판정하지 않는다. 실패 로그에는 부분 커밋 건수를 추정해
기록하지 않는다.

[`scripts/operations/notification_backlog.sql`](../../scripts/operations/notification_backlog.sql)은
read-only transaction에서 due/ineligible pending 건수·가장 오래된 due 시각·미생성 건수만
조회한다. 승인된 DB 접근 경로에서 실행하며 사용자·알림 식별자와 내용은 조회하지 않는다.
운영자용 `psql` 연결은 기존 접근 절차를 사용하고 비밀번호를 argv로 전달하지 않는다.

## 증빙과 인계

[검증·Track B 인계 기록](../validation/track-b/issue-434-notification-operations.md)에
Commit SHA, migration head, 환경, 합성 fixture, 실행 결과·재현 명령·PR/CI 링크를 기록한다.
실제 배포 시에는 별도로 적용 환경·이미지 digest·승인 링크·실행자·최초 및 다음 주기 성공·
중지/재시작·집계 결과를 남긴다. Local 통과로 Production 적용을 주장하지 않는다.
