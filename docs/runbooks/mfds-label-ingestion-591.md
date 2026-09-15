# #591 MFDS 제품별 허가사항 적재 실행 경로

상태: 구현 완료 / 실제 dev·staging 적재 전

이 실행 경로는 승인된 private 위치에 미리 확보한 MFDS EE·UD·NB와 선택적 NN XML을 검증하고, 기존 Source Snapshot lifecycle에 원문 Artifact와 member를 한 DB transaction으로 연결한다. 외부 수집 계약과 Source/Endpoint/Operation 값은 #593에서 확정된 값을 사용하며 코드가 임의 기본값을 만들지 않는다.

## 입력

- 제품별 전용 디렉터리의 `EE.xml`, `UD.xml`, `NB.xml`
- 일반의약품 e약은요를 포함할 때 같은 디렉터리의 `NN.xml`
- 같은 디렉터리의 `acquisition-manifest.json`
- 9자리 MFDS ITEM_SEQ
- 실제 원문 수집 시각을 나타내는 timezone 포함 RFC3339 값
- 승인된 Endpoint Receipt SHA-256

취득 증빙 manifest는 `mfds-label-acquisition-evidence@1` 형식으로 ITEM_SEQ, 수집 시각, Endpoint Receipt hash와 각 EE·UD·NB·NN의 문서 종류·파일명·공식 URL·원본 SHA-256·크기·관측 content type을 기록한다. 실행 인자 및 환경값과 하나라도 다르거나 실제 파일의 크기·hash가 다르면 적재를 거부한다. 공식 URL은 `https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{ITEM_SEQ}/{문서종류}`만 허용한다.

원문 디렉터리와 파일은 symlink를 허용하지 않는다. manifest는 64 KiB 이하, XML은 파일당 2 MiB 이하 UTF-8이어야 하며 DTD·ENTITY, 문서 type/title 불일치, 필수 본문 누락을 거부한다. NN은 7개 공식 항목의 정확한 순서·중복·누락을 검사한다. 공식 원문에서 관측된 빈 항목은 임의로 채우지 않고 `PARTIAL_OFFICIAL`로 유지한다.

## 제한 접근 실행 환경

실제 값은 #593에서 지정한 비밀 저장소로 전달한다. 공개 문서에는 값이나 실제 root를 기록하지 않는다.

| 환경변수 | 의미 |
| --- | --- |
| `SOURCE_WRITER_HOST/PORT/NAME/USER/PASSWORD/ACTOR` | 전용 Source Writer 접속·감사 주체 |
| `SOURCE_ARTIFACT_STORAGE_BACKEND` | 현재 실행 기준은 `LOCAL_PRIVATE` |
| `SOURCE_ARTIFACT_READER_ROOT` | writer가 commit 후 검증할 최종 Artifact read-only mount |
| `SOURCE_ARTIFACT_FINALIZER_COMMAND` | 별도 Artifact owner로 실행되는 수정 불가능한 고정 preserve 명령 |
| `SOURCE_CLEANUP_JOURNAL_ROOT` | #613 cleanup 요청·receipt용 별도 private root |
| `MFDS_LABEL_SOURCE_CODE` | #593에서 확정한 Source code |
| `MFDS_LABEL_ENDPOINT_CODE` | #593에서 확정한 Endpoint code |
| `MFDS_LABEL_OPERATION_CODE` | #593에서 확정한 Operation code |
| `MFDS_LABEL_ENDPOINT_RECEIPT_HASH` | 승인된 제한 수집 Receipt SHA-256 |

Runtime·migration·admin DB credential이 같은 프로세스에 함께 들어오면 실행을 거부한다. Source Writer가 DB 관리자·소유자·role membership을 가진 경우에도 거부한다.

## 실행

전문의약품처럼 NN이 없는 제품:

```sh
uv run python -m ai_worker.admin.mfds_label_writer 200610660 \
  --input-dir /approved/private/novasc \
  --collected-at 2026-09-15T06:00:00Z
```

NN을 포함하는 일반의약품은 `--include-e-drug`를 추가한다. 예시 경로와 시각은 실제 값이 아니다.

## 애플리케이션 무결성 경계

1. 취득 증빙 manifest로 제품·수집 실행·공식 URL·원본 파일을 결속한 뒤 제품별 필수 XML 집합을 모두 파싱한다.
2. 원본 바이트 SHA-256과 순서가 보존된 XML 구조의 canonical checksum을 계산한다.
3. 별도 Artifact owner의 고정 finalizer에 원문을 stdin으로 전달해 content-addressed `LOCAL_PRIVATE` 저장소에 보존한다. writer는 최종 root를 직접 쓰거나 삭제하지 않는다.
4. 기존 Python Snapshot lifecycle에서 Operation 잠금, 정책, 동일 version 충돌과 `NO_CHANGE`를 판정한다.
5. 생성된 Run의 Artifact ID를 다시 읽고 raw checksum과 metadata를 대조한 뒤 ARTIFACT member를 추가한다.
6. member까지 성공해야 호출자 transaction이 commit된다. 원본 저장 후 commit 전 오류는 DB transaction을 rollback하고 [#613 cleanup](./source-artifact-cleanup-613.md) 요청을 private journal에 남긴다. writer는 삭제하지 않으며 지정된 executor가 전역 DB 참조와 checksum을 다시 확인한 뒤 정리하고 Receipt를 남긴다.
7. commit 후 새 DB session에서 Snapshot→member→Artifact를 재조회하고 writer의 read-only mount에서 저장 원문 크기·SHA-256을 다시 검증한다.
8. 동일 canonical 내용 재실행은 기존 Snapshot과 member가 완전할 때만 `NO_CHANGE`로 성공한다. 새 Run의 원문은 별도 Artifact로 기록하고, 기존 Snapshot member는 생성 당시 원문 checksum을 유지한다. member 누락을 성공으로 숨기지 않는다.

비즈니스 규칙과 데이터 무결성 판정은 Python Service/Repository에서 수행한다. 신규 RLS·DB Trigger·Stored Procedure·업무 DB 함수는 사용하지 않는다.

## 완료 경계

명령 성공 출력에는 decision, Snapshot ID, canonical checksum과 member count만 포함한다. DB URL, credential, 실제 Artifact root와 원문은 출력하지 않는다.

이 구현과 합성 테스트 통과만으로 노바스크 적재가 완료된 것은 아니다. #593에서 finalizer와 역할별 mount, writer의 최종 Artifact 쓰기·삭제 거부, 별도 cleanup executor의 DB read·Artifact delete·receipt append 권한을 확인한 뒤 실제 원문으로 명령을 실행한다. 새 session 재조회 결과와 동일 입력 재실행 결과를 확인하고 현우님에게 Snapshot/member/locator를 전달해야 첫 제품 Source 범위가 완료된다. Retrieval·Citation·Runtime 사용 승인은 별도다.
