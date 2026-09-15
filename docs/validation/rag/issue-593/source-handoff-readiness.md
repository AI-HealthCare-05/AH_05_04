# #593 Source Snapshot 인계 환경 readiness 기록

상태: Draft / #591 실제 적재 전 준비 상태를 확인하기 위한 저장소 공개 기록. 실제 DB endpoint, credential, artifact root/bucket, 원문 artifact를 포함하지 않는다. PR #597로 develop에 반영된 `docs/runbooks/source-snapshot-handoff-env-593.md`를 상위 기준으로 삼는다.

관련 Issue: #593, #591, #166, #526
담당: 송은영 (`@phina-io`)
검토: 김지혜(Source 수집·저장 실행), 정현우(RAG/LLM 소비 인계)

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
| #591 로컬 검증 기록 | `docs/validation/rag/issue-591/local-storage-validation.md` | 합성 저장·재조회 검증이며 실제 인계 완료는 아님 |

## #591 실행 전 공개 문서에 기록할 수 있는 값

아래 값은 공개 저장소에 기록할 수 있다. 실제 secret이나 원문 경로를 대신하지 않는다.

| 항목 | 기록 기준 |
| --- | --- |
| `SOURCE_ARTIFACT_STORAGE_BACKEND` | `S3_PRIVATE` 우선. 준비 전에는 팀 재조회 가능한 공유 `LOCAL_PRIVATE` 전용 root만 fallback |
| `SOURCE_ARTIFACT_S3_PREFIX` | 비밀값이 아닌 논리 prefix만 기록 가능 |
| `SOURCE_ARTIFACT_S3_REGION` | region 자체가 secret이 아니면 기록 가능 |
| `SOURCE_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION` | `AES256` 또는 `aws:kms` 여부 기록 가능 |
| `SOURCE_ARTIFACT_S3_ENDPOINT_URL` | credential 없는 HTTPS endpoint 형식만 기록 가능. 실제 endpoint 공개 여부는 환경 정책에 따름 |
| Source/Endpoint/Operation code | 김지혜·정현우와 합의된 논리 식별자 기록 가능 |
| source_version/parser/canonicalization version | 원문 내용이나 secret 없이 계약 식별자로 기록 가능 |
| Snapshot 인계 증거 | Snapshot ID, source_version, canonical checksum, member count, artifact key의 안전한 식별자 기록 가능 |

## 제한 접근 위치에만 기록할 값

다음 값은 GitHub Issue, PR, Discord, 일반 로그에 기록하지 않는다.

- DB endpoint와 전체 connection URL
- DB username/password, temporary credential, access token
- S3 bucket/root 실명 또는 조직 정책상 비공개인 object prefix
- AWS access key, secret key, session token, role assumption credential
- 원문 artifact bytes 또는 원문 파일 경로
- Provider credential, signed URL, query credential

공개 문서에는 값의 존재 여부와 검증 결과만 남긴다. 실제 값은 승인된 제한 접근 위치에서 전달하고, PR에는 그 위치나 secret을 복제하지 않는다.

## 역할별 readiness 체크

| 역할 | 준비 확인 | 공개 기록 |
| --- | --- | --- |
| 수집 writer | 지정 dev/staging DB에 Source Snapshot/Run/Artifact/member 저장 경로가 있는지 확인 | credential 이름 없이 writer 역할 준비 여부만 기록 |
| 검증 reader | 새 session에서 Snapshot→member→artifact receipt 재조회가 가능한지 확인 | 조회 성공, count, checksum 대조 결과 기록 |
| cleanup executor | 미참조 Artifact 정리 정책과 executor 경계가 #347/#398과 충돌하지 않는지 확인 | 실제 삭제 권한 부여 여부가 아니라 경계 확인 결과 기록 |
| consumer reader | 정현우가 Snapshot ID와 member ID로 후속 Chunk/Index 입력을 식별할 수 있는지 확인 | 원문 bytes 없이 참조 충분성 확인 결과 기록 |


## 실제 환경 준비 기록 양식

아래 표는 공개 저장소에 남길 수 있는 준비 결과만 기록한다. 실제 endpoint, credential, bucket/root, 원문 경로는 제한 접근 위치에만 둔다.

