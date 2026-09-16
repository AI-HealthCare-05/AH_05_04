# #593 Source Snapshot 적재 환경·권한·Artifact 저장소 확정 Runbook

상태: Draft / #591 실행 전 환경·권한·인계 기준 정렬 문서. 기본 적재 환경은 2026-09-15 가빈님이 공유한 전용 dev/staging DB와 팀 공용 `LOCAL_PRIVATE` 저장소 준비 결과를 기준으로 한다. 이 문서는 실제 endpoint·credential·root 값을 공개 저장소에 기록하지 않으며, 실제 Source 적재 완료 증빙, cleanup executor 구현 완료, 운영 DB 반영 승인, Runtime 공개 승인 문서가 아니다.

관련 Issue: #593, #591, #166, #526
담당: 송은영 (`@phina-io`)
검토: 김지혜(Source 수집·Worker 실행 가능성)

## 목적

#591 노바스크정 5mg 허가사항 수집·검증·Source Snapshot 저장을 팀원이 같은 기준으로 실행하고 재조회할 수 있도록, 적재 DB/환경, 원문 Artifact 저장소, 접근 권한, 완료·인계 기준을 먼저 고정한다.

이 문서는 구현자가 로컬에서만 성공한 결과를 팀 인계 완료로 오해하지 않게 하고, 후속 Chunk/Index/Guide/Chat 작업자가 Snapshot ID·checksum·Artifact 참조를 같은 환경에서 확인할 수 있게 하는 준비 문서다.

## 적용 범위

### 포함

- #591 첫 제품 Source Snapshot 적재에 사용할 환경 선택 기준
- Source 원문 Artifact 저장소 선택 기준
- 수집 writer, 검증 reader, cleanup executor, consumer 조회 권한 경계
- Snapshot 저장 후 재조회·checksum·member count 확인 기준
- 원문·secret·운영 데이터 비노출 기준
- 환경 확정 전 결과를 로컬 검증으로만 취급하는 경계

### 제외

- 실제 MFDS 허가사항 수집·저장 구현
- 노바스크 외 전체 제품 수집 범위 확장
- Catalog build, Chunk, embedding, Index, Runtime Bundle, Guide/Chat 연결
- 운영 DB 직접 반영
- Trigger, RLS, DB 업무 함수 추가
- 실제 환자 정보, 처방전, 사용자 대화, Provider secret 저장

## 선행 근거

다음 저장소 문서는 #593 판단의 근거이며, 이 runbook은 해당 문서를 대체하지 않는다.

| 구분 | 근거 |
| --- | --- |
| Source Snapshot DB 전환 | `docs/governance/decisions/2026-09-08-source-snapshot-db-transition.md` |
| Source Snapshot 승인 경계 | `docs/governance/decisions/2026-09-09-source-snapshot-approval-boundary.md` |
| Source 수집 구현 상태 | `docs/testing/source-ingestion-165.md` |
| Source·Catalog 관리 경계 | `docs/contracts/proposed/source-catalog-management-398.md` |
| Artifact 정리·보존 경계 | `docs/runbooks/source-artifact-cleanup-347.md`는 기존 합성 cleanup 경계 참고, 실제 #591 cleanup 요청·executor 구현 추적은 #613 |
| 배포 권한 검증 | `tests/contract/test_source_management_deployment.py` |
| Local Artifact store | `ai_worker/adapters/local_private_source_artifact_store.py` |
| S3 Artifact store | `ai_worker/adapters/s3_private_source_artifact_store.py` |
| Source 관리 역할 | `infra/python/source_management_role_policy.py` |

## 환경 선택 원칙

| 환경 | 용도 | #591 인계 증거로 사용 가능 여부 |
| --- | --- | --- |
| 로컬 DB | 구현자 단위 검증, migration·adapter 동작 확인 | 단독으로는 불충분 |
| 팀 공용 dev/staging DB | 팀원이 같은 Snapshot과 Artifact 참조를 재조회하는 인계 기준 | 권장 |
| 운영 DB | 검증·승인된 Source만 별도 절차로 반영 | 이번 범위 제외 |

