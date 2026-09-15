# #591 로컬 후보 검사·Source 저장 기반 검증

- 기준: 최초 검증 develop `5880f355`, 16개 후보 확장 develop `94e5fa8d`, 2026-09-15.
- 구현 담당: 김지혜. 단일 책임 리뷰어 제안: 송은영(수락 여부는 별도 확인).
- 범위: 은영님이 인계 환경을 정리하는 동안 가능한 로컬 검사·합성 통합 검증.
- **실제 노바스크 원문 저장, 실제 MFDS Snapshot 생성, 인계용 적재 및 Retrieval/Citation 승인은 미완료다.**

## 수행한 작업

### 1. 원문 후보 검사 도구

`scripts/rag/verify_mfds_label_candidate.py`는 기존에 확보한 로컬 `EE.xml`, `UD.xml`, `NB.xml`과 선택적 `NN.xml`을 검사한다.

```sh
uv run python scripts/rag/verify_mfds_label_candidate.py --input-dir /approved/private/candidate
```

EE/UD/NB는 비어 있지 않은 `PARAGRAPH` 또는 `ARTICLE title`을 본문으로 인정한다. NB는 실제 MFDS에서 관측된 `사용상의주의사항`과 `사용상주의사항` 제목을 지원한다. NN이 있으면 e약은요 7개 항목의 순서·중복·누락을 검사하고, 공식 문서의 빈 항목은 `PARTIAL_OFFICIAL`과 항목명으로 보고한다. 빈 항목을 임의 본문으로 채우지 않는다.

위 경로는 설명용이며 실제 저장 위치가 아니다. 실제 원문은 승인된 비공개 위치를 사용한다.
이 명령은 네트워크 수집·Source 등록·DB 쓰기·원문 복사·활성화를 하지 않는다.

- EE/UD/NB 세 파일은 필수이며, 각 XML DOC의 type/title이 해당 본문과 일치해야 결과를 출력한다. NN은 파일이 있을 때 함께 검사한다.
- UTF-8만 지원하고, DTD/entity 선언·잘못된 XML·본문 단위가 전혀 없는 문서·파일 크기 초과·파일 symlink를 거부한다. NN의 개별 빈 항목은 문서 전체 실패로 숨기지 않고 부분 상태로 보고한다.
- 원문 바이트를 요약·정규화·재직렬화하지 않는다. 출력은 원본 SHA-256·바이트 크기·구조 집계뿐이다.
- 원문 및 파일 경로를 실패 메시지에 포함하지 않는다.
- 이 도구는 제품 Identity·허가상태·웹 링크와 XML의 결속·의료 본문 완전성을 검증하지 않는다.
- `candidate_xml_valid=true`는 XML 후보 검사 결과다. `product_identity_verified=false`, `source_ingested=false`를 함께 표시한다.
- canonical checksum/문서 ID/locator의 신규 공유 계약을 정의하지 않는다. 원문 SHA-256은 canonical checksum이나 Chunk hash가 아니다.

### 2. 기존 Source 저장 포트의 합성 통합 검증

기존 서비스 컨테이너와 분리한 일회용 PostgreSQL에서 기존 ORM 모델과 Python Service/Repository를 사용했다.
테스트 전용 LOCAL_PRIVATE에 의미 없는 합성 XML 세 개를 보존했다. 실제 MFDS 문서나 사용자 처방은 사용하지 않았다.

`tests/integration/rag/test_source_snapshot_lifecycle.py`에 다음 두 경우를 추가했다.

| 경우 | 확인한 결과 |
| --- | --- |
| 저장·commit | 세 artifact와 PENDING Snapshot·ARTIFACT member 연결 후 commit |
| 새 세션 재조회 | member→artifact 참조로 원문을 읽고 bytes·크기·SHA-256 대조 |
| 동일 원문 재저장 | 기존 content-addressed 원문 참조 재사용 |
| 동일 입력 재실행 | NO_CHANGE와 같은 Snapshot ID, member 수 3 유지 |
| rollback | Snapshot·Run·member가 새 세션에 남지 않음. 불변 원문은 임의 삭제하지 않고 계속 검증 가능 |

원문 보존·무결성 검사는 기존 `LocalPrivateSourceArtifactStore`와 `read_verified_raw_artifact`를 재사용했다.
Snapshot 저장·member 추가는 기존 `persist_product_ingestion_result`와 `append_snapshot_member`를 사용했다.
테스트가 합성 Source의 ACTIVE/APPROVED/ENABLED 상태를 준비하는 것은 test fixture이며, 실제 MFDS 승인 또는 우회 경로가 아니다.

## 검증 결과

- XML 후보 검사와 Source ingestion 관련 테스트: **508개 통과**.
- 실제 일회용 PostgreSQL의 Snapshot lifecycle 테스트: **51개 통과**(추가 2개 포함).
- Ruff check·format, Mypy(678개 소스 파일): 통과.
- 전체 CI 스크립트의 inventory·단일 head·DB 로직 재도입·보호 테이블 쓰기 검사를 실행했다. 전체 DB·Redis 파이프라인은 `envs/.local.env` 부재로 실행하지 못했다.
- 새 RLS·DB Trigger·업무 DB 함수·테이블·migration·공개 API·공유 enum 변경 없음.

### 한계

- 임시 DB는 현재 ORM 모델로 테이블을 구성했다. 운영 migration 적용·writer/reader 최소 권한 검증을 대신하지 않는다.
- XML 도구는 합성 fixture로 검증했다. 이전 노바스크 실측 원문은 메모리 비교만 했으므로 이 도구의 실제 입력으로 재검증하지 않았다.
- member와 artifact 원본 hash 일치는 이번 정상 저장 경로에서 확인했다. 신규 인계 reader의 경로·권한·변조 차단 구현이 완료된 것은 아니다.
- 합성 lifecycle 결과를 실제 XML 수집에 대한 버전·Receipt 계약 승인으로 확대하지 않는다.
- CURRENT 승격·Index 소비·Guide/Chat E2E는 이번 검증에 포함하지 않았다.

## 은영님 환경 정리 후 이어서 할 작업

1. #591에 기록될 DB·artifact 저장소·계정 권한과 인계 기준을 적용한다.
2. 실제 XML 제한 수집 등록·증빙, 제품 집합·Snapshot 버전·원문 locator/member 매핑을 기존 계약과 맞춘다. 기존 제품 목록이나 FAILED e약은요 Receipt를 재사용하지 않는다.
3. 노바스크 Identity·허가상태·공식 상세와 XML 3종의 결속을 확인하고 실제 원문을 지정 위치에 보존한다.
4. 지정 DB에서 Snapshot·Run·artifact·member 저장, commit 후 새 세션 재조회, 원문 hash 대조와 실제 동일 자료 재실행 결과를 남긴다.
5. 현우님이 읽을 Snapshot/member·원문 참조와 승인 상태를 비공개 경로로 인계한다.
6. 첫 제품 완료 후 PR 리뷰·병합, 이후 17개 후보 중 나머지 제품을 확장한다.

환경·권한 정리는 은영님이 담당하며, 같은 환경 문서를 중복 작성하거나 이미 정해진 업무 분담을 다시 확인하는 절차를 추가하지 않는다.
