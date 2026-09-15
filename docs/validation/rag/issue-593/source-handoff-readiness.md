# #593 Source Snapshot 인계 환경 readiness 기록

상태: Draft / #591 실제 적재 전 준비 상태를 확인하기 위한 저장소 공개 기록. 실제 DB endpoint, credential, artifact root/bucket, 원문 artifact를 포함하지 않는다. PR #597로 develop에 반영된 `docs/runbooks/source-snapshot-handoff-env-593.md`를 상위 기준으로 삼는다.

관련 Issue: #593, #591, #166, #526
담당: 송은영 (`@phina-io`)
검토: 김지혜(Source 수집·저장 실행)

## 목적

#591 노바스크정 5mg 허가사항 Source Snapshot 적재를 시작하기 전에 저장소에서 공개 가능한 readiness 기준을 고정한다. 이 문서는 환경 선택 원칙을 다시 만드는 문서가 아니라, 실제 실행 전 확인해야 할 설정 키, 기존 검증 근거, 비공개로 전달해야 할 값, 인계 증거 목록을 한곳에 모으는 실행 준비 기록이다.

## 현재 저장소에서 확인 가능한 준비 상태

| 구분 | 현재 근거 | 판정 |
| --- | --- | --- |
| Artifact backend 설정 키 | `ai_worker/core/config.py`, `ai_worker/README.md` | 존재 |
| Artifact store factory | `ai_worker/adapters/source_artifact_store_factory.py` | `DISABLED` fail-closed, `LOCAL_PRIVATE`/`S3_PRIVATE` 명시 조립 |
| Local private 저장소 | `ai_worker/adapters/local_private_source_artifact_store.py` | 절대경로, symlink 거부, 0700/0600, SHA-256 내용 주소 기준 |
| S3 private 저장소 | `ai_worker/adapters/s3_private_source_artifact_store.py` | HTTPS endpoint, credential 없는 URL, SSE `AES256`/`aws:kms`, checksum 검증 |
| Config 검증 | `ai_worker/tests/core/test_config.py` | mixed local/S3 설정과 불완전 S3 설정 거부 |
| Factory 검증 | `ai_worker/tests/rag/source_ingestion/test_source_artifact_store_factory.py` | disabled/local/s3 조립 경계 검증 |
| Source management role 검증 | `infra/python/source_management_role_policy.py`, `tests/contract/test_source_management_deployment.py` | 관리 credential과 일반 runtime credential 분리 근거 존재 |
| #591 후보 검사 | `scripts/rag/verify_mfds_label_candidate.py`, `tests/contract/test_mfds_label_candidate.py` | 실제 적재가 아닌 로컬 XML 후보 구조·hash 검사 도구 존재 |
| #591 실제 적재 실행 경로 | #609 | 최신 develop에는 MFDS Source parser·normalization·Snapshot 저장 command가 없음. #609 완료 전에는 provisioning만으로 적재 실행 불가 |
| #591 로컬 검증 기록 | `docs/validation/rag/issue-591/local-storage-validation.md` | 합성 저장·재조회 검증이며 실제 인계 완료는 아님 |

## #591 실행 전 공개 문서에 기록할 수 있는 값

아래 값은 공개 저장소에 기록할 수 있다. 실제 secret이나 원문 경로를 대신하지 않는다.

| 항목 | 기록 기준 |
| --- | --- |
| `SOURCE_ARTIFACT_STORAGE_BACKEND` | 팀 공용 `LOCAL_PRIVATE` root 우선. `S3_PRIVATE`는 후속 전환 후보 |
| `SOURCE_ARTIFACT_S3_PREFIX` | 비밀값이 아닌 논리 prefix만 기록 가능 |
| `SOURCE_ARTIFACT_S3_REGION` | region 자체가 secret이 아니면 기록 가능 |
| `SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION` | `AES256` 또는 `aws:kms` 여부 기록 가능 |
| `SOURCE_ARTIFACT_S3_ENDPOINT_URL` | credential 없는 HTTPS endpoint 형식만 기록 가능. 실제 endpoint 공개 여부는 환경 정책에 따름 |
| Source/Endpoint/Operation code | 김지혜와 합의된 논리 식별자 기록 가능 |
| source_version/parser/canonicalization version | 원문 내용이나 secret 없이 계약 식별자로 기록 가능 |
| Snapshot 인계 증거 | Snapshot ID, source_version, canonical checksum, member count, artifact key의 안전한 식별자 기록 가능 |

