# #347 Source Artifact 수동 정리 — Local 합성 실행·인계

상태: Local 합성 범위의 영속 실행 구현·리뷰 대상. 운영 Source/S3 삭제 승인 문서가 아니다.
정책: [#335 정책](../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)
검증: [#347 검증 기록](../testing/source-artifact-cleanup-347.md)

## 실행 범위

`tools/source_cleanup/run.py`는 명시된 Local 전용 `*_cleanup347_test` DB에서만 실행된다.
앱 기본 DB로 fallback하지 않는다. `create`가 **새로 만든 디렉터리와 합성 bytes**만 등록하며
기존 Source root·실제 MFDS·OCR·Guide/Chat·S3를 등록하거나 삭제하지 않는다.

파일은 실제 `LocalPrivateSourceArtifactStore.put_verified`로 보존한다. PostgreSQL receipt가
기록한 시각을 생성 이후의 보수적 유예 기산점으로 사용하고, mtime/ctime으로 날짜를 추정하지 않는다.
receipt commit 실패 시 남은 파일은 등록되지 않으므로 자동 후보가 아니다.
DB 이름·등록된 root와 root inode·객체 generation·checksum·크기를 모두 대조한다.

객체 key/물리 root는 접근 통제된 control DB에만 보관한다. 실행 출력과 audit는 안전한 참조와
고정 사유를 사용한다. 자동 삭제·Source Runtime 설정 변경·실제 데이터 활성화는 없다.

## 전용 DB 설치와 역할

1. 일회용 PostgreSQL과 이름이 `source_cleanup347_test`인 전용 DB를 준비한다.
2. 해당 DB에 현재 앱 migration을 적용한다. 사용자 서비스 DB를 지정하지 않는다.
3. `SOURCE_CLEANUP_TEST_DATABASE_URL`에 전용 URL을 지정하고 아래 명령으로 private control
   schema를 설치한다. 설치는 한 번만 수행하며 기존 schema를 삭제/재설치하지 않는다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py install
```

`tools/source_cleanup/synthetic_control.sql`은 **합성 도구 전용 schema 설치 파일**이다.
앱 public schema 변경이나 새 Alembic migration이 아니며 운영 DB에 적용하지 않는다.
현재 버전은 `review.revision`, 관리자 등록 reviewer/batch_target, 감사 결속 FK·CHECK를 사용한다. 리뷰·철회·감사 검증과 배치 잠금은 Python 명령이 명시적 transaction으로 수행한다.
이전 설치 DB의 승인·감사를 삭제하거나 schema를 덮어쓰지 않는다. 이전 DB는 증빙으로 보존하고,
새 일회용 `*_cleanup347_test` DB와 새 합성 workspace에 설치해 다시 검토한다.
이전 schema는 승인 revision·workspace 컬럼·새 감사 함수 중 필요한 요소가 없어 검사/INTENT 기록에서 실패하며 삭제를 진행하지 않는다.

관리자가 서로 다른 합성 DB 계정 3개를 준비한다. 아래 이름은 예시 역할이며 실제 팀원 계정을
만들거나 권한을 부여했다는 뜻이 아니다. 암호는 URL 환경변수로 전달하고 문서·로그에 쓰지 않는다.

| 역할 예시 | 허용 권한 | 금지 |
| --- | --- | --- |
| `cleanup347_pm` | Python 명령을 통한 배치 PM 검토 INSERT·철회 INSERT, control SELECT | actor 위조·기존 승인 UPDATE/DELETE |
| `cleanup347_security` | Python 명령을 통한 DB_SECURITY 검토 INSERT·철회 INSERT, control SELECT | PM 신원 대체·기존 승인 수정 |
| `cleanup347_executor` | control/Artifact SELECT, 검증된 감사 INSERT | 승인·소유 receipt INSERT, 감사 UPDATE/DELETE/TRUNCATE, Source 참조 변경 |

권한 설정 예시(계정은 사전 생성, 비superuser이며 control table 소유자가 아니어야 함):

```sql
GRANT USAGE ON SCHEMA public, source_cleanup
  TO cleanup347_pm, cleanup347_security, cleanup347_executor;
GRANT SELECT ON ALL TABLES IN SCHEMA source_cleanup
  TO cleanup347_pm, cleanup347_security, cleanup347_executor;
GRANT SELECT ON public.rag_source_ingestion_artifact TO cleanup347_executor;
GRANT INSERT (batch_hash,role,executor,policy_version,valid_from,expires_at)
  ON source_cleanup.review TO cleanup347_pm, cleanup347_security;
GRANT INSERT (batch_hash) ON source_cleanup.revocation TO cleanup347_pm, cleanup347_security;
GRANT USAGE ON SEQUENCE source_cleanup.review_revision
  TO cleanup347_pm, cleanup347_security;
INSERT INTO source_cleanup.reviewer(actor,role) VALUES
  ('cleanup347_security','DB_SECURITY'), ('cleanup347_pm','PM');
GRANT INSERT (batch_hash,object_ref,attempt_id,event,payload,intent_sequence,recorded_at)
  ON source_cleanup.audit TO cleanup347_executor;
GRANT USAGE ON SEQUENCE source_cleanup.audit_sequence_seq TO cleanup347_executor;
```

review의 `actor`와 audit의 `recorded_by`/executor는 인증된 DB 로그인으로 기록한다.
관리자는 reviewer 역할 목록만 등록하고, 합성 생성 계정이 정확한 batch_target을 등록한다.
실행자에게 audit 직접 INSERT·sequence USAGE·reviewer/batch_target 변경 권한을 주지 않는다.
역할마다 별도 로그인 연결을 사용한다. 검토자는 actor 컬럼에 직접 값을
넣을 수 없다. PM/DB_SECURITY 두 actor는 verifier의 지정 역할과 각각 일치해야 한다.
실행 계정은 검토자와 달라야 하며 superuser·BYPASSRLS·승인/receipt 생성 권한·감사 변경 권한이
있으면 실행을 거부한다. 정리와 지원되는 모든 게시 경로는 Python의 전역 advisory lock을 commit까지 공유한다.
리뷰·철회도 배치별 lock을 공유해 최종 검증과 삭제 사이에 변경되지 않는다. 감사 payload는 최신 승인·대상·참조에서 다시 만들며, 결과 행은 INTENT를 가리키는 FK와 payload 결속 CHECK를 통과해야 한다.
일반 실행 계정의 UPDATE/DELETE/TRUNCATE는 권한으로 차단한다. DB 소유자는 migration·장애 복구 경계이며 정상 실행 credential로 사용하지 않는다.

## 후보 → 검토 → 수동 실행

각 명령에서 `SOURCE_CLEANUP_TEST_DATABASE_URL`은 **해당 역할 계정의 전용 DB URL**을 사용한다.

1. 설치 관리 계정으로 신규 합성 디렉터리를 생성한다. 반환된 workspace ID를 기록한다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py create --root /absolute/new-synthetic-root --simulate-elapsed-days 31
```

2. 실행 계정으로 조사한다. 원문은 출력하지 않고 batch hash·객체별 안전한 참조·판정을 출력한다.
   생성 직후에는 30일 미경과로 HOLD가 정상이다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py survey --workspace WORKSPACE_ID
```

3. 31일 경과 합성 검증은 생성 시 `--simulate-elapsed-days 31`을 지정해 평가 offset을 불변 workspace에
   등록한다. 이후 survey/review/execute도 같은 옵션을 사용해야 하며 불일치는 거부한다.
   옵션 없이 만든 workspace는 offset 0이다. 승인자가 실행 중 임의로 시간을 바꾸지 못한다.
   receipt 생성 시각·감사 기록 시각은 실제 DB 시각이고, offset은 합성 유예/승인 유효기간 평가에만 사용한다.
4. **DB_SECURITY 검토를 먼저 기록한 뒤 PM이 최종 승인**한다. 두 계정 모두 조사에서 확인한
   동일 batch hash와 실행자를 사용한다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py review --workspace WORKSPACE_ID --batch-hash REVIEWED_HASH --review-role DB_SECURITY --executor cleanup347_executor --simulate-elapsed-days 31
PYTHONPATH=. uv run python tools/source_cleanup/run.py review --workspace WORKSPACE_ID --batch-hash REVIEWED_HASH --review-role PM --executor cleanup347_executor --simulate-elapsed-days 31
```

두 명령은 서로 다른 지정 계정으로 실행한다. 합성 CLI의 검토 유효기간은 1시간이다.
만료 또는 실행자 오기입을 고칠 때에는 동일 배치에 `review`를 다시 실행한다. 기존 행은 보존하고
DB가 잠금 안에서 발급한 새 revision을 추가한다. 역할별 최신 revision만 검사하며, 최신 행이
만료·신원 불일치·잘못된 실행자이면 과거 유효 승인으로 fallback하지 않는다. 두 역할 모두
현재 실행자를 승인하고 유효기간이 겹쳐야 실행할 수 있다. 최신 DB_SECURITY revision보다
PM revision이 커야 한다. DB_SECURITY가 재검토하면 기존 PM 승인은 사용할 수 없고 PM 재승인이 필요하다. audit의 `receipt_id`는 배치 hash와
선택된 PM·DB_SECURITY revision의 SHA-256으로 실제 사용한 검토 쌍을 식별한다.
철회는 해당 배치의 영구 차단으로 유지한다. 재승인은 만료·오기입을 고치는 기능이며 철회를
해제하지 않는다. 철회된 배치에 새 검토를 넣는 것도 거부한다.
실제 정책 승인자의 승인을 받았다는 증빙으로 합성 DB 계정의 기록을 사용하지 않는다.
대상이 바뀌면 hash가 달라져 이전 검토를 재사용하지 못한다. 철회는 이전 hash를 명시해 append한다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py revoke --workspace WORKSPACE_ID --batch-hash REVIEWED_HASH
```

5. 실행 계정으로 별도 수동 명령을 실행한다. 실행 직전에도 승인·철회·만료·객체·참조를 재검사한다.

```bash
PYTHONPATH=. uv run python tools/source_cleanup/run.py execute --workspace WORKSPACE_ID --batch-hash REVIEWED_HASH --executor cleanup347_executor --pm-role cleanup347_pm --db-security-role cleanup347_security --simulate-elapsed-days 31
PYTHONPATH=. uv run python tools/source_cleanup/run.py audit --workspace WORKSPACE_ID
```

승인 역할 이름은 검토된 환경 설정을 사용하며 실행자가 임의 계정으로 바꿔 통과시키지 않는다.
CLI가 기본으로 수행하는 작업은 없고 실행 command를 명시해야 한다. 실패 시 exit code 2와 안전한
고정 오류만 출력하며 DB URL·SQL parameter·원문 예외를 출력하지 않는다.

## 승인 변경과 삭제 경합

검토·철회 Python 명령은 배치별 exclusive transaction advisory lock을 취한다.
실행 guard는 같은 배치의 shared lock을 취하고, 잠금 안의 승인 재조회부터 객체 검증·unlink 및
transaction 종료까지 유지한다. 지원되는 리뷰·철회 경로는 반드시 이 Python 명령을 사용한다.

- 철회가 먼저 commit되면 잠금 안 재조회에서 거부하여 삭제하지 않는다.
- 철회/재검토 transaction이 먼저 진행 중이면 실행이 잠금을 얻지 못해 삭제하지 않는다.
- 실행이 먼저 잠금을 얻으면 검토/철회 INSERT는 즉시 실패한다. 해당 요청은 기록·commit되지
  않았으므로 철회 성공으로 안내하지 않는다. 실행 종료 후 필요하면 명시적으로 다시 요청한다.
- 이미 잠금 안에서 시작된 삭제를 뒤늦은 철회 요청이 취소한다고 보장하지 않는다.
  CLI 실패는 exit code 2이며 자동 재시도하지 않는다. 다른 배치의 검토를 같은 승인 잠금으로 묶지 않는다.

## 수집·재사용·참조 commit 경합

관리되는 합성 writer는 `reference_existing_objects(engine, batch)` 안에서 객체 bytes/세대를
검증하고 같은 connection에 Source 참조를 기록한다. shared advisory lock과 root flock은
참조 commit/rollback까지 유지한다. 삭제 후 새 참조를 만들려 하면 객체 부재로 거부한다.
일반 `publication_transaction`은 신규 합성 publication의 내부 경계다. 기존 receipt의 재사용은
반드시 `reference_existing_objects`를 사용한다.

Cleanup은 exclusive advisory lock과 root flock을 유지한다.
지원되는 게시 transaction이 진행 중이면 즉시 보류하고, 보호 중 새 게시 transaction 진입을 차단한다.
Source별 잠금에만 의존하지 않는다. DB 연결 상실 뒤에도 로컬 잠금은 context 종료까지 유지한다.
이 잠금은 **관리되는 합성 경로**의 보장이다. 현재 운영 Source writer에 등록했다고 주장하지 않는다.

동일 backend/key 전체 Artifact 행을 조회하여 FAILED/NO_CHANGE 및 그 행을 통해 이어지는
Snapshot/Catalog/검증 provenance를 보호한다. 새 public 테이블/컬럼은 실행자의 조회 권한 유무와
무관하게 catalog fingerprint를 바꾸므로 보류한다. 합성 workspace는 외부 증빙을 생성하지 않는다.
기존 root나 운영 Citation/평가의 외부 참조를 자동으로 무참조라고 판단하는 기능은 없다.
`_inspect_downstream_scope`가 전용 합성 DB·등록 배치·root 잠금·생성 receipt·실제 합성 bytes를
확인한 경우에만 닫힌 참조 범위를 반환한다. 비합성 또는 귀속이 입증되지 않은 입력에는
`NotImplementedError`를 발생시키며 조사·실행은 실패로 종료된다. 운영 adapter에서 이 메서드를
실제 downstream 조회로 구현하기 전에는 `downstream_count=0`을 재사용하지 않는다.

## 결과·재시도·UNKNOWN 인계

| 결과 | 조치 |
| --- | --- |
| COMPLETED | 해당 합성 배치의 성공/기존 성공을 확인. 자동으로 정책/Runtime 승인으로 해석하지 않음 |
| REVIEW_REQUIRED | 객체별 BLOCKED/UNKNOWN 사유를 확인. 전체 성공으로 처리하지 않음 |
| EXECUTION_OR_AUDIT_UNAVAILABLE | 일부 파일은 이미 삭제됐을 수 있음. DB INTENT를 기준으로 조사 |
| MISSING_REQUIRES_RECONCILIATION | 파일 부재만으로 성공 판정 금지. 기존 이력 수정 없이 UNKNOWN 유지 |
| RECREATED_AFTER_SUCCESS | 같은 key의 새 객체를 과거 승인으로 삭제하지 않음 |
| RETRY_REQUIRES_REVIEW | 생존 실패 대상의 승인·참조·세대를 확인한 뒤 제한 수동 재시도 |

재시도는 기존 execute 명령에 `--retry --max-attempts 2`를 명시한다. 합성 도구의 최대 한도는
3회이며 자동 반복/스케줄러는 없다. 성공 대상은 건너뛰고 실패 대상만 다시 검사한다.
검토가 철회·만료되면 재시도도 막힌다. 결과가 불명확하면 임의 성공 처리 기능 없이 조사 대상으로
남긴다. 이 한도는 운영 재시도 정책을 확정한 값이 아니다.

INTENT와 결과는 각각 **독립 DB transaction으로 commit**한다. 파일 unlink가 성공한 뒤 결과
기록이 실패해도 INTENT는 남고, 재시작 시 새 adapter가 DB를 읽어 UNKNOWN으로 복구한다.
파일을 지우기 전에 Source 참조/receipt를 제거하지 않으며 rollback으로 파일을 복원한다고 가정하지 않는다.

## 보관·담당 인계

| 항목 | 이번 PR 인계 기준 |
| --- | --- |
| 구현·합성 재현 | 김지혜: 코드·fixture·CI·검증 기록과 이 runbook 제공 |
| DB 권한·불변성 | 송은영 PR 리뷰: control schema/역할/잠금·DB 감사 검증 |
| Source provenance | 정현우 PR 리뷰: 직접 참조 전체 보호·생성 receipt·범위 불명 보류 |
| 정책 | 권가빈 PR 리뷰: 30일·수동 승인·실패/UNKNOWN 처리·비활성 범위 |
| 실제 운영 실행자 | 실제 삭제 작업 전에 Backend/운영 담당 중 지정. 합성 실행은 운영 권한을 부여하지 않음 |
| 감사 보관 위치 | 합성 DB의 `source_cleanup.audit`, 승인/철회/객체 receipt와 검증 보고서. 실행자는 수정·삭제 권한 없음 |
| 감사 관리·보존 종료 | 실환경은 PM·DB 담당자가 저장 위치/관리자를 지정하고, 관련 provenance와 증빙이 더 이상 필요 없음을 검토한 뒤 별도 종료 승인. 서비스 종료만으로 제거하지 않음 |
| UNKNOWN 장기 잔존 | 실행 결과와 batch/attempt 참조를 DB·Source 검토자에게 인계. 자동 삭제/성공 변환 없음 |

테스트 DB를 폐기하는 것은 일회용 합성 환경 정리다. 운영 감사 기록의 삭제 허용 정책이 아니다.
이번 PR의 구현·리뷰·CI가 완료되면 #347의 합성 수동 정리 완료 범위를 판정할 수 있다.
운영 S3/실제 Source 데이터 삭제·자동화·Runtime 활성화는 #347 제외 범위를 유지한다.
#335·#165·#323에는 사용자가 게시할 결과/증빙 연결 초안을 제공하며 도구가 댓글을 게시하지 않는다.


## DB가 생성하는 감사 근거와 실행자 보고

`PostgresAuditJournal.append`만 지원되는 감사 쓰기 경로다. 호출자가 전달한 승인자·receipt·checksum·시각은 신뢰하지 않고 Python adapter가 아래 DB 근거를 다시 확인한다.

- 관리자 등록 reviewer와 실제 최신 DB_SECURITY → PM revision 순서·실행자·유효기간·철회 여부
- 생성 계정이 등록한 배치/대상과 불변 object receipt의 결속
- 실제 DB 시각과 생성 시 등록된 합성 offset으로 30일 유예 검사
- 참조 테이블 잠금과 같은 backend/key 전체 직접 참조 0건

Python adapter가 checksum·종류·정책·승인 revision 해시·actor·실행자·실제 DB 기록 시각을 구성한다.
`references_verified`는 **INTENT 시점의 DB 직접 참조 검사 이력**이다. 현재 전체 downstream이나
파일 상태의 증명으로 재사용하지 않는다. 전체 scope/bytes 검증은 기존 Local 실행 guard가 수행한다.
후속 결과는 같은 로그인·batch/object/attempt의 기존 INTENT 근거를 보존하고 새 이벤트·시각만 추가한다. DB의 복합 FK와 CHECK는 INTENT 연결, payload 식별자, event/reason 조합을 추가로 강제한다.

`event/reason`은 정해진 조합의 실행자 보고다. DB가 파일 시스템 unlink를 독립적으로 관측하는 것은
아니므로 DELETED 행 하나만으로 물리 삭제를 별도 증명했다고 주장하지 않는다. 재시작 경로는
실제 객체 상태도 다시 검사한다. 원문 없는 고정 reason만 허용하며 임의 설명·시각을 넣지 못한다.