#591의 기본 결정은 팀 공용 dev/staging 적재 환경이다. 로컬 DB 결과는 구현 검증에는 사용할 수 있지만, 후속 AI/RAG consumer가 Chunk/Index 작업에서 같은 증거를 재조회해야 하는 인계 완료 기준으로 사용하지 않는다.

## 기본 결정

| 항목 | 결정 |
| --- | --- |
| 적재 DB | 팀 공용 dev/staging DB를 사용한다. 개인 로컬 DB는 구현 검증용으로만 사용한다. |
| 운영 DB | 이번 #591 범위에서는 직접 반영하지 않는다. 운영 반영은 검증·승인된 Snapshot 이후 별도 절차로 진행한다. |
| Artifact 저장소 | 팀 공용 `LOCAL_PRIVATE` root를 우선한다. 데모 기간에는 같은 EC2 제한 접근과 OS 파일 권한으로 즉시 검증·재조회할 수 있기 때문이다. `S3_PRIVATE`는 bucket/KMS/IAM 운영 정책 확정 후 전환 후보로 둔다. 개인 PC 로컬 경로는 최종 인계 기준이 아니다. |
| 권한 | 수집 writer, 검증 reader, cleanup executor, consumer reader를 역할 기준으로 분리한다. 실제 credential 이름과 secret은 공개 문서에 쓰지 않는다. |
| 인계 증거 | Snapshot ID, source_version, canonical checksum, artifact key, member count, 재조회 가능 여부를 기준으로 한다. |

운영 DB 반영은 #591 완료 조건이 아니며, Source 공개·Runtime 사용·사용자 응답 근거로 쓰려면 별도 승인과 공개 게이트를 따른다. 실제 DB endpoint, credential, bucket/root 값은 공개 저장소에 기록하지 않고 접근 제한된 비밀 저장소에 별도 보관한다. 2026-09-15 공유된 준비 결과 기준으로 dev/staging DB와 팀 공용 `LOCAL_PRIVATE` 저장소는 준비됐지만, #591 Source parser·적재 command가 준비되고 실제 저장·재조회·checksum 검증이 끝나기 전에는 #591 인계 완료로 보지 않는다. 실패 artifact cleanup 요청·executor 구현은 #613에서 추적한다.

## 확정해야 할 값

아래 항목은 #591 실제 적재 전에 값이 정해져야 한다. 미정 항목은 임의 기본값으로 대체하지 않는다.

| 항목 | 결정값 | 확인자 | 비고 |
| --- | --- | --- | --- |
| 적재 DB/환경 | 팀 공용 dev/staging DB | 송은영 | 로컬은 구현 검증용, 운영 DB는 별도 승인 후 반영 |
| DB 접속 주체 | 역할 기준 분리 | 송은영 | 수집 writer, 검증 reader, cleanup executor, consumer reader를 분리. 실제 계정명은 제한 접근 위치에 별도 기록 |
| Artifact backend | 팀 공용 `LOCAL_PRIVATE` root 우선, `S3_PRIVATE` 후속 전환 후보 | 송은영, 김지혜 | 2026-09-15 provision 준비 결과 공유됨. 실제 root는 접근 제한된 비밀 저장소에만 기록 |
| Artifact root/bucket | 접근 제한된 비밀 저장소에 별도 기록 | 가빈, 송은영 | GitHub/Discord에 secret·원문 경로·credential을 노출하지 않음 |
| Source code | TBD | 김지혜 | #591 수집 대상 Source 식별자 |
| Endpoint/Operation code | TBD | 김지혜 | 효능효과/용법용량/주의사항 범위 확인 |
| source_version 형식 | TBD | 김지혜 | 기존 Source version 문법과 충돌 금지 |
| canonicalization/parser version | TBD | 김지혜 | 저장 결과 checksum 재현 기준 |
| 인계 대상 product | 노바스크정 5mg / ITEM_SEQ `200610660` | 김지혜 | 첫 제품 범위 |
| 후속 consumer | 후속 AI/RAG consumer | 김지혜 | Chunk/Index/Guide/Chat 인계 기준만 기록 |

