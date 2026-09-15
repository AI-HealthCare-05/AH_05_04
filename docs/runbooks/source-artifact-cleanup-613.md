# #613 MFDS 적재 실패 Artifact cleanup

상태: 구현 완료 / #593 executor 권한 검증 전 실제 실행 금지

이 절차는 #591 MFDS 적재에서 원본 Artifact 보존 뒤 DB transaction이 실패한 경우만 다룬다. 30일 보존·이중 승인 합성 검증인 #347 cleanup과 실행 목적 및 기록을 공유하지 않는다.

## 역할과 저장 위치

- writer는 Artifact 저장과 cleanup 요청 기록만 수행한다. 삭제 capability와 executor DB credential을 주입하지 않는다.
- cleanup executor는 Source DB의 전역 참조를 읽고, checksum을 확인한 객체만 삭제하며, 결과 receipt를 기록한다.
- 요청·receipt는 `SOURCE_CLEANUP_JOURNAL_ROOT` 아래 생성 전용 JSON으로 보존한다. Artifact root와 다른 제한 접근 root를 사용한다. 별도 OS 계정을 쓰면 승인된 cleanup 공용 그룹/ACL로 writer의 request append와 executor의 read·receipt append만 열고 world 권한은 열지 않는다.
- 요청에는 request ID, durable run group key, nullable ingestion run ID, Artifact key·object key·checksum, 고정 failure reason, 요청 시각만 기록한다.
- 원문 bytes, credential, DB URL, signed URL, 실제 root는 요청·receipt·표준 출력에 기록하지 않는다.

## 환경변수

실제 값은 #593 제한 접근 안내에서 받는다. 공개 저장소에는 key 이름만 기록한다.

| 실행 주체 | 환경변수 |
| --- | --- |
| writer | 기존 `SOURCE_WRITER_*`, `SOURCE_ARTIFACT_LOCAL_ROOT`, 추가 `SOURCE_CLEANUP_JOURNAL_ROOT` |
| executor | `SOURCE_CLEANUP_EXECUTOR_HOST/PORT/NAME/USER/PASSWORD/ACTOR`, `SOURCE_ARTIFACT_LOCAL_ROOT`, `SOURCE_CLEANUP_JOURNAL_ROOT` |

두 root는 서로 달라야 한다. writer와 executor credential을 같은 프로세스에 함께 넣으면 명령이 거부된다. executor DB 역할은 `rag_source_ingestion_artifact`, `rag_source_ingestion_run`, `rag_source_snapshot_member` SELECT만 필요하며 관리자·소유·업무 테이블 쓰기 권한이 있으면 거부된다.

## 실패 요청

MFDS writer는 현재 시도에서 `put_verified`가 성공한 객체만 기억한다. DB transaction 진입·flush·commit 중 오류가 나면 rollback 후 다음을 수행한다.

1. rollback으로 DB Run ID가 사라질 수 있으므로 `run_group_key`를 주 식별자로 기록한다.
2. 실제 저장 확인된 객체를 object key로 중복 제거한다.
3. `MFDS_LABEL_TRANSACTION_FAILED` 고정 reason과 요청 시각을 private journal에 기록한다.
4. 요청 기록까지 실패하면 적재 성공으로 보고하지 않는다.

commit 뒤 재조회 실패는 이미 DB 참조가 존재할 수 있으므로 cleanup 요청으로 바꾸지 않고 기존 `MFDS_LABEL_POST_COMMIT_VERIFICATION_FAILED` 경계로 중단한다.

## executor 실행

```sh
uv run python -m ai_worker.admin.source_artifact_cleanup CLEANUP_REQUEST_UUID
```

각 객체마다 다음 순서를 지킨다.

1. writer 적재와 cleanup이 동시에 진행되지 않도록 두 명령이 공유하는 transaction advisory lock을 잡는다. 이 잠금은 Python에서 호출하며 DB 함수·trigger를 생성하지 않는다.
2. 전체 DB에서 같은 `LOCAL_PRIVATE object_key`의 Artifact receipt, 연결 Run, Snapshot member, Snapshot 연결과 checksum 충돌을 조회한다.
3. 참조가 하나라도 있으면 `BLOCKED`, 같은 key의 checksum이 다르면 `MANUAL_REVIEW`로 기록하고 삭제하지 않는다.
4. 파일이 없으면 `NOT_FOUND`로 기록한다.
5. 삭제 직전에 참조를 새로 조회하고 `INTENT` receipt를 먼저 durable 기록한다. 이 기록이 실패하면 삭제하지 않는다.
6. 파일 bytes SHA-256을 요청 checksum과 대조한 뒤 삭제한다.
7. 삭제 예외나 최종 receipt 실패는 성공으로 추정하지 않고 기존 `INTENT`를 수동 조정 단서로 사용한다.
8. 대상 key·checksum, 확인한 참조 범위, executor, 실행 시각, 결과·reason을 receipt에 남긴다.

`DELETED`와 `NOT_FOUND`만 명령 성공이다. `BLOCKED`, `MANUAL_REVIEW`, `UNKNOWN`은 수동 확인이 필요하며 자동 재시도하지 않는다.

## 실제 실행 전 #593 확인

- [ ] writer 계정으로 Artifact 삭제가 실제 거부된다.
- [ ] 별도 cleanup executor가 DB read와 대상 Artifact 삭제, receipt append만 할 수 있다.
- [ ] writer가 요청을 append하고 executor가 같은 요청을 읽을 수 있다.
- [ ] journal과 Artifact root의 실제 값은 제한 접근 위치에만 있다.
- [ ] 합성 실패 객체로 요청 → 전역 무참조 → 삭제 → receipt 재조회 검증을 완료했다.

현재 writer가 Artifact root 소유자로 직접 삭제할 수 있거나 executor credential·mount가 준비되지 않았다면 실제 노바스크 적재를 실행하지 않는다. 이 조건은 코드의 capability 분리만으로 충족됐다고 보지 않는다.