| 항목 | 공개 기록값 | 제한 접근 위치에 둘 값 | 확인 기준 |
| --- | --- | --- | --- |
| dev/staging DB | 환경 별칭, 역할 준비 여부 | endpoint, full connection URL, username/password 또는 임시 credential | writer 저장과 reader 재조회가 분리 계정으로 가능 |
| writer 계정 | 역할명 또는 logical actor | 실제 DB login, password, token | Source Snapshot/Run/Artifact/member 쓰기만 가능 |
| reader 계정 | 역할명 또는 logical actor | 실제 DB login, password, token | commit 후 새 session에서 Snapshot→member→artifact receipt 조회 가능 |
| cleanup executor | 역할 준비 여부 | 실제 DB login, cleanup 실행 credential | 승인된 cleanup 경계 외 삭제 불가 |
| artifact backend | `S3_PRIVATE` 또는 공유 `LOCAL_PRIVATE` | bucket/root 실명, private object prefix, 실행 credential | object key와 checksum으로 재조회 가능 |
| source identity | source/endpoint/operation code | 없음 | #591 대상 범위와 후속 consumer가 같은 식별자를 사용 |
| version identity | source_version, parser/canonicalization version | 없음 | checksum 재현 기준으로 재실행 가능 |

이 표가 채워져도 실제 secret 값이 공개 저장소에 있으면 안 된다. 공개 기록은 준비 여부와 검증 결과를 설명하고, 실제 값은 승인된 제한 접근 위치의 별도 기록으로만 전달한다.

## 실제 #591 적재 전 체크리스트

- [x] PR #597의 runbook이 develop에 반영됐다.
- [ ] dev/staging DB가 지정됐고, 실제 endpoint·credential은 제한 접근 위치에 준비됐다.
- [ ] `SOURCE_ARTIFACT_STORAGE_BACKEND`가 `S3_PRIVATE` 또는 팀 공유 `LOCAL_PRIVATE`로 정해졌다.
- [ ] `S3_PRIVATE` 사용 시 bucket/prefix/encryption/credential 주입 방식이 제한 접근 위치에서 확인됐다.
- [ ] `LOCAL_PRIVATE` fallback 사용 시 개인 PC 경로가 아니라 팀원이 재조회 가능한 전용 root임을 확인했다.
- [ ] writer/reader/cleanup/consumer 역할 분리 기준이 실제 실행 계정과 맞는지 확인했다.
- [ ] Source/Endpoint/Operation code와 source_version/parser/canonicalization version이 #591 기준으로 정해졌다.
- [ ] 원문 artifact 전달 경로가 GitHub/Discord/일반 로그를 거치지 않도록 정했다.
- [ ] 운영 DB 반영과 Runtime/Guide/Chat 공개는 이번 실행에서 제외된다는 점을 확인했다.

## 실제 #591 적재 후 기록할 증거

| 증거 | 기록 기준 |
| --- | --- |
| Snapshot | `source_snapshot_id`, status, source_version, canonical checksum |
| Member | EE/UD/NB 등 합의한 member count, locator, member ID |
| Artifact | storage backend, object key의 안전한 식별자, size, raw SHA-256, content type |
| Verification | checksum 대조 결과와 verification status |
| Re-query | commit 후 새 session에서 Snapshot→member→artifact receipt 재조회 결과 |
| Re-run | 동일 입력 재실행 시 NO_CHANGE 또는 합의된 멱등 결과 |
| Handoff | 정현우가 Chunk/Index 입력으로 충분하다고 확인한 참조 목록 |

위 증거가 없으면 #591은 로컬 검증 또는 부분 조사 결과로만 기록하고, 인계용 Source Snapshot 적재 완료로 보지 않는다.

## 이번 PR에서 하지 않는 것

- 실제 DB endpoint, credential, artifact root/bucket 값을 추가하지 않는다.
- 실제 노바스크 원문을 저장소에 추가하지 않는다.
- Source/Catalog schema, migration, 공개 API, Runtime currentness를 변경하지 않는다.
- 신규 RLS, DB Trigger, 업무 DB 함수를 추가하지 않는다.
- #591 실제 적재 성공이나 운영 공개 승인을 주장하지 않는다.