## 권한 경계

정상 경로의 역할은 최소 권한으로 분리한다. 한 계정이 모든 쓰기·검증·정리 권한을 갖는 방식은 #591 인계 기준으로 사용하지 않는다.

| 역할 | 허용 | 금지 |
| --- | --- | --- |
| 수집 writer | Source 수집 실행, Snapshot 후보와 Artifact 참조 기록, 최종 Artifact read-only 재조회 | 최종 Artifact 쓰기·삭제·덮어쓰기, cleanup 실행, 운영 DB 직접 반영 |
| Artifact finalizer | stdin 원문과 checksum 검증 후 최종 content-addressed 객체 보존 | Source DB 접근, 임의 경로 쓰기, Artifact 삭제 |
| 검증 reader | Snapshot, member, verification, ingestion run, Artifact receipt 조회 | Source 데이터 수정·삭제 |
| cleanup executor | 승인된 정책에 따른 미참조 Artifact 조사·정리 | 수집 결과 생성, 승인 없는 삭제 |
| consumer reader | 인계된 Snapshot ID/checksum/member/artifact 참조 조회 | 원문 bytes 직접 노출, Source 관리 변경 |

실제 DB credential 이름과 secret은 이 문서에 쓰지 않는다. 권한 부여 SQL 또는 infra helper가 필요하면 별도 PR에서 안전한 placeholder와 검증만 기록한다.

## Artifact 저장소 기준

원문 Artifact는 GitHub Issue, PR 본문, Discord, 일반 로그에 첨부하지 않는다. 저장소에는 안전한 참조, checksum, content type, size, object key의 공개 가능한 식별자만 기록한다.

### `LOCAL_PRIVATE` 사용 시

- 접근 제한된 전용 root를 사용한다.
- writer의 업로드·임시 공간과 최종 Artifact root를 분리한다.
- 최종 Artifact는 별도 owner의 고정 finalizer만 보존하고 writer에는 read-only mount만 제공한다.
- cleanup executor의 검증된 삭제 mount와 consumer의 read-only mount를 writer 권한과 분리한다.
- 앱 기본 업로드 저장소나 임시 다운로드 폴더를 공유하지 않는다.
- root 권한, 파일 권한, checksum 검증이 기존 adapter 기준과 맞아야 한다.
- 팀원이 같은 환경에서 재조회할 수 없는 개인 로컬 경로는 최종 인계 기준이 아니다.

### `S3_PRIVATE` 사용 시

- bucket, prefix, server-side encryption 기준을 명시한다.
- access key와 secret key는 설정 파일, DB, 로그, GitHub에 저장하지 않는다.
- AWS SDK 표준 credential provider chain 또는 승인된 실행 역할을 사용한다.
- 평문 HTTP endpoint, URL credential, query/fragment credential은 허용하지 않는다.

## 실제 provision 담당과 비공개 전달 기준

#591 적재용 dev/staging DB와 팀 공용 `LOCAL_PRIVATE` root provision은 가빈님이 진행했고, 2026-09-15 접근 제한 위치의 README/STATUS로 준비 결과가 공유됐다. 송은영은 #605에서 공개 가능한 환경 기준과 검증 절차를 반영하고, 실제 endpoint, credential, root 값은 GitHub Issue, PR, Discord에 남기지 않는다. 공개 채널에는 준비 여부, env key, 검증 결과만 기록한다.

공개 채널에는 다음 항목만 확인한다.

