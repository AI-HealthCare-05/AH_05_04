# #166 Catalog DB 통합 후속 구현 계획

- 상태: 1단계 조사 완료 / 후속 구현 계획. 새 공유 계약 승인 또는 DB 구현 완료가 아니다.
- 작성: 김지혜, 2026-09-08
- 구현 담당: 김지혜. Candidate·RAG 의미 계약 리뷰: 정현우. DB·FK·transaction 리뷰: 송은영.
- 기준: develop `e20acb9` (#353 병합 포함), 작업 브랜치 `feat/166-catalog-db-integration`.
- 최신 협의 입력: 김지혜가 제공한 `issue-166-d05-hash-calculation-draft-v2.md`.
  비공개 공유 초안 자체는 저장소에 복제하지 않고 이번 구현 경계만 기록한다.
- 관련: 기존 #166의 남은 범위. #347 승인과 독립적으로 진행하며 별도 이슈·PR을 자동 생성하지 않는다.

## 이번 구현의 고정 경계

기존 `medication-catalog-v2` / `catalog-manifest-envelope-v2`를 유지한다.
Candidate Index에 전달하는 `catalog_manifest_hash`는 계속 기존 envelope hash다.
필드명·payload·계산 의미를 조용히 바꾸거나 새 hash 종류/spec을 기존 envelope 안에 삽입하지 않는다.
DB 보존용 hash metadata와 현재 인계 payload는 구분한다.

다섯 값(export checksum, Catalog envelope, Candidate projection, Candidate Index manifest,
Runtime medication Catalog manifest)의 의미는 구분한다. 그러나 Candidate projection 및 Runtime
medication Catalog manifest 분리는 새 계약 버전 제안이며 이번 v2 저장에 즉시 적용하지 않는다.
승인 Product Identifier 집합, 명시적인 Entry 본문 hash, projection allowlist, canonicalization fixture가
준비되고 계약이 확정된 뒤 전환한다. 내부 member hash를 Entry 본문 hash라고 간주하지 않는다.

D-02는 미확정이다. ingestion run을 normalization run으로 바꾸어 부르거나 별도 run 테이블을
임의로 만들지 않는다. Source의 Snapshot 생성 충돌 검사로 Catalog 재처리 실행을 식별할 수는 없다.
실제 Source 승인 adapter와 Catalog 승인 adapter의 권한·회수·유효기간 검증도 저장 성공과 별개다.
Runtime 활성화·Production 공개는 이번 완료 범위가 아니다.

## 현재 구현과 변경·검증 대상

| 대상 | 기준 커밋에서 확인한 구조 | 후속 변경 또는 유지 방향 | 검증 항목 |
| --- | --- | --- | --- |
| Product | Snapshot·code system·code unique, source record 보존 | 안정 Identity와 Snapshot별 구성원을 구분. UUID를 공식 Identity로 대체하지 않음 | 동일 공식 Identity의 버전 계보, 다른 Identity 분리 |
| Ingredient | Snapshot·정규화 이름 unique, 공식 code nullable | 독립 Ingredient registry와 이름이 같은 다른 공식 Identity를 수용할 DB 기준 검토 | 동일 성분 재사용 성공, 같은 이름의 다른 code 보존, 상충 정의 거부 |
| Alias | Product/Ingredient ID와 동일 Snapshot composite FK, 대상·정규화 문자열 unique, is_approved boolean | 안정 대상 참조, Alias 자체 출처와 대상 출처 분리, D-03 상태·전환안 필요 | 교차 Snapshot 입력, 반복 Alias 출처 보존과 결정적 Search Entry 선택, PENDING 비승격 |
| Component | product_id·ingredient_id·role unique, 양쪽 대상과 동일 Snapshot FK | D-04에서 실제 Source·Loader 근거로 자연키·출처·relationship 확정 | 고아 참조, role 반복 정상/충돌 구분, 양쪽 provenance 정합 |
| Identity/Crosswalk/Set/Search Entry | 해당 영속 모델 없음 | 기존 Catalog 4개 테이블을 확장하고 D-03의 정확한 Set/member 범위를 설계 | 구성원 적격성, 불변 집합, 참조 결속 및 중복 방지 |
| Source 실행 | ingestion run 존재, Snapshot에 normalization_version 존재, 정규 normalization run 없음 | D-02 인계 전 최종 실행 FK 보류 | 같은 Snapshot·다른 Catalog 실행 구분, Source·실행 결속 |
| Source uniqueness | operation_id·source_version, FAILED 제외 partial unique | #166에서 Source 정책을 임의로 재정의하지 않음 | #165 FAILED/NO_CHANGE/충돌 및 계보 회귀 유지 |
| 저장 포트 | CatalogBuildRepository.save_build(members, artifacts), 서비스가 검증 후 호출 | PostgreSQL adapter 및 transaction 조립 필요 | mapping/검증 실패 저장 미호출, 저장 예외의 성공 변환 금지 |
| Backend repository | 구성원별 create 후 flush, 외부 session 사용 | 기존 CRUD 자체를 Catalog 전체의 원자적 저장이라고 간주하지 않음 | commit 책임, Set·manifest 포함 rollback·재시도·동시성 |
| 승인 | 선택적 CatalogApprovalVerifier, receipt 없으면 NOT_APPROVED/STALE | DB 저장으로 승인 승격하지 않음. 실제 adapter의 권한 검증과 읽기/소비 gate 연결 | 위조·회수·만료·Source subset·checksum 불일치 거부 |
| Hash | JSONL checksum과 envelope hash 존재 | 종류·schema/spec·값·불변 계산 대상 참조를 DB에 함께 보존하는 설계 | DB 왕복 후 bytes/hash 재현, digest 단독 대체 거부 |
| Runtime | 기준 커밋의 모델·migration에 Runtime Bundle 영속 기반 없음 | #355 및 Evidence/Citation 산출물 반영 여부를 migration 직전 재확인 | 별도 medication manifest 계약 전 기존 hash를 Runtime 값으로 대입하지 않음 |

### 조사 근거

- [Catalog 모델](../../../backend/app/models/rag_catalog.py)
- [Source 모델](../../../backend/app/models/rag_source.py)
- [기존 Backend repository](../../../backend/app/repositories/rag_source_catalog_repository.py)
- [Catalog build](../../../ai_worker/tasks/rag/catalog/build.py), [서비스·저장 포트](../../../ai_worker/tasks/rag/catalog/service.py)
- [export 생성·검증](../../../ai_worker/tasks/rag/catalog/export.py), [승인 포트](../../../ai_worker/tasks/rag/catalog/approval.py)
- [Candidate 소비 경계](../../../ai_worker/tasks/rag/candidate_index.py)
- [v2 계약](../../contracts/targets/post-mvp-1/catalog-build-v2.md)
- [Source 목표·현재 최소 구현](../../contracts/targets/post-mvp-1/rag-source-ingestion-v1.md)
- [Source DB 전이 결정 기록](../../governance/decisions/2026-09-08-source-snapshot-db-transition.md)
- [Catalog 검증 기록](../../testing/catalog-build-166.md)

기존 v2 문서의 `165f90716263` head는 #329 당시 기록이다. 이번 조사 기준 head는
`169b2c3d4e5f`이며 이를 새 migration의 고정 부모로 예약하지 않는다.
기존 문서의 검증 수치·과거 기준점을 이번 실행 증빙으로 재사용하지 않는다.
현재 DB의 실제 Catalog 데이터 유무는 조회하지 않았으므로 빈 테이블을 전제로 이행안을 만들지 않는다.

## 결정 항목과 착수 경계

| 항목 | 상태 | 1~3단계에서 할 일 | 최종 DB 연결 전 필요한 결과 |
| --- | --- | --- | --- |
| D-02 실행 provenance | 미확정 | Snapshot/Source 입력과 실행 참조의 차이·필요 사례 정리 | #164·#165 인계 key, 소유 범위, 재처리·재시도 식별 및 FK 기준 |
| D-03 Identity·Alias/Crosswalk Set | 세부 제안 필요 | 대상·버전·상태·기존 boolean 이행 매핑 설계 | Set 포함 범위·READY 불변성·승인 전환 규칙 리뷰 |
| D-04 Component | 최종 자연키 미확정 | Parser/Loader·합성 fixture에서 role/출처 근거 수집 | 실제 Source 근거와 자연키·source_record_key·relationship 선택 |
| D-05 | 이번 v2 유지 확인, 물리 저장 위치 검토 필요 | 기존 hash fixture 및 저장 metadata 설계 | 저장 위치·참조·유일성 리뷰. 새 projection 계산은 별도 계약 전환 |
| D-06 transaction | 세부 제안 필요 | 쓰기 순서·멱등키·실패 감사·재시도 설계 | 원자성·동시성·실패 감사 transaction 및 소비 가시성 기준 |

기술적인 세부안은 구현 담당자가 근거와 함께 제안하고 PR 리뷰로 확인한다.
미확정된 공유 의미·FK를 먼저 확정값으로 구현하지 않으며, 독립된 작업은 계속 진행한다.
추가 이슈 생성이 필요해지면 먼저 사용자에게 범위와 이유를 설명한다.

## 6단계와 산출물

1. **현재 단계 — 대조와 기준 고정:** 최신 develop 브랜치, 현재 구조·차이·인계 목록, 기존 회귀 기준 확인.
2. **v2 hash 검증:** 기존 fixture의 부족한 사례를 보강하고 고정 canonical bytes/digest로 검증한다.
   기대 hash를 테스트 실행 시 같은 생산 함수로 만들어 비교하지 않는다. DB용 metadata 설계가
   기존 export bytes에 영향을 주지 않게 한다. NFC/NFD 원문 보존 회귀는 현재도 검증 가능하다.
3. **저장 모델·적재 규칙:** D-03·D-04·D-06 구체안, DB 비의존 검증·mapping 구현.
   스키마를 먼저 만들기 위한 임시 ingestion FK나 가짜 normalization run을 넣지 않는다.
4. **DB schema·migration:** D-02 및 관련 공유 기준 인계 후 구현. 은영님 Evidence/Citation 병합 뒤
   최신 develop의 head를 다시 확인한다. 기존 행 이행, upgrade/downgrade, 단일 head, 제약 검증을 한다.
5. **Adapter·PostgreSQL:** 구성원·Set·manifest 원자 저장, 실제 승인 경계, 실패 지점별 rollback,
   동일 요청 재시도·충돌·READY 변경 거부·부분 공개 방지 및 기존 Source 회귀를 검증한다.
6. **Candidate 인계·PR:** DB에서 복원한 v2 artifacts를 공개 Candidate 입력으로 검증하고
   재현 증빙·계약 상태·PR 초안을 갱신한다. 사용자가 PR을 생성한다.

단계 4의 인계가 늦으면 완료한 1~3단계를 보존하고 대기 항목을 명시한다. 문서나 가짜 adapter를
실제 DB 통합 완료로 보고하지 않는다. 이번 후속 PR과 #166 이슈 종료는 별도로 판정한다.
새 계약 전환 등 남은 항목은 #166에 명시하며 자동 종료 키워드를 사용하지 않는다.

## 1단계 검증

기준 develop `e20acb9`에서 실행했다. 이번 단계는 계획 문서만 추가하며 코드·migration은 변경하지 않는다.

```text
PYTHONPATH=backend:. python -m pytest ai_worker/tests/rag/catalog ai_worker/tests/rag/test_candidate_index.py -q
177 passed
```

Python은 공유 테스트 환경의 3.13을 사용했다. PostgreSQL 접속·migration 적용·실제 승인·Runtime은
실행하지 않았다. 이번 결과는 Catalog 계산·Candidate 단위 회귀이며 DB 적재 성공 증빙이 아니다.
문서 상대 링크, Markdown 표 구조, 전체 변경 diff와 공백 오류를 확인한다.

## 2단계 — v2 bytes 검증과 저장 metadata 설계

### 보존 모델 설계 (아직 DB 컬럼·DTO 확정 아님)

| 속성 | 이번 v2에서 보존할 의미 | 책임·제약 검토 |
| --- | --- | --- |
| hash_kind | JSONL export checksum 또는 Catalog envelope hash | 내부 저장 구분자. 정확한 enum·컬럼명은 DB 리뷰 후 결정. v2 envelope 입력에 새 key를 추가하지 않음 |
| schema_version | medication-catalog-v2 | 소비자가 요청한 계약과 일치 확인 |
| canonicalization_spec_version | envelope는 catalog-manifest-envelope-v2 | export는 현재 v2 JSONL 규칙을 식별할 저장 표현을 검토. 새 spec을 임의 발급하지 않음 |
| hash_value | 각 대상 bytes의 SHA-256 | hash 문자열만으로 종류나 권한을 판단하지 않음 |
| 계산 대상 참조 | 불변 manifest payload 또는 이를 정확히 복원할 구성원·Set 참조, JSONL 계산 대상 | digest만 저장하지 않음. DB에서 복원한 bytes와 재계산 digest가 동일해야 함 |

현재 v2 producer·consumer 전달 구조와 이 내부 보존 모델을 분리한다. 유일성 범위를 digest 단독으로
결정하지 않고 종류·schema/spec·계산 대상 및 실행 식별의 관계를 검토한다. D-02 실행 FK는 미확정이다.
Runtime medication hash와 Candidate projection hash의 placeholder 값을 저장하거나 envelope hash를
그 종류로 표시하지 않는다. 기존 manifest JSON 파일 checksum은 envelope hash가 아님을 fixture로 구분한다.

### 검증 보강

승인된 합성 v2 입력, canonical JSONL, envelope 계산 bytes, 전달 manifest 및 기대 digest를
`tests/fixtures/rag/catalog/hash-v2/`에 고정했다. 테스트는 기대값을 생산 함수로 매번 재생성하지 않는다.
입력·Source refs·receipt 순서 반전, NFD 표시값 보존, LF/CRLF·마지막 LF·BOM 변경,
checksum 재결속을 시도해도 비정규 JSONL 거부, 다른 종류의 digest 대입, envelope 필드 변조,
최상위·중첩 JSON 중복 key 거부를 Catalog 및 공개 Candidate 인계 경계에서 검증한다.

기존 manifest 파서가 중복 key의 마지막 값만 사용해 검증을 통과하는 실패 사례 2건을 먼저 재현했다.
파싱 시 중복 key를 거부하도록 보완했다. 정상 v2 계산식·필드·오류 코드는 유지한다.
동일한 값을 반복한 중복 key도 거부하며, 실패 상세는 기존 고정 경로만 사용한다.
실제 승인 만료·회수 검증은 승인 저장소 adapter 후속 범위다. 이번 fixture의 승인 상태는 합성이다.
DB 원자 저장·rollback은 아직 실행하지 않았다.

2단계 최종 실행 결과:

```text
python -m pytest ai_worker/tests/rag -q
937 passed
ruff check ai_worker/tasks/rag ai_worker/tests/rag
All checks passed
MYPYPATH=backend:. python -m mypy ai_worker/tasks/rag
Success: no issues found in 36 source files
```

고정 파일 3개의 SHA-256은 별도 `shasum -a 256` 결과와 `expected.json`의 값이 일치했다.
신규 hash 회귀는 22건이다. 변경 Python 파일 format 검사 및 전체 diff 공백 검사를 함께 수행했다.
PostgreSQL·Frontend·실제 외부 Source 승인·Runtime 검증은 이번 단계에서 수행하지 않았다.