## 제한 접근 위치에만 기록할 값

다음 값은 GitHub Issue, PR, Discord, 일반 로그에 기록하지 않는다.

- DB endpoint와 전체 connection URL
- DB username/password, temporary credential, access token
- LOCAL_PRIVATE root 실경로, S3 bucket/root 실명 또는 조직 정책상 비공개인 object prefix
- AWS access key, secret key, session token, role assumption credential
- 원문 artifact bytes 또는 원문 파일 경로
- Provider credential, signed URL, query credential

공개 문서에는 값의 존재 여부와 검증 결과만 남긴다. 실제 값은 승인된 제한 접근 위치에서 전달하고, PR에는 그 위치나 secret을 복제하지 않는다.

## 역할별 readiness 체크

| 역할 | 준비 확인 | 공개 기록 |
| --- | --- | --- |
| 수집 writer | 지정 dev/staging DB에 Source Snapshot/Run/Artifact/member 저장 경로가 있는지 확인 | credential 이름 없이 writer 역할 준비 여부만 기록 |
| 검증 reader | 새 session에서 Snapshot→member→artifact receipt 재조회가 가능한지 확인 | 조회 성공, count, checksum 대조 결과 기록 |
| cleanup executor | 미참조 Artifact 정리 정책과 executor 경계가 #347/#398 기존 경계를 참고하면서 #613 구현 범위와 충돌하지 않는지 확인 | 실제 삭제 권한 부여 여부가 아니라 경계 확인 결과 기록 |
| consumer reader | 후속 AI/RAG consumer가 Snapshot ID와 member ID로 후속 Chunk/Index 입력을 식별할 수 있는지 확인 | 원문 bytes 없이 참조 충분성 확인 결과 기록 |


## provision 담당과 실제 환경 준비 기록 양식

가빈님이 #591 적재용 dev/staging DB와 팀 공용 `LOCAL_PRIVATE` root를 provision한다. 송은영은 공개 가능한 기준과 검증 절차를 문서화하고, 준비된 환경이 운영·배포 DB와 분리됐는지와 역할별 권한이 분리됐는지 확인한다. 실제 endpoint, credential, root는 접근 제한된 비밀 저장소에만 둔다.

## 실제 환경 준비 기록 양식

아래 표는 공개 저장소에 남길 수 있는 준비 결과만 기록한다. 실제 endpoint, credential, bucket/root, 원문 경로는 제한 접근 위치에만 둔다.

| 항목 | 공개 기록값 | 제한 접근 위치에 둘 값 | 확인 기준 |
| --- | --- | --- | --- |
| dev/staging DB | 환경 별칭, 역할 준비 여부 | endpoint, full connection URL, username/password 또는 임시 credential | writer 저장과 reader 재조회가 분리 계정으로 가능 |
| writer 계정 | 역할명 또는 logical actor | 실제 DB login, password, token | Source Snapshot/Run/Artifact/member 쓰기만 가능 |
| reader 계정 | 역할명 또는 logical actor | 실제 DB login, password, token | commit 후 새 session에서 Snapshot→member→artifact receipt 조회 가능 |
| cleanup executor | 역할 준비 여부 | 실제 DB login, cleanup 실행 credential | 승인된 cleanup 경계 외 삭제 불가 |
| artifact backend | 팀 공용 `LOCAL_PRIVATE` root 우선, `S3_PRIVATE` 후속 전환 후보 | LOCAL_PRIVATE root 실경로, bucket/root 실명, private object prefix, 실행 credential | object key와 checksum으로 재조회 가능 |
| source identity | source/endpoint/operation code | 없음 | #591 대상 범위와 후속 consumer가 같은 식별자를 사용 |
| version identity | source_version, parser/canonicalization version | 없음 | checksum 재현 기준으로 재실행 가능 |

이 표가 채워져도 실제 secret 값이 공개 저장소에 있으면 안 된다. 공개 기록은 준비 여부와 검증 결과를 설명하고, 실제 값은 승인된 제한 접근 위치의 별도 기록으로만 전달한다.