- 운영·배포 DB와 분리된 dev/staging DB인지
- writer / reader / consumer 권한이 분리됐는지
- cleanup 실행 권한은 아직 준비되지 않았고 #613에서 추적되는지
- 팀 공용 `LOCAL_PRIVATE` root가 준비됐는지
- 실제 endpoint·credential·root가 접근 제한된 비밀 저장소에 저장됐는지
- 작업 담당자와 필요한 검토자에게 접근 권한이 부여됐는지

## #591 실행 경로 상태

#609가 병합돼 MFDS EE/UD/NB/NN parser·normalization·Snapshot 저장 command는 준비됐다. 실제 #591 적재는 #613의 finalizer·cleanup 경계와 역할별 OS mount가 적용되고 합성 E2E가 끝난 뒤 시작한다.

## #591 적재 전 확인 체크리스트

아래 체크는 공개 문서에서 확정 가능한 기준의 결정 상태를 뜻한다. 실제 DB 계정, endpoint, artifact root/bucket 값은 제한 접근 위치의 실행 준비 기록에서만 확인한다.

- [x] 적재 DB/환경의 기본 방향은 팀 공용 dev/staging DB로 정했다.
- [x] 2026-09-15 공유된 준비 결과 기준으로 운영 DB와 분리된 dev/staging DB가 준비됐다.
- [x] Artifact backend는 팀 공용 `LOCAL_PRIVATE` root 우선으로 정했다.
- [x] 2026-09-15 공유된 준비 결과 기준으로 팀 공용 `LOCAL_PRIVATE` 저장소가 준비됐다.
- [x] writer/reader/consumer 권한 경계는 역할 기준으로 분리한다.
- [ ] finalizer와 writer read-only·executor delete·consumer read-only mount가 준비되고 합성 cleanup E2E가 통과했다. #613에서 처리한다.
- [x] #591 대상 Source parser·정규화·적재 실행 경로가 존재한다. #609에서 처리했다.
- [ ] Source, Endpoint, Operation 식별자가 정해졌다.
- [ ] source_version, parser/canonicalization version이 정해졌다.
- [ ] 원문 Artifact를 공개 채널에 올리지 않는 접근 제한 비밀 저장소 전달 방식이 정해졌다.
- [ ] 로컬 검증 결과와 팀 인계용 dev/staging 결과의 차이를 문서화했다.
- [x] 운영 DB 반영은 이번 범위가 아니며 별도 승인 후 진행한다고 정리했다.

## #591 적재 후 인계 기준

첫 제품 적재를 완료했다고 말하려면 아래 항목을 같은 환경에서 재조회할 수 있어야 한다.

| 항목 | 성공 기준 |
| --- | --- |
| Snapshot | Snapshot ID, status, source_version, canonical checksum 조회 가능 |
| Member | EE/UD/NB/NN 합의된 member count와 locator 조회 가능 |
| Ingestion run | run status, collected_at, parser/canonicalization version, record count 조회 가능 |
| Artifact | backend/key, size, sha256, content type, raw/reject 종류 조회 가능 |
| Verification | checksum 대조와 검증 상태 조회 가능 |
| 재실행 | 동일 입력·동일 source_version·동일 checksum·동일 member set 재실행 시 NO_CHANGE 또는 기존 Snapshot 재사용 등 합의된 멱등 결과 확인 가능. checksum/source_version/member mismatch는 fail-closed 또는 수동 검토로 종료 |
| 인계 | 후속 AI/RAG consumer가 Snapshot 기준으로 후속 Chunk/Index 입력 범위를 식별 가능 |

위 기준이 충족되지 않으면 로컬 검증 또는 부분 조사 결과로만 기록하고, Source Snapshot 인계 완료로 쓰지 않는다.

## 실패와 보류 기준

다음 상황에서는 #591 적재 완료로 처리하지 않는다.

