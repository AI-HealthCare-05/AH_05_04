# #591 만성질환 시연용 허가사항 수집·저장·인계 검토안

> 은영님·현우님 답변을 반영한 진행 기준입니다. #591은 노바스크 첫 제품 수집·검증·Snapshot 저장에 집중합니다. 은영님이 DB·artifact 저장소·권한·인계 기준을 정리하는 동안 로컬 검증을 진행하며, 환경 확정 전에는 실제 인계용 적재 완료로 기록하지 않습니다. 아래 버전·수집 등록 세부안은 별도로 확정될 때까지 제안입니다.

| 항목 | 내용 |
| --- | --- |
| 관련 이슈 | [#591](https://github.com/AI-HealthCare-05/AH_05_04/issues/591) |
| 작성일·조사 기준 | 2026-09-15 · 최초 조사 `58aa7286`, 로컬 검증 `5880f355` |
| 구현 담당 | 김지혜 |
| 단일 책임 리뷰어 제안 | 송은영 — 아직 지정·수락되지 않음 |
| 소비 인터페이스 협의 | 정현우 — 원문 인계 후 Chunk·검색·Guide/Chat 연결 |
| 현재 상태 | 첫 제품 경로 확인, XML 후보 검사 도구·합성 DB 저장 검증 완료. 실제 원문 적재·인계·사용 승인은 미완료 |

## 최신 협의 반영

- 은영님이 적재 DB·환경, artifact 저장소, writer/reader/cleanup/consumer 권한과 실제 인계 기준을 정리해 #591에 연결한다. 같은 환경 문서를 별도로 중복 작성하지 않는다.
- 지혜님은 기존 포트를 이용한 로컬 검증을 진행하고, 환경 확정 후 지정 기준으로 실제 원문 저장·재조회·checksum 증빙을 남긴다.
- 현우님 17개 요청서에 따라 후속 후보는 처방약 12개와 OTC 5개다. 첫 제품 완료·리뷰·병합 후 다이아벡스·리피토, 이어 다이크로짇·이지엔6 애니 순서로 확장한다.
- 나머지 OTC 후보는 타이레놀정500mg·판콜에스내복액·겔포스엠현탁액·지르텍정이다. 공식 Identity 재확인과 e약은요 제한 수집 gate 검증이 필요하다.
- 이번 로컬 검증은 합성 데이터만 사용한다. 합성 Source의 준비 상태와 임의 version/hash를 실제 MFDS 승인 증빙으로 재사용하지 않는다.
- [로컬 검증 결과](../../validation/rag/issue-591/local-storage-validation.md)를 실제 노바스크 적재 증빙과 구분한다.

## 1. 왜 새 작업이 생겼는가

### 기존 Plan B에서는 제품별 근거 RAG 연결을 보류했다

앞서 Plan B 사전 확인에서는 e약은요의 기존 Receipt에 자연키 차단이 남아 있는 것을 확인했습니다. 당시 범위와 일정에 따라 제품별 근거 RAG를 위한 #526 추가 구현을 보류하고, #566에 판단 근거와 재개 조건을 기록했습니다. 해당 확인은 API 실측·차단 증빙 검토였으며, RAG가 읽을 복약정보를 실제 Source Snapshot으로 적재한 상태는 아니었습니다.

### 이후 현우님이 실제 원문 적재를 별도 후속 작업으로 요청했다

현우님은 사용자 확정 처방약의 주의사항을 복약 가이드에 표시하고, 챗봇도 같은 공식 근거로 답변할 수 있도록 **실제 원문을 확보·저장해 인계하는 작업**을 요청했습니다.

전체 의약품을 대상으로 하면 범위가 커지므로, 프로젝트의 고혈압·제2형 당뇨병·이상지질혈증 시연에 사용할 12개 제품 후보로 좁혔습니다. 자료 종류도 **제품별 MFDS 공식 허가사항의 효능효과·용법용량·사용상의주의사항**으로 정했습니다.

따라서 이번 #591은 기존 보류 기록을 유지하면서 진행하는 별도 후속 작업입니다. e약은요 전체 자연키 해결, DUR, #166 주성분 QNT 문제와 #577 Worker 조립은 포함하지 않습니다. 기존 수집 차단을 풀거나 과거 Receipt를 새로운 원문의 승인 근거로 재해석하지 않습니다.

## 2. 왜 노바스크부터 하는가

현우님이 전달한 12개 후보 중 **노바스크정 5mg을 첫 연결 제품으로 지정**했습니다. 먼저 한 제품으로 수집→실제 저장→재조회→인계를 끝까지 검증하고, 그 구현을 리뷰·병합한 뒤 다른 제품에 적용하려는 순서입니다.

| 진행 순서 | 대상 |
| --- | --- |
| 첫 제품 | 노바스크정 5mg |
| 첫 PR 병합 후 기본 확장 | 다이아벡스정 500mg, 리피토정 10mg |
| 제형·복합제 구분 확인 후 확장 | 다이아벡스XR 서방정 500mg, 엑스포지정 5/80mg |
| 나머지 후보 | 코자정 50mg, 다이크로짇정(25mg), 자누비아정 100mg, 자디앙정 10mg, 아마릴정 2mg, 크레스토정 5mg, 이지트롤정(10mg) |

이는 서비스 지원 제품 후보이며, 한 환자의 병용 처방 조합이나 복용량 권고가 아닙니다. 후보 표기를 그대로 정식 제품명으로 저장하지 않고, 실제 수집 때 공식 식별정보와 대조합니다. 나머지 11개는 이번 첫 제품 확인의 완료 범위에 포함되지 않습니다.

## 3. 현재 어디까지 확인했는가

### 완료: 노바스크 제품 식별과 공식 본문 다운로드 경로

| 항목 | 확인 결과 |
| --- | --- |
| 정식 제품명 | 노바스크정5밀리그람(암로디핀베실산염) |
| ITEM_SEQ | `200610660` |
| 업체 | 비아트리스코리아(주) |
| 성분·함량 | 암로디핀베실산염 6.944mg, 암로디핀으로서 5mg |
| 성상·제형 | 흰색의 팔각형 정제 |
| 공식 본문 | 제품 상세에서 연결된 효능효과·용법용량·주의사항 XML 3종 모두 HTTP 200 |
| 본문 대조 | XML 문단과 ARTICLE 제목이 해당 제품의 상세 본문에서 확인됨 |

세 XML은 일반 첨부문서가 아니라 해당 허가사항 본문의 다운로드 링크에서 조회했습니다. 실제 원문 저장소에는 아직 보존하지 않았습니다. 현재 허가상태의 명시적 판정·증빙 방식은 적재 전에 보완합니다.

[사전 확인 결과와 실측 요약](../../validation/rag/issue-591/novasc-label-precheck.md)에 크기·해시·본문 대조 방법과 한계를 기록했습니다.

### 미완료: 저장 환경 확정, 실제 적재와 인계

- Source Snapshot 생성·DB commit·재조회·동일 입력 재실행은 아직 수행하지 않았습니다.
- 공식 XML 경로의 이용·보존 조건과 제한 수집 계약은 확인·정렬이 필요합니다.
- 현우님이 접근할 DB·원문 저장 위치와 읽기 형식은 아직 확정하지 않았습니다.
- Source 적재와 Retrieval·Citation 사용 승인, 운영 공개는 각각 별도 상태로 기록합니다.

## 4. 누가 어디까지 맡는가

| 담당 | 이번 작업의 역할 |
| --- | --- |
| 김지혜 | 공식 원문 수집, 제품 식별·품질 검증, Source 실제 적재, DB·원문 재조회, 재실행 검증과 인계 |
| 정현우 | 인계 형식 협의, Chunk 생성·검증, 검색용 Index 구성, Guide/Chat 연결 |
| 송은영께 검토 요청 | 저장 환경·권한, 제한 수집 등록·증빙, Snapshot 버전 및 트랜잭션 경계 |

은영님께 전체 수집 구현을 요청하는 것이 아니라, 제가 구현할 저장 경계와 사용할 환경을 맞추려는 요청입니다. PR의 단일 책임 리뷰어는 은영님으로 제안하며, 현우님의 의견은 소비 인터페이스 검토 근거로 연결합니다.

## 5. 제안하는 저장 방식

### 기존 Source 구조를 재사용한다

원문은 접근 통제된 파일/객체 저장소에 보존하고, DB에는 원문을 찾고 검증하는 정보를 저장하는 방향입니다.

| 용어 | 이 작업에서의 의미 |
| --- | --- |
| Snapshot | 특정 시점에 검증한 자료 묶음과 그 버전 |
| artifact | 실제 원문 파일의 저장 참조·크기·checksum |
| member | 해당 Snapshot에 어떤 원문이 포함되는지 연결하는 항목 |
| locator | Snapshot 안에서 특정 본문을 가리키는 위치 정보 |
| corpus | 이번 시연에 선정한 제품들의 근거 자료 모음 |

현우님은 **Snapshot ID→member→artifact**를 따라 저장된 원문을 읽고, checksum으로 내용이 맞는지 확인한 뒤 Chunk를 만들 수 있도록 연결하려고 합니다. 웹의 최신 본문을 매번 다시 가져오는 방식과 구분합니다.

현재 `RawArtifactStore`는 쓰기 포트이고 로컬 파일의 검증 읽기 함수는 존재합니다. DB 참조에서 원문 읽기까지 이어주는 제한 reader/명령은 이번에 연결해야 합니다. 기존 구조가 있다는 이유로 인계 기능도 이미 완성됐다고 보지 않습니다.

### 제품 확대 시에는 누적 자료 묶음의 새 Snapshot을 제안한다

기존 코드는 최신 Snapshot을 `operation_id` 단위로 비교하며, CURRENT Snapshot도 Operation당 하나입니다. **같은 Operation에 제품별 Snapshot을 하나씩 독립 저장하면 서로 다른 제품이 이전·다음 버전으로 연결될 수 있습니다.**

이를 피하고 기존 구조를 재사용하기 위해 다음 안을 제안합니다.

| 설명용 버전 | 포함 제품 |
| --- | --- |
| v1 — 첫 제품 PR | 노바스크 |
| v2 — 첫 PR 병합 후 확장 | 노바스크 + 새로 확보한 다이아벡스·리피토 |
| 이후 | 기존 적격 제품 + 추가로 검증한 제품 |

v1/v2는 설명용 표기이며 실제 source_version 형식은 별도 정렬합니다. 이전 Snapshot은 수정하지 않습니다. 기존 원문 객체는 checksum으로 재사용할 수 있지만, 새 Snapshot의 Run·Artifact·member 연결을 다시 검증합니다.

이것은 **검토할 제안이며 확정된 계약이 아닙니다.** 제품별 독립 Snapshot이 필요하다면 제품별 현재성 범위를 어떻게 구분할지 먼저 맞춰야 합니다.

### 실패 제품은 실행 중 조용히 제외하지 않는다

- 전체 후보 17개와 이번 Snapshot에 넣기로 한 정확한 제품 집합을 구분합니다. 첫 집합은 `{200610660}`입니다.
- 이번 집합의 제품 식별과 필수 본문 3종이 모두 통과해야 성공 Snapshot을 만듭니다.
- 선택한 제품에 누락·충돌이 있으면 그 실행을 실패 처리합니다.
- 실패 후보를 빼고 진행하려면 대상 집합과 제외 사유를 기록한 별도 실행으로 처리합니다. 기존 성공 Snapshot은 보존합니다.
- 이전 원문을 재사용하면 최초 수집시각과 출처를 유지합니다. 재조회하지 않은 원문을 새로 수집한 것으로 기록하지 않습니다.

## 6. 저장 환경 확인 결과와 은영님께 요청할 정보

로컬 `postgres` 컨테이너는 실행 중이며, `five_pills`에 Source 관련 테이블 9개가 있습니다. 다만 migration은 `166f50617283`으로 확인됐고, 현재 develop에 필요한 컬럼·권한까지 준비됐는지는 검증하지 않았습니다. 기존 DB의 schema·데이터는 변경하지 않았습니다.

확인한 작업 폴더의 `.env`/`envs/.local.env`에서는 이번 적재 설정을 발견하지 못했습니다. 팀 공용 환경의 존재 여부는 아직 확인되지 않았습니다.

**권장안:** 팀 공용 DB·비공개 저장소가 준비돼 있으면 지정받은 환경을 사용합니다. 없다면 기존 서비스 DB와 분리한 전용 로컬 DB와 LOCAL_PRIVATE에서 먼저 검증하고, 현우님이 접근할 공동 환경으로 인계합니다. 로컬에서만 읽히는 상태는 최종 인계 완료로 처리하지 않습니다.

은영님께 다음 정보를 요청드립니다. 비밀번호·키는 문서에 기재하지 않고 비공개 설정 전달 경로로 받겠습니다.

1. 실제 적재할 DB와 migration 적용 담당.
2. Source 쓰기 계정, 현우님 읽기 계정의 권한·접속 경로.
3. 원문을 보존할 LOCAL_PRIVATE 공유 경로 또는 S3_PRIVATE 설정의 전달 위치.
4. 신규 제한 수집 등록·증빙·원문 보존에 필요한 검토 경계와 책임 리뷰어 지정.

기존 서비스 DB와 분리한 일회용 PostgreSQL과 테스트 전용 LOCAL_PRIVATE 디렉터리에서 합성 검증을 수행했습니다. 테스트 후 삭제하며, 실제 인계용 환경을 생성·확정한 것은 아닙니다. LOCAL_PRIVATE의 기존 권한 통제(디렉터리 0700·파일 0600·symlink 거부)는 유지합니다. 다른 컴퓨터에 로컬 절대 경로만 전달하는 것은 인계로 보지 않습니다.

## 7. 은영님·현우님께 검토 요청하는 결정

| 검토 항목 | 제안 | 확인 대상 |
| --- | --- | --- |
| 적재 환경 | 공용 환경 우선, 없으면 분리된 로컬 검증 후 공동 환경 인계 | 은영님 |
| 자료 묶음 단위 | 첫 제품부터 누적 corpus의 새 Snapshot 생성 | 은영님·현우님 |
| 수집 계약 | 기존 제품 목록 API와 구분한 공식 XML 제한 수집 등록·증빙 | 은영님·현우님 |
| 버전 | 기존 수집시각·canonical checksum 기반 형식의 적용 범위 확인 | 은영님·현우님 |
| 저장 원자성 | Snapshot·Run·Artifact·member까지 하나의 DB transaction으로 저장 | 은영님 |
| 인계 | 특정 Snapshot과 본문별 member로 원문을 검증해 읽는 방식 | 현우님 |
| 리뷰 | 단일 책임 리뷰어 은영님, 소비 인터페이스 의견은 현우님 근거 첨부 | 은영님 |

AGENTS.md의 공유 계약 변경 전 담당자 조율 원칙에 따라 실제 달라지는 경계를 검토하는 단계입니다. 기존 합의를 모두 다시 승인받으려는 요청은 아닙니다. 합의 후 필요한 Decision·계약 문서·인덱스와 구현·테스트를 같은 첫 제품 PR에 반영합니다.

## 8. 첫 PR까지의 진행·완료 기준

1. 노바스크 제품 식별·본문 확보 경로 확인 — 완료. 허가상태의 명시적 검증은 적재 전 보완.
2. 은영님이 적재 환경·권한·artifact 저장소·인계 기준을 정리하는 동안 로컬 검증 진행 — 현재 단계.
3. 실제 원문 보존·Snapshot 적재→commit→새 session에서 DB·원문 조회→동일 입력 재실행 검증.
4. 현우님이 전달한 참조로 원문과 출처를 읽을 수 있는지 확인.
5. 노바스크 1개 구현·검증·인계 결과를 PR로 올려 리뷰·병합.
6. 병합 후 나머지 16개 후보를 후속 확장. 이슈 종료·분리 범위는 은영님이 업데이트할 #591 완료 기준에 맞춘다.

공식 XML의 이용·보존 조건은 해당 경로 기준으로 확인합니다. 기존 공공데이터 API 라이선스를 자동 적용하지 않습니다. Source 적재 완료는 실제 Index 편입·Guide/Chat 답변·환자 공개 승인 완료가 아닙니다.

---

# 부록: 구현 검토를 위한 세부안

아래 규칙은 앞의 제안을 구체화한 협의 초안입니다. 승인되거나 이미 구현된 계약으로 취급하지 않습니다. **비즈니스 로직과 데이터 무결성은 Python Service/Repository에서 관리하며 신규 RLS·DB Trigger·업무 DB 함수는 추가하지 않습니다.**

## A. 제한 수집·문서·버전 규칙

### 원문·식별

- 제품 상세의 정식 허가명·ITEM_SEQ·업체·성분·함량·제형·허가상태를 명시적으로 검증한다. 허가일만으로 현재 허가상태를 추정하지 않는다.
- 공식 상세 HTML과 세 XML 원본을 비공개 artifact로 보존하는 안을 제안한다. 상세 HTML은 식별 및 다운로드 링크 출처 증빙이며 RAG 의료 본문은 EE/UD/NB다.
- 대상 host는 `nedrug.mfds.go.kr`, 제품 상세에서 확인된 경로만 사용한다. 임의 URL·외부 redirect·XML 외부 entity/DTD를 허용하지 않는다.
- XML DOC type/title과 기대 EE/UD/NB를 검증한다. PARAGRAPH뿐 아니라 ARTICLE title, 순서, 표, 소제목, 조건·예외를 보존한다.
- 응답의 실제 Content-Type은 `application/download; UTF-8; charset=UTF-8`이었다. JSON Decoder에 넣거나 `application/xml`로 관측값을 바꾸지 않는다. 허용 응답형·XML 검증·크기/시간 제한을 해당 수집 계약에 명시한다.
- XML에 제품 ID가 직접 없을 수 있으므로 제품 상세의 링크→요청 경로→응답 artifact→manifest의 ITEM_SEQ 결속을 검증한다.

### 문서·버전·checksum

- 문서 논리 ID 후보: `mfds-label:200610660:EE` (UD/NB 동일 규칙). 버전은 별도 Source version으로 결속한다.
- member locator 후보: `mfds-label/200610660/EE`; 외부 URL·저장소 object key와 구분한다. locator만으로 최신 웹을 다시 읽지 않는다.
- raw checksum: 받은 바이트 그대로 SHA-256. XML 재직렬화 결과를 raw로 표기하지 않는다.
- canonical checksum: 정렬된 selected 제품 집합, 검증된 제품 식별값, 순서가 보존된 EE/UD/NB 구조와 canonicalization version을 포함하는 결정적 manifest를 기준으로 정의한다. 수집시각·실행 ID·웹 장식 요소는 내용 동일성 hash에서 제외한다. 정확한 정규화 규칙은 계약·합성 테스트로 고정한다.
- 외부 불변 문서 버전/Last-Modified는 현재 확인되지 않았다. 허가일·URL을 외부 불변 버전으로 대입하지 않는다.
- 기존 `build_api_source_version(collected_at, canonical_checksum)` 형식을 공식 XML 다운로드에도 적용하는 안을 협의한다. 외부 version은 null이며 INTERNAL fixture로 위장하지 않는다.
- 동일 내용·동일 계약은 기존 NO_CHANGE 판정을 사용한다. 동일 source_version에 다른 canonical 내용은 SOURCE_VERSION_CONFLICT로 차단한다. 장식만 다른 원본도 새 수집 시도의 raw 증빙은 보존한다.
- 기존 전체 수집 Endpoint Receipt를 재사용하지 않는다. 신규 Source/Endpoint/Operation 코드, 제한 수집 검증 증빙과 receipt hash 규칙은 승인 전 확정값처럼 등록하지 않는다.

## B. 저장 트랜잭션·재실행 검증

1. 지정된 제품 집합·원문·식별·receipt를 검증하고 불변 원문 저장을 완료한다.
2. 애플리케이션 Service에서 transaction을 시작하고 Operation 잠금·Source 정책·동일 version 충돌을 확인한다.
3. 기존 함수로 PENDING Snapshot·Run·Artifact를 저장하고, 해당 Run에 귀속된 원문 artifact ID를 조회해 ARTIFACT member를 추가한다.
4. member의 content_sha256은 연결한 원문 artifact의 raw checksum과 일치하도록 Service에서 검증한다. 일반 DB 제약만으로 보장된다고 가정하지 않는다.
5. member까지 성공한 뒤 caller가 commit한다. 중간 오류는 rollback하고 성공 인계 파일을 만들지 않는다. DB 실패 후 남은 불변 객체를 임의 삭제하지 않는다.
6. 새 DB session에서 Snapshot→member→artifact를 조회하고 지정 저장소에서 실제 원문 크기·SHA-256을 재검증한다. 다른 Snapshot의 artifact·경로탈출·변조·누락은 고정 오류로 차단한다.
7. NO_CHANGE 재실행은 기존 Snapshot/member를 검증해 재사용한다. 이미 봉인된 Snapshot에 member를 추가하지 않는다. 기존 member가 누락되었다면 멱등 성공으로 숨기지 않는다.

실패·동시성·NO_CHANGE 테스트는 합성 데이터로 수행한다. 실제 데이터 재실행 결과는 별도 남긴다. 신규 RLS·Trigger·Stored Procedure·업무 DB 함수는 추가하지 않는다.

## C. 인계 필드와 읽기 경계

새 테이블이나 공개 API가 아니라 기존 참조를 묶은 비공개 manifest/읽기 명령을 우선 제안한다. 실제 UUID·checksum은 commit과 재조회 성공 후 채운다. 예시 ID를 실제 적재 결과로 사용하지 않는다.

| 단위 | 전달 필드 |
| --- | --- |
| corpus | selected ITEM_SEQ 집합, source_code/endpoint_code/operation_code, source_snapshot_id, source_version, canonical_checksum, canonicalization version |
| 제품 | ITEM_SEQ, 정식 허가명, 업체, 성분·함량·제형, 허가상태 검증 근거 |
| EE/UD/NB 각각 | external_document_id, source_snapshot_member_id, ingestion_artifact_id, logical locator, 공개 출처 URL, raw checksum·크기·실제 Content-Type, 수집시각 |
| 실행 | 기준 commit, ingestion_run_id, 실제 적재·재조회·동일 입력 재실행 결과, 제외·충돌 사유 |
| 승인 | 적재 상태·Snapshot verification_status, 수집/보존·Retrieval·Citation 각각의 승인 상태와 근거 |

`KnowledgeChunkIdentity`의 source_snapshot_id/source_snapshot_member_id/source_code/source_version/canonical_checksum/external_document_id/locator와 맞춘다. chunk_index·knowledge_chunk_id·content_hash·embedding은 현우님이 실제 Chunk 생성 결과로 채운다. raw checksum을 Chunk content_hash로 대신 넣지 않는다.

읽기 입력은 명시적 Snapshot ID와 member ID로 고정한다. DB 결속 및 artifact 크기/hash 확인 후 원문 바이트와 출처를 반환한다. 검토용 PENDING 원문 읽기와 승인된 Retrieval/Index 사용을 분리한다. 기존 Index가 PENDING을 거부하면 우회하지 않으며, 현우님은 합성 입력으로 준비하고 실제 Index 편입은 승인 경계에 맞춘다.

## D. 확인한 기존 계약·구현

- [Source 수집·활성화 목표 계약](../../contracts/targets/post-mvp-1/rag-source-ingestion-v1.md)
- [기존 수집 Operation 등록](../../../ai_worker/tasks/rag/source_client/endpoints.py)
- [Source 저장·버전 비교 흐름](../../../ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py)
- [Snapshot DB 저장·member 연결](../../../ai_worker/adapters/sqlalchemy_source_snapshot_repository.py)
- [Operation별 CURRENT 제약](../../../backend/app/models/rag_source.py)
- [로컬 원문 저장소](../../../ai_worker/adapters/local_private_source_artifact_store.py)
- [원문 저장 포트·검증 읽기](../../../ai_worker/tasks/rag/source_ingestion/artifacts.py)
- [Source version 생성·검증](../../../ai_worker/tasks/rag/source_ingestion/source_version.py)
- [Knowledge Chunk 식별 구조](../../../ai_worker/tasks/rag/knowledge_evidence_index.py)

새로운 공개 API·테이블·migration을 먼저 추가하지 않고 기존 구조로 가능한 범위를 우선 구현합니다. 원문·인증정보·민감 저장 위치는 GitHub나 일반 로그에 남기지 않습니다.