## 실제 #591 적재 전 체크리스트

- [x] PR #597의 runbook이 develop에 반영됐다.
- [ ] dev/staging DB가 운영·배포 DB와 분리되어 지정됐고, 실제 endpoint·credential은 접근 제한된 비밀 저장소에 준비됐다.
- [ ] `SOURCE_ARTIFACT_STORAGE_BACKEND`가 팀 공용 `LOCAL_PRIVATE` root로 정해졌다.
- [ ] 팀 공용 `LOCAL_PRIVATE` root가 개인 PC 경로가 아니라 팀원이 재조회 가능한 전용 root임을 확인했다.
- [ ] `S3_PRIVATE`로 전환하는 경우 bucket/prefix/encryption/credential 주입 방식이 접근 제한된 비밀 저장소에서 확인됐다.
- [ ] writer/reader/cleanup/consumer 역할 분리 기준이 실제 실행 계정과 맞는지 확인했다.
- [ ] #591 대상 Source parser·정규화·적재 실행 경로와 실행 command가 #609에서 준비됐다.
- [ ] Source/Endpoint/Operation code와 source_version/parser/canonicalization version이 #591 기준으로 정해졌다.
- [ ] 원문 artifact 전달 경로가 GitHub/Discord/일반 로그를 거치지 않도록 정했다.
- [ ] 운영 DB 반영과 Runtime/Guide/Chat 공개는 이번 실행에서 제외된다는 점을 확인했다.

## 실제 #591 적재 후 기록할 증거

| 증거 | 기록 기준 |
| --- | --- |
| Snapshot | `source_snapshot_id`, status, source_version, canonical checksum |
| Member | EE/UD/NB/NN 합의한 member count, locator, member ID |
| Artifact | storage backend, object key의 안전한 식별자, size, raw SHA-256, content type |
| Verification | checksum 대조 결과와 verification status |
| Re-query | commit 후 새 session에서 Snapshot→member→artifact receipt 재조회 결과 |
| Re-run | 동일 입력·동일 source_version·동일 checksum·동일 member set 재실행 시 NO_CHANGE 또는 기존 Snapshot 재사용 등 합의된 멱등 결과. checksum/source_version/member mismatch는 fail-closed 또는 수동 검토 |
| Handoff | 후속 Chunk/Index 입력으로 충분한 참조 목록 |

위 증거가 없으면 #591은 로컬 검증 또는 부분 조사 결과로만 기록하고, 인계용 Source Snapshot 적재 완료로 보지 않는다. 적재 transaction 실패는 DB rollback으로 처리하고, Artifact 저장 후 DB commit 전에 실패한 미참조 Artifact는 writer가 삭제하지 않는다. writer는 run ID·artifact key·checksum·failure reason·requested_at만 cleanup 요청으로 남기고, 지정된 cleanup executor가 Snapshot/member/run/artifact receipt 참조를 확인한다. 참조가 하나라도 있으면 삭제하지 않고 `BLOCKED` 또는 `MANUAL_REVIEW`로 남기며, 삭제 또는 quarantine 결과는 대상 key, checksum, 확인한 참조 범위, 실행자, 실행 시각, 결과 receipt로 남긴다. 실제 요청 기록·executor 검증 절차는 #613에서 추적한다.

## 이번 PR에서 하지 않는 것

- 실제 DB endpoint, credential, artifact root/bucket 값을 추가하지 않는다.
- 실제 노바스크 원문을 저장소에 추가하지 않는다.
- #591 Source parser·정규화·적재 실행 command를 구현하지 않는다. 해당 작업은 #609에서 처리한다.
- #591 적재 실패 artifact cleanup 요청·executor 절차를 구현하지 않는다. 해당 작업은 #613에서 처리한다.
- Source/Catalog schema, migration, 공개 API, Runtime currentness를 변경하지 않는다.
- 신규 RLS, DB Trigger, 업무 DB 함수를 추가하지 않는다. 무결성·중복·상태 판정은 애플리케이션 계층과 DB 제약으로 처리한다.
- #591 실제 적재 성공이나 운영 공개 승인을 주장하지 않는다.