- 적재 DB/Artifact 저장소가 개인 로컬에만 존재한다.
- 원문 Artifact를 재조회할 수 없거나 checksum을 대조할 수 없다.
- source_version 또는 canonicalization version이 미정이다.
- 일부 member만 저장됐는데 후속 consumer가 완전한 허가사항으로 오해할 수 있다.
- Provider 원문, secret, URL credential, 개인정보가 로그나 공개 채널에 노출됐다.
- 운영 DB에 직접 반영했지만 별도 승인·공개 게이트가 없다.

실패·보류 시에는 원문 없는 고정 reason과 안전한 참조만 남기고, 성공 Snapshot으로 승격하지 않는다. 적재 transaction 실패는 DB rollback으로 처리하며, Artifact 저장 후 DB commit 전에 실패한 경우 writer는 삭제하지 않고 run ID·artifact key·checksum·failure reason·requested_at만 cleanup 요청으로 남긴다. 지정된 cleanup executor는 Snapshot/member/run/artifact receipt 참조가 없는지 확인하고, 참조가 하나라도 있으면 삭제하지 않고 `BLOCKED` 또는 `MANUAL_REVIEW`로 남긴다. 삭제 또는 quarantine 결과는 대상 key, checksum, 확인한 참조 범위, 실행자, 실행 시각, 결과 receipt로 남긴다. 실제 #591 cleanup 요청·executor 구현과 권한 준비는 #613에서 추적한다.

MFDS 원본 보존 뒤 DB rollback이 발생한 경우에는 [#613 전용 절차](./source-artifact-cleanup-613.md)를 사용한다. #347 합성 cleanup schema나 30일 보존 배치를 실제 실패 복구 권한으로 사용하지 않는다. writer가 삭제 가능한 현재 구성은 준비 완료가 아니며, 별도 executor 역할·mount와 private cleanup journal을 제한 접근 환경에서 검증해야 한다.

## 보안·Privacy·의료 안전 기준

- 실제 환자 정보, 처방전, 사용자 대화, OCR 원문은 #591 Source 적재 대상이 아니다.
- MFDS 허가사항 원문도 공개 Issue/Discord에 첨부하지 않고 접근 제한 Artifact 저장소에 둔다.
- DB URL, access key, secret key, Provider credential은 문서·로그·오류 응답에 남기지 않는다.
- Source Snapshot은 후속 RAG/Guide/Chat 근거가 될 수 있으므로, 검증되지 않은 결과를 Runtime current 또는 운영 공개 근거로 사용하지 않는다.
- 무결성·중복·상태 판정은 애플리케이션 Service/Repository와 DB 제약으로 처리하며, 이번 범위에서 RLS, Trigger, DB 업무 함수를 추가하지 않는다.
- 운영 DB 반영, Runtime Bundle 활성화, 사용자 응답 공개는 별도 승인 게이트를 따른다.

## 담당 인계

| 담당 | 확인할 내용 |
| --- | --- |
| 송은영 | 적재 DB, 권한, Artifact 저장소, 보안 경계 확정 |
| 김지혜 | #591 수집·검증·Snapshot 저장 실행 가능성, 실패 시 안전한 reason 기록 |
| 후속 AI/RAG consumer | Snapshot ID/checksum/member/artifact 참조가 Chunk/Index 후속 입력으로 충분한지 확인 |
| 권가빈 | 운영 공개 또는 사용자-facing 근거로 쓰는 경우 제품·Privacy 승인 필요 여부 확인 |

## 후속 작업

- #591 본문에 확정된 적재 환경과 인계 기준 반영
- #609에서 구현한 #591 Source parser·정규화·적재 실행 command 사용
- #613에서 #591 적재 실패 artifact cleanup 요청·executor 절차 구현
- 필요한 경우 env example 또는 infra 권한 검증 보강
- #591 첫 제품 실제 수집·검증·Snapshot 저장 PR 작성
- Snapshot 인계 후 Chunk/Index/Guide/Chat 후속 작업 연결
