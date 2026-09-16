# #591 노바스크 첫 제품 실제 적재 결과

2026-09-16: **생산자 측 실제 적재·commit 후 재조회·동일 입력 멱등성 검증 완료**.
대상은 노바스크정5mg ITEM_SEQ `200610660`, EE·UD·NB 3개다. NN·나머지 제품은 제외한다.
이 문서는 사용자(김지혜)가 서버에서 실행하고 공유한 JSON 결과와 관리자 회신을 기록한다.
리뷰어의 서버 직접 실행이나 consumer acceptance 완료를 주장하지 않는다.

## 근거와 실행 경계

- 제한 수집·private 보존 범위: [#591 댓글](https://github.com/AI-HealthCare-05/AH_05_04/issues/591#issuecomment-5691854414).
- 은영님의 등록 상태 동의 및 가빈님의 정책·제품 범위 동의는 담당자 회신으로 확인했다. 공개 댓글의 과거 대기 상태와 구분한다.
- [#643](https://github.com/AI-HealthCare-05/AH_05_04/pull/643) 병합·서버 적용 commit: `91b6062df4bb8437786fb4fca6b5cef0e94a24ec`.
- 가빈님 16:05 회신: 실제 writer 세 테이블 잠금 성공, 업무 열 UPDATE/DELETE 차단(42501), marker 비영 값 거부(23514). 실제 parser·finalizer·member INSERT 3건 후 합성 DB 실패·rollback, Artifact 3개 DELETED 및 writer/reader receipt 재조회 통과. 실제 적재 전 노바스크 Snapshot/Run 각각 0건.
- 서버 검증 보고서와 실제 입력 위치·credential·원문·private journal 전체는 제한 접근 경로에 보존하며 이 PR에 포함하지 않는다.
- Source `MFDS_PRODUCT_LABEL`, Endpoint `MFDS_NEDRUG_LABEL_XML`, Operation `COLLECT_NOVASC_200610660_LABEL_XML`.
- 실행 직전 및 재실행 시 등록 조회: Source ACTIVE, Endpoint VERIFIED/ENABLED/APPROVED, Operation ENABLED/APPROVED, matched_source_rows=1, registration_ready=true.
- 최초 권한 오류 때 남은 Artifact는 cleanup DELETED로 정리한 뒤 재시도했다. 합성 환경 검증과 아래 실제 적재 결과는 별개다.

## 실제 실행 결과

원본 출력의 비민감 필드는 [실행 결과 JSON](novasc-first-ingestion-result.json)에 보존했다.
정확한 실행 UTC 시각과 shell exit code는 제공된 출력에 없으므로 미기록이다. 수집시각으로 대신하지 않는다.

| 항목 | 최초 성공 실행 | 동일 입력 재실행 |
| --- | --- | --- |
| decision | CREATED | NO_CHANGE |
| Snapshot ID | 073ee706-d039-49b5-8ca5-e92cd021f087 | 동일 |
| member_count | 3 | 3 |
| post_commit_requery_passed | true | true |
| expected_result_matches | true | true |

canonical checksum: `a5df76494560f07db5f919b5c37dce4c681cbc214b27450466348a1849039cd3`

source_version: `api:2026-09-16T01:16:39.187000Z:a5df76494560f07db5f919b5c37dce4c681cbc214b27450466348a1849039cd3`

두 실행의 Snapshot ID·member count·canonical checksum·source_version이 일치한다.
`run_ingestion`은 commit 이후 새 session에서 `requery_mfds_label_persistence`를 호출한다.
해당 함수는 Snapshot provenance·Run/Artifact·Member 참조와 각 원문 byte size·checksum을 reader로 검증한다.
이는 코드의 검증 범위와 성공 출력을 함께 해석한 결과이며 개별 Member/Artifact UUID는 이 요약 출력에 없다.

실행 명령의 비민감 형태(입력 경로는 서버에서 비공개 설정):

```sh
sudo -n /usr/local/bin/source591-python < ~/first_ingestion_diagnostic.py
sudo -n /usr/local/bin/source591-python < ~/repeat_ingestion_diagnostic.py
```

두 명령은 실제 적재 호출이다. 첫 결과 확인 후 repeat를 별도로 실행했으며 자동 반복하지 않았다.
위 스크립트는 운영자 로컬 준비 도구이며 저장소 CLI로 새로 제공하는 것이 아니다.

## 원문·버전 해석

서버 반입 때 대조한 입력 기준값이며, 아래 표 자체를 consumer 재검증 결과로 해석하지 않는다.

| 문서 | bytes | raw SHA-256 |
| --- | --- | --- |
| EE | 757 | e0481cf0134377c918192252c2130c7511c561cdc6cdd8d901ba35a28daa53eb |
| UD | 965 | 3abaaeea590bb41e675d56faf8a272e528d8f84fe19d2138abf29c2a625a8bf4 |
| NB | 22750 | 2970de8f769470cd6590226b8fe15d044b18dfba64bf745380b1f5e46abd29e1 |

원문 SHA-256은 디코딩·개행 변환·BOM 제거·XML 재직렬화 전 전체 바이트를 대상으로 한다.
collected_at은 기존 수집 로그의 완료 관측시각 `2026-09-16T01:16:39.187000Z`를 사용한다.
오늘의 로컬/서버 바이트 재검증을 신규 HTTP 수집 성공이나 현재 허가상태 재확인으로 주장하지 않는다.

구현 참조는 위 서버 commit 기준이다:

- `ai_worker/tasks/rag/source_ingestion/mfds_label.py`: schema `mfds-label-selected-product@1`, parser `mfds-label-xml@1`, normalization `mfds-label-xml-structure@1`, canonicalization `mfds-label-selected-product@1`.
- `_canonical_element`/`_canonical_text`/`_canonical_checksum`: 구조·문서 순서 보존, data- 속성 제외, CRLF/CR→LF 및 NFC 정규화 후 canonical manifest hash.
- `ai_worker/tasks/rag/source_ingestion/normalize.py::canonical_json_bytes`: UTF-16 키 정렬·compact JSON·UTF-8.
- `ai_worker/tasks/rag/source_ingestion/source_version.py::build_api_source_version`: UTC collected_at+canonical checksum으로 버전 생성.
- `ai_worker/adapters/local_private_source_artifact_finalizer.py::LocalPrivateSourceArtifactReader`: 원문 크기·checksum 검증 조회.

이는 구현 상수 참조이며 실제 Snapshot의 버전 필드별 별도 조회 출력은 후속 인계에 보완한다.

## 인계와 남은 작업

- [x] 실제 적재 CREATED 및 새 session 재조회 완료
- [x] 같은 입력 NO_CHANGE·동일 Snapshot·checksum 확인
- [ ] 개별 Member/Artifact ID·버전 필드·verification_status·검증 증빙 재조회 및 인계
- [ ] 대상 환경·consumer 계정/실행 절차·원문 조회법·content type/encoding·권한 증빙을 제한된 채널로 전달
- [ ] 현우님 consumer acceptance: Snapshot→Member→Artifact 조회, EE/UD/NB 크기·hash 및 source_version 대조, 원문 read-only 접근, 후속 provenance 적합성 확인
- [ ] 첫 제품 결과 PR 책임 리뷰·CI·merge queue 병합
- [ ] 다음 제품의 목록·수집/보존 승인 범위 확인 후 나머지 제품 순차 진행

실제 원문을 수정·삭제해 권한을 시험하지 않는다. 기존 실제 역할·합성 검증 증빙으로 차단을 확인한다.
Snapshot의 CURRENT는 최신 검증 상태이며 runtime 선정과 동일하지 않다. 이번 출력은 verification_status를 포함하지 않으므로 추정하지 않는다.
내부 저장·consumer 인계 승인과 내부 RAG 사용·Citation/서비스 공개 승인은 구분한다.
본 PR은 #591 전체 종료, 17개 적재 완료, consumer 인계 완료, Guide/Chat E2E 또는 운영 공개 승인 완료를 의미하지 않는다.

구현·기록 담당: 김지혜. 단일 책임 리뷰어: 송은영(@phina-io), 검토 범위는 Source 저장·재조회·멱등성 증빙 및 상태/승인 경계다.
현우님은 consumer acceptance 담당이며 별도 필수 PR 리뷰어로 추가하지 않는다.
