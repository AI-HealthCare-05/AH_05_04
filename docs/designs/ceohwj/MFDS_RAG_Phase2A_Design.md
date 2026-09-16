# MFDS RAG Phase 2A Source → Knowledge materialization 설계

- 상태: **Revision 11 — Task 1 승인 가능 / Task 2a 착수 가능 / Task 2b·3·4 보류**
- 작성일: 2026-09-16 (Asia/Seoul), Revision 9 갱신 2026-09-16
- 범위: MFDS Source 참조와 기존 reader·parser를 재사용한 `KnowledgeDocument` / `KnowledgeChunk` 생성·저장
- 비범위: embedding, Index 생성, 검색·RRF·답변 생성, 실제 서버 적재, migration, 환경·권한·데이터 변경
- 구현 Issue: `#634`
- 관련 작업: `Related/Refs #591, #178`; server handoff `#593, #613`
- 구현 담당: 정현우 (`@ceohwj`)
- 단일 책임 reviewer: 송은영 (`@phina-io`) — Backend·DB·Security, Source lifecycle,
  transaction·권한·Index 호환
- specialist evidence: 김지혜 (`@Jye-rookie`) — Worker, parser, private artifact reader 경계

> 이 문서는 구현 지시서가 아니라 Phase 2A 구현 전에 합의할 설계 기준이다. 특히 아래의
> “보류 접점”은 현재 계약으로 추정하지 않는다. 보류 접점과 무관한 순수 변환·저장 설계와
> 로컬 합성 검증은 독립적으로 진행할 수 있다.

## 1. 확인 근거와 현재 상태

> 이 절의 모든 값은 **Revision 11 실행 시점(2026-09-16T09:08Z)에 실제로 조회한 결과**다. 과거 SHA·과거 PR 목록·다른
> branch의 working tree 상태를 현재 증거로 남기지 않는다.

### 1.1 실제로 읽은 인계·로드맵 문서

저장소 밖의 로컬 전용 artifact이므로 개인 절대 경로 대신 파일명으로만 기록한다. 두 파일 모두 실행자의 로컬
`Downloads` 디렉터리에 존재했고 전문을 열어 확인했다.

- `MFDS_RAG_Agent_Handoff_Final.md` (25,704 bytes) — §0~§5 공통 지침과 §6 설계 전용 지시를 확인했다.
  기준 SHA로 기재된 `develop@3a8cc79e`는 문서 작성 시점 값이며 현재 HEAD가 아님을 문서 스스로 명시한다.
- `MFDS_17products_Codex_RAG_execution_prompt_with_issues.md` (53,141 bytes) — §0 Issue 연결 지도와 단계별 주
  이슈·경계를 확인했다. 기재된 `develop@1ebac025` 역시 문서 작성 시점 값이다.

두 문서의 명령·범위 지시는 조사·설계 입력이며, 저장소 규칙(`AGENTS.md`, `CONTRIBUTING.md`, `docs/contracts/`)과
현재 코드·Issue 상태보다 상위의 런타임 사실로 간주하지 않았다.

함께 읽은 저장소 지침: `AGENTS.md`(루트, 중첩 `AGENTS.md`는 없음), `CONTRIBUTING.md`, `SECURITY.md`,
`docs/privacy-safety.md`, `docs/testing.md`, `docs/contracts/README.md`,
`docs/governance/post-mvp-1-document-authority.md`.

### 1.2 Git 기준점과 working tree (Revision 12 실측)

> `develop`은 이 작업 중 계속 전진했다 (`dec6076b` → `91b6062d` → `c9474386` → `9334ebbc` → `2f003eac` → `efeeb22e` → `1cbb229b`).
> 아래 값은 **2026-09-16 Revision 12 라운드에서 `git fetch` 후 실측한 값**이며, 읽는 시점에 이미 뒤처져 있을 수 있다.
> 기준 SHA를 인용할 때는 항상 확인 시각을 함께 남긴다.

| 항목 | 값 (Revision 12 실측) |
|---|---|
| Worktree | `<repo>/.worktrees/feat-634` |
| Branch | `feat/634-mfds-knowledge-materialization` |
| HEAD | `1cbb229b704ae554e5e079d0dfb75951eb52f5a0` (= `origin/develop`) |
| `origin/develop` | `1cbb229b704ae554e5e079d0dfb75951eb52f5a0` (`git rev-list --left-right --count HEAD...origin/develop` → `0\t0`) |
| 원격 push 여부 | 이 branch는 `origin`에 push되지 않았다 |

Revision 11에서 반영한 upstream: `896ec59e`(#639 Track C 지원 분기), `a437a7d4`(#646 Wireframe),
`2f003eac`(**#649 `#591` 노바스크 실제 적재·멱등성 검증 기록 — 병합됨**), `4b7ab80f`(#653),
`dac51c29`(#652 withdrawal gate regression), `efeeb22e`(#658 withdrawal retention matrix).

Revision 12에서 추가 반영한 upstream 2건: `edfa23e1`(#193 Local 7일 합성 Safety 데모 정책),
`1cbb229b`(#651 Web Push Production 활성화 게이트). 두 commit 모두 `ai_worker/` RAG 코드는 건드리지
않았으나 `#193`이 `docs/testing.md`와 `docs/contracts/README.md`를 수정해 **`docs/testing.md`에서 세 번째
텍스트 충돌**이 발생했다 (Plan finding 41).

병합 방식: `docs/contracts/README.md`·`docs/testing.md`는 3-way 재적용으로 충돌 없이 병합됐다. Revision 10에서
예측했던 `#639`의 `docs/testing.md` tail 충돌은 `#639`가 해당 절을 추가하지 않은 형태로 병합되어 실제로는
발생하지 않았다. `ai_worker/**` 두 파일은 upstream 무변경, byte-identical 보존.

working tree 상태 (모두 보존, commit 없음):

```text
 M ai_worker/tasks/rag/source_ingestion/mfds_label.py
 M ai_worker/tests/rag/source_ingestion/test_mfds_label.py
 M docs/contracts/README.md
 M docs/testing.md
?? docs/contracts/proposed/post-mvp-1/knowledge-materialization-v1.md
?? docs/designs/ceohwj/MFDS_RAG_Phase2A_Design.md
?? docs/designs/ceohwj/MFDS_RAG_Phase2A_Plan.md
```

### 1.3 관련 Issue 확인 결과

`#634`는 **OPEN**, assignee `@ceohwj`, label `type: feature`. 담당·리뷰어 기록은 `AGENTS.md` 역할 baseline과
일치한다 (구현 `@ceohwj`, 단일 책임 리뷰어 `@phina-io`, specialist evidence `@Jye-rookie`).

Issue 코멘트 1건 — `@phina-io` (2026-09-16T04:02:56Z). 범위 동의 + 7개 유지 조건. 반영 위치:

| `@phina-io` 요청 | 반영 위치 |
|---|---|
| Source member·raw artifact provenance를 짧은 transaction 안에서 재검증 | 계약 "저장 및 트랜잭션 경계" 4절 |
| `document_content_hash == SnapshotMember.content_sha256` | 계약 6대 hash domain #5, §5.2 정규화 영향 표 |
| `content_hash == sha256(chunk_text UTF-8)` | 계약 "Byte-exact 렌더링 규약" + golden vector G1~G8 |
| 같은 입력은 byte-identical chunk와 동일 receipt | golden vector 표 + 계약 "Receipt 동일성 비교 규칙" |
| 하나라도 다르면 쓰기 없이 fail-closed | 계약 "실행 순서" 1~7단계 |
| raw text·private object path·credential 비노출 | 계약 "공개 오류 계약 (reason-only)" |
| NN 공식 빈 ARTICLE 명시적 보류 | 계약 "미확정 접점" 표 |

다른 Issue는 기존 판단 유지. 구현 PR은 `Part of #634`로 연결하고 `Part of #178` / `Closes #178`로 쓰지 않는다.

### 1.4 열린 PR·원격 브랜치 확인 결과 (Revision 12 실측, 저장소 전체 조회)

> 열린 PR 목록은 이 작업 중 세 차례 바뀌었다. Revision 9~10 보고서에 적었던 `#629`, `#636`, `#640`,
> `#639`, `#646`, `#649`는 모두 이후 **MERGED**됐다. 아래가 현재 값이다.

Phase 2A 구현 PR은 **없다.** `gh pr list --state open` 저장소 전체 기준 열린 PR 4건이며, `#634` 변경 파일과 겹치는 것은 `#650` 하나뿐이다:

| PR | branch | 작성자 | `#634` 변경 파일과의 겹침 |
|---|---|---|---|
| `#650` | `feat/180-endpoint-member-contract` | `@ceohwj` | `docs/contracts/README.md` |

나머지 3건 `#660`(frontend account) · `#655`(frontend chat/guide) · `#654`(frontend track-c)는 `frontend/` 및 `docs/validation/` 파일만 건드려 겹침이 없다. Revision 11 이후 `edfa23e1`(#193) · `1cbb229b`(#651) 2건이 병합됐고, 그중 #193이 `docs/testing.md`에서 **다시 텍스트 충돌**을 일으켜 두 절 보존으로 해소했다 (Plan finding 41).

`#650`의 README 변경은 `#634`의 Proposed 구역 1행과 다른 위치이므로 텍스트 충돌 가능성은 낮다.
`git ls-remote --heads origin` 기준 materialization/chunk 전용 미병합 원격 브랜치는 없다.

#### 1.4.1 `#591` 실제 적재 증거 — 병합됨 (Phase 2A 전제 갱신)

`#649`가 `2f003eac`로 병합되어 `docs/validation/rag/issue-591/novasc-first-ingestion-record.md`가
**develop의 기록**이 됐다. Revision 10에서는 "open PR"로 다뤘으나 이제는 병합된 증거다.

| 확인된 사실 (병합된 기록 기준) | Phase 2A 영향 |
|---|---|
| Source `MFDS_PRODUCT_LABEL` **ACTIVE**, Endpoint `MFDS_NEDRUG_LABEL_XML` **VERIFIED/ENABLED/APPROVED**, Operation `COLLECT_NOVASC_200610660_LABEL_XML` **ENABLED/APPROVED** | Phase 2A lifecycle gate의 Source·Endpoint·Operation 조건이 실제 환경에서 **충족 가능**함이 확인됐다 |
| Snapshot `073ee706-d039-49b5-8ca5-e92cd021f087`, `member_count=3` (EE·UD·NB), canonical checksum `a5df7649...39cd3` | 실제 Source 좌표가 존재한다. 다만 `member_ids`로 쓸 개별 Member/Artifact UUID는 아직 인계되지 않았다 |
| **최초 실행 `decision=CREATED`, 동일 입력 재실행 `decision=NO_CHANGE`**, Snapshot ID·member_count·checksum·source_version 모두 일치 | `NO_CHANGE` run은 가설이 아니라 재적재마다 실제로 생성된다. §7.3의 `NO_CHANGE` 거절 판단을 실측으로 뒷받침한다 (아래 분석) |
| `post_commit_requery_passed=true` — `run_ingestion`이 commit 이후 새 session에서 `requery_mfds_label_persistence`를 호출 | **기존 ingestion 경로에는 post-commit 재조회가 실제로 존재한다.** 단 그것은 rollback 불가능한 사후 확인이며, Phase 2A의 receipt 검증(commit 전 transaction 내)과 같은 것으로 취급하지 않는다. 두 개념을 구분해 기술한다 |
| 출력에 `verification_status`가 없어 CURRENT를 추정하지 않는다고 명시. 미완 항목에 "개별 Member/Artifact ID·버전 필드·verification_status·검증 증빙 재조회 및 인계" | Phase 2A gate의 `verification_status == "CURRENT"` 요구는 **실측 미확인**이다 |
| 미완 항목에 "**content type/encoding**·권한 증빙을 제한된 채널로 전달" | 수집 시점 관측 `content_type`은 `novasc-label-probe-summary.json` 계열 기록으로 **공개 확인**되며, 미확인인 것은 **저장된 `IngestionArtifact.content_type` 컬럼 값**과의 대조다 (§13.1). Revision 11이 이를 "미인계"로 적은 것은 오류다 |
| 미완 항목에 "**현우님 consumer acceptance**: Snapshot→Member→Artifact 조회, EE/UD/NB 크기·hash 및 source_version 대조, 원문 read-only 접근, 후속 provenance 적합성 확인" | **구현 담당(`@ceohwj`)에게 배정된 미완 작업**이며 Phase 2A actual persistence의 선행 조건이다 |
| NN과 나머지 16개 제품은 범위 제외 | `{NN}` 단독 요청 경로는 실제 입력이 아직 없다 |

`NO_CHANGE` 분석: 재적재 시 `NO_CHANGE` run도 artifact를 생성하지만, Snapshot member의
`ingestion_artifact_id`는 여전히 최초 `CREATED`(=`SUCCEEDED`) run의 artifact를 가리킨다. Phase 2A gate가
`member.ingestion_artifact_id == artifact.id` 및 `artifact.ingestion_run_id == run.id`를 함께 요구하므로,
member를 통해 도달하는 run은 항상 최초 `SUCCEEDED` run이다. 따라서 `NO_CHANGE`를 거절해도 정상 경로가
막히지 않는다.

### 1.5 Task 상태 (Revision 12 기준)

| Task | 상태 | 근거 |
|---|---|---|
| Task 0 — 공유 계약 문서화·Index/Testing 갱신 | **완료** | 계약 문서 + `docs/contracts/README.md` Proposed index 1행 + `docs/testing.md` `#634` 절. working tree 변경으로만 존재하며 commit·PR 없음 |
| Task 1 — Parser seam 추출·회귀 고정 | **구현 완료 · 리뷰 승인 가능 판정 수령 · Revision 11에서 재검증** | `pytest ai_worker/tests/rag/source_ingestion/test_mfds_label.py -q` → 27 passed |
| Task 2a — 순수 renderer/chunker (golden vector) | **구현 완료 · Revision 11에서 신규 작성** | `ai_worker/tasks/rag/mfds_label_chunk_policy.py` + `ai_worker/tests/rag/test_mfds_label_chunk_policy.py`. `pytest ai_worker/tests/rag/test_mfds_label_chunk_policy.py -q` → **31 passed**. golden vector G1~G8(10개 parametrized 행)의 문자열·hash가 계약 표와 일치하고, 선언된 hash가 기대 문자열의 UTF-8 bytes에서 나온 값임을 구현과 무관하게 독립 계산으로 확인. Revision 12에서 인라인 table fail-closed 회귀 4건 추가 (Plan finding 42) |
| Task 2b — materialization 커널 + loader/오류 변환 | **보류 — reader typed boundary 결정 대기 (BLOCKED)** | `ai_worker/tasks/rag/knowledge_materialization.py` 미존재 |
| Task 3 — PostgreSQL Repository 어댑터 | **미착수 — cross-flow lock 전략 확정 선행 필요** | `ai_worker/adapters/sqlalchemy_knowledge_materialization.py` 미존재 |
| Task 4 — 격리 PostgreSQL 통합 테스트 | **미착수** | `tests/integration/rag/test_knowledge_materialization_postgresql.py` 미존재 |

Revision 12 전체 검증 (develop `1cbb229b` 기준, 전부 **로컬 실행** 결과이며 GitHub CI 결과가 아니다):
`run_with_worker_test_environment pytest ai_worker/tests/rag -q` → **1969 passed**,
`ruff check` (4개 파일) → All checks passed!, `ruff format --check` (4개 파일) → 4 files already formatted,
`uv run mypy backend/app ai_worker` → **Success: no issues found in 762 source files**, `git diff --check` 출력 없음.

### 1.6 서버 권한이 필요한 검증 vs 로컬 합성 검증

| 구분 | 내용 |
|---|---|
| 로컬 합성으로 가능 | parser seam 회귀, 순수 chunking/정규화 커널, mock session 기반 Repository 쿼리·lock 순서 검증, 격리 disposable PostgreSQL에서의 transaction·동시성·rollback·권한 분리 검증 |
| 서버 권한·실제 인계 필요 | 실제 MFDS Snapshot commit과 member 좌표, private artifact mount에서의 실제 read, 공용 환경의 `knowledge_index_builder` credential provision, 실제 Document/Chunk 저장, embedding·Index·Retrieval·Evaluation |

로컬 구현 완료는 서버 실행 승인이나 Source 인계 완료가 아니다. `#591`/`#593`/`#613` gate가 충족되기 전에는
actual server persistence를 열지 않는다.

## 2. 설계 목표와 경계

### 2.1 목표

명시적으로 선택된 MFDS Source Snapshot member의 raw artifact를 기존 보안 reader로 읽고 기존
MFDS label parser로 구조화한 뒤, 결정적인 chunk policy를 적용하여 다음 행을 저장한다.

1. Source member 하나당 `KnowledgeDocument` 하나
2. 해당 문서의 의미 단위마다 순서가 안정적인 `KnowledgeChunk`
3. 기존 Knowledge Index adapter가 추가 변환 없이 검증할 수 있는 provenance와 hash
4. 동일 요청의 재실행은 같은 행·ID를 반환하고, 다른 내용은 덮어쓰지 않는 fail-closed 저장

### 2.2 비목표

- actual Source ingestion 또는 `#591` 완료 선언
- embedding 계산·외부 vector store 기록
- `RagKnowledgeIndex` / `RagKnowledgeIndexMember` 생성
- 검색·RRF·answer/citation 정책 변경
- Source/Endpoint/Operation lifecycle 변경
- 기존 migration, DB role policy, compose/env, 실제 artifact 데이터 변경
- OTC Source 또는 17개 전 제품의 준비 완료 추정
- 현재 Proposed 문서를 Current 계약으로 승격

### 2.3 단계 분리

```text
명시적 Snapshot/member 요청
        │
        ▼
참조 조회 ── raw artifact reader 검증 ── 기존 parser 재사용
        │                                  │
        └────────── 순수 MaterializationDraft ──────────┐
                                                        ▼
                                          짧은 DB transaction
                                 provenance 재검증 → exact replay/insert
                                                        │
                                                        ▼
                                    commit 전 같은 transaction에서 receipt 재구성·비교
```

Raw I/O와 parsing은 DB transaction 밖에서 수행한다. 저장 직전에 같은 provenance를 transaction
안에서 다시 확인하여 조회와 저장 사이의 상태 변경을 탐지한다.

## 3. 기존 구현 재사용과 변경 파일

### 3.1 그대로 재사용할 구현

| 기존 파일 / symbol | 사용 목적 |
|---|---|
| `ai_worker/tasks/rag/source_ingestion/mfds_label.py`의 parser·canonical helpers | XML/DTD/entity 처리, NFC/newline 정규화, EE/UD/NB/NN 구조 검증 |
| `MfdsLabelArtifactReader` | materializer가 특정 storage 구현에 결합되지 않는 reader protocol |
| `ai_worker/adapters/local_private_source_artifact_finalizer.py`의 `LocalPrivateSourceArtifactReader` | 실제 byte read, path escape/symlink/world-writable/read-only 검증, raw hash·size 검증 |
| `ai_worker/adapters/sqlalchemy_source_snapshot_repository.py`의 Snapshot/artifact/member receipt 의미 | Source 참조와 post-commit handoff 의미의 기준 |
| `backend/app/models/knowledge.py` | 기존 Document/Chunk 스키마와 unique/check 제약 |
| `ai_worker/tasks/rag/knowledge_evidence_index.py` | `KnowledgeChunkIdentity`, stable coordinate, exact UTF-8 chunk hash 기준 |
| `ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py` | Index가 요구하는 Source lifecycle와 hash/identity 조건 |
| `infra/python/knowledge_index_role_policy.py` | 기존 builder SELECT/INSERT 권한 경계 |

기존 private parser helper를 복사하거나 별도의 XML parser를 만들지 않는다.

### 3.2 구현 시 계획된 변경 파일과 symbol

Revision 9 기준으로 **이미 변경된 파일**(working tree, commit 없음)과 **아직 변경하지 않은 후보**를 구분한다.

이미 변경된 파일 (Task 0·1):

| 파일 | 변경된 symbol | 상태 |
|---|---|---|
| `ai_worker/tasks/rag/source_ingestion/mfds_label.py` | `ParsedMfdsLabelDocument`(`section`, `document_title`, `article_count`, `paragraph_count`, `nonempty_paragraph_count`, `content_status`, `empty_article_titles`, `root: field(repr=False)`), `parse_mfds_label_artifact()`, `inspect_xml()`, `_load_document()` | 구현·검증 완료 |
| `ai_worker/tests/rag/source_ingestion/test_mfds_label.py` | seam·repr·깊은 XML·`inspect_xml()` 무변경·raw 구조 보존 회귀 테스트 11건 추가 | 27 passed |
| `docs/contracts/proposed/post-mvp-1/knowledge-materialization-v1.md` | Proposed 계약 (신규, untracked) | Revision 9 반영 |
| `docs/designs/ceohwj/MFDS_RAG_Phase2A_Design.md` | 이 설계서 (신규, untracked) | Revision 9 반영 |
| `docs/designs/ceohwj/MFDS_RAG_Phase2A_Plan.md` | 계획서 (신규, untracked) | Revision 9 반영 |
| `docs/contracts/README.md` | Proposed 구역 index 1행 | 완료 |
| `docs/testing.md` | `#634` 검증 절 | 완료 (Task 2b~4는 예정으로 표기) |
| `ai_worker/tasks/rag/mfds_label_chunk_policy.py` (신규, Task 2a) | `CHUNK_POLICY_VERSION`, `ChunkPolicyFailureReason`, `ChunkPolicyError`, `KnowledgeChunkDraft`, `build_chunk_drafts()`, `chunk_content_hash()`, `normalize_chunk_text()` | 구현·검증 완료. reader·DB·receipt를 import하지 않는 순수 변환 계층 |
| `ai_worker/tests/rag/test_mfds_label_chunk_policy.py` (신규, Task 2a) | golden vector G1~G8(10개 parametrized 행) + 정규화·정책·결정성 + table 위치 규칙 테스트 31건 | 31 passed |

아직 변경하지 않은 후보 (Task 2~4, 착수 승인 대기):

| 파일 | 변경 / 신규 symbol | 책임 |
|---|---|---|
| `ai_worker/tasks/rag/knowledge_materialization.py` (신규, Task 2b) | `KnowledgeMaterializationRequest`, `MaterializationSourceDocument`, `KnowledgeDocumentDraft`, `KnowledgeMaterializationReceipt`, `KnowledgeMaterializationFailureReason`, `KnowledgeMaterializationError` | 입력 검증, draft 조립, repository orchestration. chunking은 Task 2a의 `mfds_label_chunk_policy`를 재사용하고 재구현하지 않는다 |
| `ai_worker/adapters/sqlalchemy_knowledge_materialization.py` (신규) | `SqlAlchemyKnowledgeMaterializationRepository` | Source 참조 조회, transaction 내 provenance 재검증, advisory lock, exact replay 또는 insert, receipt 재조회 |
| `ai_worker/admin/mfds_label_materializer.py` (신규, 서버 gate 이후) | standalone entrypoint/config | actual builder credential과 read-only artifact mount를 명시적으로 주입. 일반 AI worker runtime에 builder 권한을 넣지 않음 |
| `ai_worker/tests/rag/test_knowledge_materialization.py` (신규) | pure unit tests | chunking, hash, 입력/출력, fail-closed 동작 |
| `ai_worker/tests/rag/test_sqlalchemy_knowledge_materialization.py` (신규) | repository tests | query/result mapping, error sanitization, exact replay |
| `tests/integration/rag/test_knowledge_materialization_postgresql.py` (신규) | real PostgreSQL tests | transaction, concurrency, 제약, Index 호환 검증 |

### 3.3 변경하지 않을 파일

- `backend/app/models/knowledge.py`: 현재 필드와 제약으로 EE/UD/NB 기본 경로를 저장할 수 있다.
- `backend/alembic/versions/**`: migration 불필요.
- `infra/python/knowledge_index_role_policy.py`: 현재 builder role이 네 knowledge table의 SELECT/INSERT와
  Source read를 이미 가진다. 권한 확대를 설계하지 않는다.
- `.env*`, compose/deployment 파일, 실제 Source 데이터: 서버 gate가 해소되기 전 변경하지 않는다.
- 기존 Index/Search/Retrieval 구현: Phase 2A가 그 계약에 맞춘다.

## 4. 입력과 출력 계약

### 4.1 요청 입력

`KnowledgeMaterializationRequest`는 다음 값만 외부에서 받는다.

| 필드 | 타입 / 조건 | 의미 |
|---|---|---|
| `snapshot_id` | UUID | materialize할 정확한 Source Snapshot |
| `member_ids` | 중복 없는 UUID tuple | 요청 대상 Source Snapshot member들 |
| `expected_item_seq` | 정확히 9자리 문자열 | 서로 다른 품목의 member 혼합 방지 |
| `chunk_policy_version` | 고정 문자열 | 최초 구현은 `mfds-label-knowledge-chunk@1`만 허용 |

`source_code`, `source_version`, `canonical_checksum`, locator, artifact object key,
`external_document_id`, hash는 요청자가 신뢰 입력으로 제공하지 않는다. DB와 검증된 artifact에서
도출한다.

`member_ids`가 지시하는 section 집합은 정확히 다음 두 형태 중 하나만 허용한다.

1. 동일 `expected_item_seq` 품목의 정확한 `{EE, UD, NB}`
2. 동일 `expected_item_seq` 품목의 정확한 `{NN}` 단독

EE/UD/NB 누락, section 중복, 추가 section 결합(`{EE, UD, NB, NN}`), `EE+NN` 등 부분 혼합,
지원 밖 section, 서로 다른 품목 혼합은 모두 `REQUEST_INVALID`로 거절한다.

**판정 시점**: 이 판정은 `member_ids`만 보고 할 수 없다. section은 member row의 locator에서 나오므로
**read-only read model을 확보한 뒤**에 판정한다 (§7.1 2단계 → 3단계). 요청 단계(1단계)에서 검증할 수 있는 것은
UUID 형식·중복 UUID·`expected_item_seq` 형식·`chunk_policy_version` 지원 여부뿐이다. 다만 section 집합 판정은
lifecycle gate의 나머지 조건 검증과 artifact **읽기**보다는 앞서고, 실패 시 write transaction을 열지 않는다.

### 4.2 repository가 조회하는 Source 입력

`MaterializationSourceDocument`는 최소 다음 immutable 사실을 가진다.

- Source: `source_id`, `source_code`, lifecycle status
- Endpoint/Operation: verified/enabled/acquisition status
- Snapshot: `snapshot_id`, `source_version`, `raw_manifest_checksum`, `canonical_checksum`,
  parser/normalization/canonicalization versions, status
- Member: `member_id`, `member_kind`, `locator`, `content_sha256`, artifact binding
- IngestionRun: `ingestion_run_id`, `ingestion_run_status`
- Artifact receipt: `artifact_id`, `artifact_key`, `artifact_kind`, `page_number`,
  `storage_backend`, `reject_code`, `parser_location`, private object reference, byte size,
  content type, `raw_sha256`, persistence status

artifact identity 불변식은 다음과 같고, 하나라도 불만족 시 `SOURCE_BINDING_INVALID`로 거절한다
(상태 부적격은 `SOURCE_NOT_ELIGIBLE`).

- `ingestion_artifact_id == RagSourceIngestionArtifact.id`
- artifact가 요청된 ingestion run에 속함 (`ingestion_run_id` 일치)
- `ingestion_run_status == "SUCCEEDED"` (`SUCCEEDED_WITH_REJECTIONS`는 MFDS 경로가 생성하지 않으므로 미허용)
- `artifact_kind == "RAW_RESPONSE"`
- `artifact_key == f"{locator}.xml"`
- `object_key == LocalPrivateSourceArtifactStore.object_key_for_checksum(artifact.raw_checksum)`
  — **기존 공개 `@staticmethod`를 그대로 호출**한다 (새 helper를 만들지 않는다). 결과는
  `f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"`이며 **`raw_checksum`에서 도출**되고 locator가
  아니다. 검증 정규식은 `source_cleanup/orphan_artifact.py`의 `_OBJECT_KEY`와 같은 형태다.
  S3 backend(`S3PrivateSourceArtifactStore._object_key()`)는 `{prefix}/sha256/...`로 prefix에 의존하므로
  규칙이 backend마다 다르고, Phase 2A는 `LOCAL_PRIVATE`만 허용해 결정적이다. 이 검증은 DB row를 보는
  **Task 3 책임**이다
- `storage_backend == "LOCAL_PRIVATE"`
- `content_type == OBSERVED_CONTENT_TYPE` (`"application/download; UTF-8; charset=UTF-8"` exact match)
- `raw_checksum == member.content_sha256`
- artifact section과 Snapshot Member section이 정확히 일치
- `page_number == SECTION_ORDER.index(section) + 1` — `EE`=1, `UD`=2, `NB`=3, `NN`=4
- `reject_code is None`
- `parser_location is None`
- schema/parser/normalization/canonicalization version 결속이 pre-read와 locked read에서 동일
- 다른 품목 또는 다른 Snapshot Member의 artifact 재사용 거부

DTO의 `page_number`는 DB 스키마와 동일하게 `int | None`으로 선언한다
(`RagSourceIngestionArtifact.page_number`는 nullable이고 `REJECTS` artifact는 `NULL`이다).
`None`은 타입 수준에서 배제하지 않고 gate에서 fail-closed로 거절하며 negative test에 포함한다.

위 조건 중 네 가지(`storage_backend`, `artifact_key`, `raw_checksum`, `content_type`)는 기존
`_member_artifact_metadata()`가 이미 강제하므로 그 검증 의미를 **재사용**하고 중복 규칙을 새로 만들지 않는다.

`artifact_key`를 DTO에 포함함으로써 기존 `LocalPrivateSourceArtifactReader.read_verified()`에
필요한 `RawArtifactMetadata(artifact_key, raw_checksum, byte_size, content_type)`를 이 DTO만으로
완전히 구성할 수 있다. 요청자 입력이나 별도 조회로 보충하지 않는다.

Private object reference는 `repr=False`로 취급하고 로그·receipt·예외 메시지에 출력하지 않는다.
`artifact_key`는 member locator에서 도출되는 공개 좌표이므로 repr에 남긴다.

### 4.3 저장 출력

`KnowledgeMaterializationReceipt`는 raw text나 storage 경로 없이 다음을 반환한다.

- `snapshot_id`, `source_code`, `source_version`, `snapshot_canonical_checksum`
- policy/canonicalization version
- 문서별:
  - `source_snapshot_member_id`
  - `knowledge_document_id`
  - `external_document_id`
  - `document_content_hash`
  - `chunk_count`
  - 순서가 고정된 `(knowledge_chunk_id, chunk_index, content_hash)` tuple
- `created` 또는 `exact_replay`

Commit 뒤 새 session으로 receipt를 재조회하여 DB에 실제 저장된 identity와 hash가 반환값과
일치하는지 확인한다.

## 5. identity와 hash 의미

### 5.1 identity

- Document natural key: `(source_snapshot_member_id, external_document_id)`
- Chunk natural key: `(knowledge_document_id, chunk_index)`
- 기존 Index stable coordinate:
  `(source_code, source_version, external_document_id, chunk_index)`
- `knowledge_document_id`, `knowledge_chunk_id`는 DB UUID이며 재실행 시 기존 값을 반환한다.
- `external_document_id` 후보: `mfds-label:{ITEM_SEQ}:{EE|UD|NB|NN}`
- Source member locator 후보: `mfds-label/{ITEM_SEQ}/{EE|UD|NB|NN}`

마지막 두 좌표는 `#591` validation 문서의 “적재 전 좌표 후보”이며 Current 계약이 아니다.
형식 승인과 구현 Issue 기록 전에는 persistence를 활성화하지 않는다. 순수 parse/chunk draft는
승인과 독립적으로 구현·검증할 수 있다.

### 5.2 hash domain 분리

| 값 | 입력 byte / 의미 | 생성·검증 규칙 |
|---|---|---|
| Artifact `raw_sha256` | 저장된 원본 artifact의 정확한 raw bytes | 기존 reader가 read 시 재계산하고 artifact receipt와 비교 |
| Snapshot `raw_manifest_checksum` | Snapshot 전체 raw artifact metadata manifest | member 하나의 hash로 재계산하거나 대체하지 않음 |
| Snapshot `canonical_checksum` | 기존 MFDS parser가 만든 품목 전체 canonical structure manifest | 문서 hash나 chunk hash로 재사용하지 않음 |
| `SnapshotMember.content_sha256` | 해당 member raw bytes | `raw_sha256` 및 실제 read bytes SHA-256과 모두 같아야 함 |
| `KnowledgeDocument.document_content_hash` | Index가 Source member와 결합할 때 쓰는 hash | 현재 스키마/Index 계약에 맞춰 member `content_sha256`을 그대로 복사. 저장 전 실제 raw bytes 재검증 필수 |
| `KnowledgeChunk.content_hash` | 최종 저장 `chunk_text`의 정확한 UTF-8 bytes | `sha256(chunk_text.encode("utf-8")).hexdigest()` |

#### 정규화가 hash에 미치는 영향 (domain별로 다르다)

| 값 | 입력 domain | NFD 입력과 NFC 입력에서 |
|---|---|---|
| Artifact `raw_sha256` | 원문 XML raw bytes | **달라야 한다** |
| `SnapshotMember.content_sha256` | 동일 raw bytes | **달라야 한다** |
| `KnowledgeDocument.document_content_hash` | `member.content_sha256` 복사 | **달라야 한다** |
| Snapshot `raw_manifest_checksum` / `canonical_checksum` | Snapshot 전체 manifest (Phase 2A 비소유) | Phase 2A가 규정하지 않는다 |
| 정규화된 `chunk_text` | parsed text의 NFC projection | **같아야 한다** |
| `KnowledgeChunk.content_hash` | 위 `chunk_text`의 UTF-8 bytes | **같아야 한다** |

"정규화하면 hash가 같아진다"는 규칙은 chunk text projection domain에만 적용된다. 같은 품목의 NFD 판본과 NFC
판본은 서로 다른 Snapshot member이며, 동일 natural key로 들어오면 `document_content_hash`가 달라 exact replay가
아니라 `CONTENT_CONFLICT`다.

`document_content_hash`라는 이름을 canonical text hash로 재해석하지 않는다. 현재 Index adapter는
`document_content_hash == SnapshotMember.content_sha256`를 요구한다. 별도의 문서 canonical hash가
필요해지면 새 계약·필드·migration 문제이므로 Phase 2A에서 추정하지 않는다.

## 6. parser 재사용과 chunking policy

### 6.1 parser seam

현재 `load_mfds_label_plan()` 내부의 실제 parsing 경로를 public pure function으로 추출한다.

```python
parse_mfds_label_artifact(raw_bytes: bytes, section: str) -> ParsedMfdsLabelDocument
```

이 함수는 기존과 동일하게 다음을 보장한다.

- XML declaration/encoding, 허용 DTD/entity 처리
- element/attribute/text/tail/source order 보존
- EE/UD/NB 필수 구조와 NN의 정확한 7개 ARTICLE title 검증
- title-only ARTICLE을 parser 오류나 누락으로 바꾸지 않음

`load_mfds_label_plan()`도 이 public seam을 호출하게 하여 Source ingestion과 materialization이
서로 다른 parser 사본을 갖지 않게 한다.

#### 정규화 책임 경계 (Revision 9 정정)

Revision 8까지 이 절은 parser seam이 "NFC 및 newline 정규화"를 보장하는 것처럼 기술했다. 실제 구현은 그렇지
않다. `parse_mfds_label_artifact()`는 `_parse_xml()` + `_inspect_body()`만 수행하고 `ElementTree` root를
그대로 반환하며 `_canonical_text()`를 적용하지 않는다.

| 계층 | 책임 |
|---|---|
| Parser seam (`parse_mfds_label_artifact`) | XML 구문 분석 · section/title/body 구조 검증 · 구조 통계 추출 · parsed root 제공. **정규화 없음** |
| Task 2 renderer/materialization | text projection · newline 정규화 · Unicode NFC 정규화 · canonical document text 생성 · 해당 canonical text 기반 hash 생성 |

- `_canonical_text()`(NFC + newline)는 `_canonical_element()` 안에서만 쓰이며 Snapshot `canonical_checksum`
  산출용 **구조** canonicalization이다. chunk text projection과 같은 경계가 아니다.
- 하위 Index 계층의 `_bounded_nfc()`는 identity 문자열이 **이미 NFC임을 검증**할 뿐 정규화해 주지 않는다
  (`unicodedata.normalize("NFC", value) == value`). 따라서 NFC 보장 책임은 생산자인 Phase 2A에 있다.
- Task 1 회귀 테스트 `test_parse_mfds_label_artifact_preserves_raw_parsed_structure`는 seam이 NFD 입력을
  NFC로 바꾸지 않고 trailing space·연속 빈 줄도 정리하지 않음을 고정한다. (XML 1.0이 요구하는 line-ending
  정규화는 expat 동작이며 이 seam의 책임 경계가 아니다.)

canonical structure 생성은 이 seam의 책임이 아니다. `data-*` attribute 제외 등 기존
canonicalization 규칙은 그대로 유지하되, `_canonical_element()` 호출은 기존에 canonicalization을
수행하던 `_load_document()` 경로에만 남긴다. 구조 검사만 필요한 `inspect_xml()`은 재귀
canonicalization을 수행하지 않으므로 깊게 중첩된 유효 XML에서도 기존과 동일하게 동작한다.
`ParsedMfdsLabelDocument`는 `root`를 `field(repr=False)`로 보유하고 canonical structure를 필드로
갖지 않으므로 repr·로그에 Source 본문이 새지 않는다.

### 6.2 `mfds-label-knowledge-chunk@1` 제안

이 절은 구현 가능한 구체안이지만 아직 승인된 공유 계약은 아니다.

1. Source member 하나를 Document 하나로 매핑한다.
2. parser의 `DOC` 바로 아래 자식 element를 source order로 순회한다.
3. 각 top-level `ARTICLE`을 하나의 의미 chunk로 만든다. nested ARTICLE은 부모 chunk 안에서
   원래 순서와 heading hierarchy를 유지한다.
4. `DOC` 바로 아래의 연속된 non-ARTICLE content는 인접 ARTICLE과 합치지 않고 하나의 별도
   top-level block chunk로 만든다. 공백만 있는 block은 만들지 않는다.
5. v1은 token/character 수에 따른 2차 분할과 overlap을 하지 않는다. source semantic boundary를
   임의 길이보다 우선한다.
6. `chunk_index`는 위 top-level block의 source order를 따라 0부터 연속 증가한다.
7. chunk text renderer:
   - ARTICLE title은 첫 줄 heading으로 그대로 출력한다.
   - paragraph/block 경계는 `\n`, 서로 다른 의미 block 경계는 `\n\n`으로 표현한다.
   - inline element는 text와 tail을 source order로 연결한다.
   - table은 row마다 한 줄, cell마다 `\t`로 표현하며 caption/header/unit/footnote의 source order를
     보존한다.
   - **renderer가** parsed text에 Unicode NFC 정규화를 적용하고 CRLF/CR을 LF로 통일한다. parser seam은
     정규화하지 않으므로(§6.1 정규화 책임 경계) 이 단계가 NFC의 유일한 생산 지점이다.
   - 각 line의 trailing whitespace를 제거하고 연속된 빈 line은 최대 1개로 줄인다.
   - 전체 결과의 leading/trailing whitespace를 제거한다.
   - source에 없는 의학적 문구, 요약, “내용 없음” 표지, section label을 추가하지 않는다.
8. 최종 `chunk_text`가 빈 문자열이면 저장하지 않고 정책 오류로 처리한다.
9. Document `canonicalization_spec_version`은 Source Snapshot의 기존 canonicalization version을
   기록한다. Chunk `normalization_version`은 `mfds-label-knowledge-chunk@1`을 기록한다.
10. 모든 chunk hash는 renderer 완료 후 최종 문자열에서 계산한다.

### 6.3 NN 공식 빈 항목 보류

NN parser는 공식 문서의 title-only ARTICLE을 합법적인 `PARTIAL_OFFICIAL` 상태로 보존한다.
그러나 현재 Knowledge schema에는 “공식 빈 본문”과 “실수로 누락된 chunk”를 구분할 metadata가
없다. 임의의 `[공식 원문 본문 없음]` 문구를 chunk에 넣으면 Source에 없는 text가 검색 근거가 되고,
빈 chunk를 저장하면 기존 exact content 검증의 의미가 약해진다.

따라서 다음처럼 범위를 나눈다.

- EE/UD/NB: v1 독립 구현·저장 가능
- 본문이 있는 NN ARTICLE: 위 renderer로 draft 생성 가능
- 하나라도 공식 빈 NN ARTICLE이 있는 NN member: Document/Chunk persistence를
  `CHUNK_POLICY_UNSUPPORTED`로 보류
- 이 보류는 동일 품목의 EE/UD/NB 설계를 중단시키지 않는다. 실행 request에서 NN을 분리한다.

NN representation은 계약 owner가 다음 중 하나를 명시적으로 결정해야 한다: metadata 필드 추가,
별도 상태 행, source text가 아닌 marker의 명시적 허용, 또는 빈 ARTICLE의 Document-only 표현.

### 6.4 policy 변경 규칙

현재 Index stable coordinate에는 chunk policy version이 포함되지 않는다. 같은 `source_version`과
`external_document_id`에 다른 분할 결과를 쓰면 같은 coordinate의 content 의미가 바뀐다.
따라서 v1으로 materialize한 Source version을 in-place rechunk하지 않는다. 향후 size split/overlap/
renderer 변경은 coordinate versioning 방식을 먼저 공유 계약으로 결정한 뒤 새 policy로 진행한다.

## 7. 저장 transaction과 동시성

### 7.1 transaction 전 (실행 순서)

요청 검증 단계에서 **아직 조회하지 않은** member의 section 집합을 이미 아는 것처럼 취급하지 않는다. 요청 자체의
문법·형식 검증과, 조회된 실제 member로 판정하는 section 집합 검증은 서로 다른 단계다.

1. 요청의 문법적·형식적 검증 — `snapshot_id`/`member_ids` UUID 형식, `member_ids` 비어있지 않음·중복 UUID
   없음, `expected_item_seq`가 `^[0-9]{9}$`, `chunk_policy_version`이 지원 목록에 존재.
2. read-only query로 Snapshot, Member, IngestionRun, Artifact read model을 확보한다.
3. **조회된 실제 member**의 section 집합과 lifecycle/status를 검증한다. section 집합은 정확한
   `{EE, UD, NB}` 또는 정확한 `{NN}` 단독이어야 하고, 모든 member의 locator 품목이 `expected_item_seq`와
   같아야 하며, IngestionRun `run_status`가 terminal success여야 한다.
4. artifact locator와 identity 결속을 검증한다 — `artifact_kind`, `artifact_key`, `object_key`,
   `storage_backend`, `content_type`, section별 exact `page_number`, `reject_code`/`parser_location` 부재.
5. 기존 `LocalPrivateSourceArtifactReader.read_verified()`로 raw bytes를 읽고 실제 byte size와 SHA-256을
   artifact receipt 및 member hash와 비교한다.
6. parser seam `parse_mfds_label_artifact()`를 호출한다.
7. deterministic renderer/chunker(정규화 포함)로 모든 Document/Chunk draft를 메모리에서 만들고
   `SECTION_ORDER` 순서로 정렬한다.

1~7 단계 중 하나라도 실패하면 DB write transaction을 열지 않는다. 8단계 이후(§7.2)의 실패는 전체 rollback이다.

#### 결정적 출력 순서

순서 기준을 새로 발명하지 않고 기존 `SECTION_ORDER = ("EE", "UD", "NB", "NN")`을 정본으로 재사용한다.
결과는 요청 `member_ids` 순서, DB 조회 순서, SQL row 반환 순서에 의존하지 않는다.

정렬 대상: `MaterializationSourceDocument` tuple, `KnowledgeDocumentDraft` tuple, 각 문서의
`KnowledgeChunkDraft` tuple(`chunk_index` 오름차순), receipt의 `documents`/`chunks`, exact replay 비교에
사용하는 기존 Document/Chunk collection.

#### section별 exact page binding

`RagSourceIngestionArtifact.page_number`는 MFDS ingestion이 `enumerate(plan.documents, start=1)`로 부여하고
`plan.documents`는 `SECTION_ORDER` 순서이므로 다음으로 고정된다.

| Section | Expected `page_number` |
|---|---|
| `EE` | 1 |
| `UD` | 2 |
| `NB` | 3 |
| `NN` | 4 |

`_validate_plan()`이 문서 집합을 `REQUIRED_SECTIONS` 또는 `SECTION_ORDER`로만 허용하므로 `NN` 단독 ingestion
run은 존재할 수 없다. 따라서 `NN` 단독 materialization 요청의 대상 artifact도 항상 4-artifact run의 page 4다.
`page_number >= 1`만 검증하지 않고 `page_number == SECTION_ORDER.index(section) + 1`을 정확히 검증한다.
DB 스키마상 `page_number`는 nullable(`int | None`)이므로 `None`도 negative 경로로 처리한다.

#### Snapshot identity·checksum race 검증 (재계산 아님)

Phase 2A는 `raw_manifest_checksum`·`canonical_checksum`의 **생성 알고리즘을 소유하지 않는다.** 선택된 member
집합만으로 두 checksum을 다시 계산하지 않는다 (Snapshot 전체 domain의 값이므로 부분 집합 재계산은 정의상
다른 값이 된다). Phase 2A가 확인하는 것은 기존 Snapshot identity의 **불변성**이다.

1. 읽기 단계에서 Snapshot row의 식별자·lifecycle/status·checksum들을 read model로 확보한다.
2. artifact를 읽고 draft를 계산한다 (read-only).
3. write transaction에서 §7.2의 공통 순서대로 동일 Snapshot row를 lock한다.
4. lock 후 다시 읽은 `snapshot_id`, `verification_status`, source/operation 결속(`source_id`,
   `operation_id`, `source_version`), `raw_manifest_checksum`, `canonical_checksum`,
   `canonicalization_spec_version`을 pre-read 값과 비교한다.
5. 하나라도 달라졌으면 race / stale input으로 fail-closed 처리하고 write 없이 rollback한다
   (`ARTIFACT_INTEGRITY_MISMATCH`).
6. `NN` singleton 요청에도 같은 원칙을 적용한다. 단일 member라는 이유로 검증을 축소하지 않는다.

### 7.2 원자적 저장

한 request의 EE/UD/NB 문서와 chunk를 하나의 짧은 transaction으로 저장한다.

1. `snapshot_id + expected_item_seq`에서 안정적으로 만든 PostgreSQL transaction advisory lock을
   획득한다. 기존 Index builder는 namespace `0`과 `f"{index_code}:{index_version}"`를 쓰므로
   materialization은 namespace `1`과 `f"mfds-materialize:{snapshot_id}:{expected_item_seq}"`를 쓴다.
2. **기존 Index flow와 정렬한 공통 테이블 순서**로 row lock을 획득한다.

   > **상태: 확정 아님 — Task 3/4 검증 전 가설.** 아래 순서는 *한 member 문장 안에서 잠기는 테이블 순서*를
   > Index와 맞춘 것이다. 기존 Index는 member를 `knowledge_chunk_id.bytes` 오름차순으로 순회하고
   > materialization은 아직 chunk id가 없어 `SECTION_ORDER` 오름차순으로 순회하므로 **두 flow의 실제 row
   > 획득 순서가 같다고 보장되지 않는다.** 서로 다른 member 사이에서 row 집합이 교차하면 테이블 순서 일치
   > 만으로 deadlock을 배제할 수 없다. 따라서 "deadlock-safe 공통 순서 확정"이라고 기술하지 않고 Task 4의
   > cross-flow 동시 실행 테스트로 검증할 가설로 둔다. 교착이 재현되면 순회 기준 통일(예: 양쪽 모두
   > `source_snapshot_member_id` 오름차순)이나 상위 직렬화 수단을 책임 리뷰어와 재결정한다.

   `member_ids` 순회는 `SECTION_ORDER` 오름차순으로 고정하여 **자기 자신의 실행 간 순서만** 결정적으로 만든다
   (materialization은 아직 chunk id가 없어 section 순서가 유일한 결정적 기준이다).

   기존 `SqlAlchemyKnowledgeEvidenceIndexRepository.persist_complete_index()`의 실측 lock 순서:

   | 단계 | 대상 |
   |---|---|
   | 1 | `pg_advisory_xact_lock(hashtextextended('<index_code>:<index_version>', 0))` |
   | 2 | `_source_binding_statement()` → `FOR UPDATE OF rag_source, rag_source_endpoint, rag_source_operation, rag_source_snapshot, rag_source_snapshot_member, knowledge_document, knowledge_chunk` |
   | 3 | `_artifact_origin_lock_statement()` (ARTIFACT member) → `FOR UPDATE OF rag_source_ingestion_run, rag_source_ingestion_artifact` |
   | 4 | `FOR UPDATE OF rag_knowledge_index` |

   두 flow가 공유하는 row의 **공통 순서**:

   ```text
   rag_source → rag_source_endpoint → rag_source_operation → rag_source_snapshot
   → rag_source_snapshot_member → knowledge_document → knowledge_chunk
   → rag_source_ingestion_run → rag_source_ingestion_artifact
   ```

   Phase 2A는 이 테이블 순서를 그대로 따른다. Revision 8까지 기재했던
   `snapshot → ingestion_run → ingestion_artifact → snapshot_member` 순서는 Index와 반대이므로 폐기한다.

   원칙:
   - lock 획득 **전**에 read-only artifact I/O(파일 읽기·checksum 재계산·draft 계산)를 모두 끝낸다.
   - transaction 내부에서 object read를 하지 않는다.
   - deadlock retry를 근거 없이 도입하지 않는다. 순서 통일로 예방하고, 발생하면 fail-closed로 보고한다.
   - `FOR UPDATE`는 대상 테이블마다 UPDATE 권한을 요구한다. `knowledge_index_builder`는
     `infra/python/knowledge_index_role_policy.py`의 `KNOWLEDGE_INDEX_LOCK_COLUMNS`에 따라 lock marker
     컬럼만 UPDATE 권한을 갖는다. `rag_source_snapshot`은 예외적으로 `management_lock_marker`가 lock
     전용 컬럼이다. Phase 2A는 이 목록을 확장하지 않는다.
   - `knowledge_index_builder`는 `rag_source_snapshot_verification` SELECT 권한이 없으므로 Snapshot
     적격성은 `RagSourceSnapshot.verification_status`로만 판정한다.
   - `#643`(`91b6062d`)은 Source writer의 `FOR UPDATE` lock marker 권한을 최소화했다
     (`WRITER_LOCK_TABLES`). Phase 2A는 writer role이 아니라 builder role 경계에서 동작한다.
3. transaction 전 조회와 동일한 snapshot version/checksum, member locator/hash, artifact binding인지
   재검증한다.
4. persistence lifecycle gate를 검증한다.
5. natural key로 기존 Document/Chunk를 조회한다.
6. 행이 없으면 Document를 먼저 insert하고 그 ID로 Chunk를 `chunk_index` 순서대로 insert한다.
7. 행이 있으면 모든 immutable field, chunk count/index/text hash/normalization version을 비교한다.
8. exact match이면 update 없이 기존 ID를 반환한다.
9. 하나라도 다르면 `CONTENT_CONFLICT`로 전체 rollback한다. 기존 행을 update/delete하지 않는다.
10. 새 행과 exact replay가 섞인 부분 적재 상태도 허용하지 않는다. 기존 집합이 요청 전체와 정확히
    같거나, 요청 전체가 새로 insert되는 경우만 성공한다.

11. INSERT 또는 exact replay 판정 직후, **같은 transaction 안에서** row를 재조회해 receipt를 재구성하고
    생성 receipt와 비교한다. 불일치는 `RECEIPT_MISMATCH`이고 transaction 전체 rollback이다. 이 순서는 기존
    Index 어댑터와 같다 — `persist_complete_index()`는 `session.begin()` 블록 안에서
    `_load_and_recompute_receipt()`를 호출하고 불일치 시 commit 전에 raise한다.
12. commit. **commit 이후에는 rollback이 불가능하다.** commit 뒤의 새 session 재조회는 rollback 불가능한
    **별도 audit**으로만 정의하며, 계약 성공 조건이 아니고 실패 시 `RECEIPT_MISMATCH`로 보고하지 않는다.

Unique constraint race는 advisory lock이 1차로 직렬화한다. 방어적으로 insert는 conflict 후 동일
transaction에서 재조회하여 exact replay만 허용한다. unexpected exception, cancellation,
parser/hash mismatch 모두 요청 전체 rollback이다.

### 7.3 lifecycle gate

순수 parse/chunk draft는 명시적 Snapshot/member와 검증된 raw bytes가 있으면 CURRENT 여부와 분리해
테스트할 수 있다. 반면 `ACTIVE` KnowledgeDocument를 실제 저장하면 즉시 Index 후보가 되므로,
기본 제안은 기존 Index source binding과 같은 조건을 persistence 시점에 요구하는 것이다.

- Source `ACTIVE`
- Endpoint `VERIFIED`, runtime `ENABLED`, acquisition `APPROVED`
- Operation runtime `ENABLED`, acquisition `APPROVED`
- Snapshot `CURRENT`
- IngestionRun `run_status` == `SUCCEEDED` (단일 상태)
- member/artifact origin과 checksum 유효, section별 exact `page_number`, `content_type` exact match

IngestionRun 상태 근거 (실측):

- `RagIngestionRunStatus` = {`RUNNING`, `SUCCEEDED`, `SUCCEEDED_WITH_REJECTIONS`, `NO_CHANGE`, `FAILED`}
  (`backend/app/models/rag_source.py`).
- `persist_source_ingestion_result()`는 `SnapshotIngestionDecision.CREATED` 경로에서 run을 **생성할 때**
  `run_status = "SUCCEEDED_WITH_REJECTIONS" if metadata.rejected_record_count else "SUCCEEDED"`를 기록한다.
  이 경로에 `RUNNING → SUCCEEDED` 사후 전이는 없다. `RUNNING`은 모델 기본값으로만 존재한다.
- **MFDS label 경로는 `_validate_metadata()`에서 `metadata.rejected_record_count == 0`을 강제한다**
  (`expected` tuple의 `0`, 불일치 시 `ValueError("MFDS_LABEL_METADATA_MISMATCH")`). 따라서 현재 실제 MFDS
  artifact-producing 경로가 만들 수 있는 성공 상태는 `SUCCEEDED` **하나**다. `SUCCEEDED_WITH_REJECTIONS`는
  enum에 존재하지만 이 경로가 생성하지 않으므로 허용 입력으로 넓히지 않는다 (§13 미확정 항목).
- `FAILED` run도 artifact를 생성하지만 `snapshot_id IS NULL`이다
  (`chk_rag_ingestion_run_snapshot_status`).
- `NO_CHANGE` run도 artifact를 생성하지만 그 `snapshot_id`는 **직전 비교 Snapshot**을 가리킨다. 즉 그 run이
  해당 Snapshot member를 만든 run이 아니다. 따라서 Phase 2A는 보수적으로 거절하고, 허용 여부는
  §13의 미확정 항목으로 남긴다.
- 상태 불일치는 새 reason을 만들지 않고 `SOURCE_NOT_ELIGIBLE`로 보고한다.

이 조건은 안전한 제안이지 현재 materialization 전용 계약으로 확정된 사실은 아니다. 계약 owner가
“materialize는 가능하지만 Index 불가” 중간 상태를 원한다면 현재 `KnowledgeDocumentStatus`만으로
표현 가능한지 먼저 결정해야 한다. 합의 전에는 pure draft·synthetic DB 검증까지만 진행하고 actual
persistence entrypoint를 활성화하지 않는다.

## 8. 멱등성과 실패 의미

### 8.1 멱등성

동일한 Snapshot/member/policy 요청은 다음을 보장한다.

- Document/Chunk row count 불변
- 기존 UUID 불변
- `is_exact_replay`를 **제외한** 모든 receipt 필드가 동일 (`is_exact_replay`는 첫 실행 `False`,
  재실행 `True`가 정상이므로 동일성 비교에서 제외한다. "field-equivalent receipt"라고 쓸 때는 항상 이 제외
  규칙을 함께 명시한다. 순수 draft에는 이 필드가 없으므로 draft 수준에서는 전체 필드 동일성을 요구할 수 있다)
- `created_at` 변경 없음
- update/delete 수행 없음
- 두 concurrent request 중 하나가 insert한 뒤 다른 하나는 exact replay로 종료

멱등성은 natural key가 같다는 사실만으로 인정하지 않는다. 문서 hash, external ID,
canonicalization version, chunk index/text/hash/normalization version의 완전한 동일성이 필요하다.

Phase 2A 소유 불변 필드 비교에는 Document의 `publisher`, `source_url`, `document_version`
(모두 `NULL`) 및 `knowledge_index_lock_marker == 0`도 포함한다.

반면 Chunk의 `embedding_model`과 `vector_store_key`는 Phase 2A가 소유하지 않는 **legacy nullable field**이며
현재 authoritative owner는 **미확정**이다 (Revision 9 정정).

실측 근거:

- 기존 `SqlAlchemyKnowledgeEvidenceIndexRepository`는 두 컬럼을 읽거나 쓰지 않는다.
- `knowledge_index_builder` role은 `knowledge_chunk`에 `GRANT SELECT, INSERT`와
  `GRANT UPDATE (knowledge_index_lock_marker)`만 받는다. 두 컬럼을 UPDATE할 권한이 없다
  (`infra/python/knowledge_index_role_policy.py`).
- 두 컬럼에 값이 관측되는 유일한 확인 사례는 migration 보존 회귀
  (`tests/integration/rag/test_knowledge_evidence_index_postgresql.py`)의 `LEGACY_V1` row다.

따라서 "후속 Index/Embedding 단계가 소유한다"고 단정하지 않는다. Phase 2A는 insert 시 `NULL`을 쓰고, exact
replay에서 두 필드를 **갱신하지 않으며**, 기존 non-NULL 값이 있으면 **보존**하고 비교 대상에서 제외한다.
owner 확정 전에는 두 필드의 의미를 추정·확장하지 않는다 (`vector_store_key`는 UNIQUE nullable이므로 임의 값
주입은 후속 단계를 차단할 수 있다).

### 8.2 내부 오류 분류

초기 구현은 public API를 추가하지 않고 다음 내부 reason을 사용한다.

| reason | 조건 |
|---|---|
| `REQUEST_INVALID` | UUID/중복/item sequence/member 집합 오류 |
| `SOURCE_BINDING_INVALID` | Snapshot/member/artifact 소속·locator·origin 불일치 |
| `SOURCE_NOT_ELIGIBLE` | 합의된 persistence lifecycle gate 불충족 |
| `ARTIFACT_INTEGRITY_MISMATCH` | byte size/raw/member checksum 불일치 |
| `PARSER_REJECTED` | 기존 parser가 공식 구조를 수용하지 못함 |
| `CHUNK_POLICY_UNSUPPORTED` | NN 공식 빈 항목 또는 지원하지 않는 policy/구조 |
| `CONTENT_CONFLICT` | 같은 natural key에 다른 immutable content가 존재 |
| `RECEIPT_MISMATCH` | **commit 전 같은 transaction 안에서** 재조회한 결과가 저장 receipt와 불일치 (commit 이후 audit 불일치는 rollback 불가이므로 이 reason으로 보고하지 않는다) |
| `DEPENDENCY_ERROR` | DB/storage의 분류되지 않은 안전한 실패 |

예외와 로그에는 raw content, object key/path, DB URL, credential을 포함하지 않는다. UUID·source code·
안전한 reason·hash의 짧은 prefix만 structured log allowlist로 허용한다.


#### Artifact reader 오류 변환 — typed boundary 확정까지 보류 (BLOCKED)

Revision 10에서 기재한 3-way 매핑 표는 **철회한다.** 현재 인터페이스로 구현할 수 없다.

- `LocalPrivateSourceArtifactReader.__init__`의 root 절대경로·디렉터리·world-writable·writer-writable
  ·symlink 위반은 **생성자에서** 평문 `ValueError`로 발생한다. reader가 `materialize_documents()`에 전달되기
  전이므로 순수 커널이 변환할 대상이 아니다 → **composition boundary(DI 조립 지점/entrypoint)에서 처리**하고
  계약의 failure reason 체계로 투영하지 않는다.
- `read_verified()` 경로의 크기 불일치·checksum 불일치·I/O 실패·object key root 이탈은 **모두 구분 없는
  일반 `ValueError`**다. 타입으로 나뉘지 않으므로 reason 3분류는 메시지 문자열 비교를 요구하고, 이는
  "메시지에 의존하지 않는다"는 계약과 모순된다.

결정 옵션과 권고는 계약 문서 "Parser 계층 격리 및 오류 변환 계약" 3절에 기록했다. 요약: **옵션 B(Phase 2A
소유 typed loader adapter)** 를 기본 권고로 하고, integrity/access 구분이 필요하면 **옵션 A(reader typed
예외)** 로 승격한다. reader 실패 전체를 `DEPENDENCY_ERROR`로 합치는 옵션 C는 integrity fail-closed 신호를
약화하므로 권고하지 않는다.

reader 호출 **전에** Phase 2A가 스스로 판정할 수 있어 지금도 세분화가 가능한 항목은 둘뿐이다.

- `object_key != LocalPrivateSourceArtifactStore.object_key_for_checksum(raw_checksum)` →
  `SOURCE_BINDING_INVALID` (reader를 호출하지 않는다)
- Phase 2A가 직접 구성하는 `RawArtifactMetadata(...)`의 `ValueError` → `SOURCE_BINDING_INVALID`
  (예외 타입이 아니라 **자기 호출 위치**로 구분된다)

#### 공개 오류 계약 (reason-only)

자유형 message 인자를 갖는 `KnowledgeMaterializationError(reason, message=None)` 형태를 쓰지 않는다. 저장소의
기존 reason-only 관례와 동일한 형태로 설계한다 (`KnowledgeEvidenceIndexValidationError`,
`SourceSnapshotMemberError` — 둘 다 `__init__(self, reason) -> None`, `super().__init__(reason.value)`).

```python
class KnowledgeMaterializationError(Exception):
    def __init__(self, reason: KnowledgeMaterializationFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)
```

- 외부 노출 문자열은 enum reason 값으로 한정한다.
- `object_key`, 로컬/호스트 경로, DB detail, SQL text, 원문 XML, source text를 노출하지 않는다.
- `repr(error)`에도 민감정보가 없어야 한다.
- 하위 parser의 `ValueError` 메시지를 공개 오류 문자열로 그대로 전달하지 않고 `PARSER_REJECTED`로만 투영한다.
- 내부 원인은 exception chaining(`raise ... from exc`) 또는 내부 로깅 정책으로만 보존하며, 그 로깅에도
  민감정보를 넣지 않는다.

## 9. 기존 Index 호환 검증

저장 결과는 별도 변환 없이 `KnowledgeChunkIdentity`를 구성할 수 있어야 한다.

| Index identity 필드 | Phase 2A 출처 |
|---|---|
| `knowledge_chunk_id` | 저장된 Chunk UUID |
| `source_snapshot_id` | request/검증된 Snapshot |
| `source_snapshot_member_id` | Document의 Source member FK |
| `source_code`, `source_version`, `snapshot_canonical_checksum` | transaction에서 재검증한 Source/Snapshot |
| `external_document_id` | 승인된 deterministic document identity |
| `chunk_index`, `chunk_content_hash` | 저장된 Chunk |
| `source_member_locator` | 검증된 member locator |

호환 acceptance 조건:

- Document contract version은 `KNOWLEDGE_EVIDENCE_V1`, status는 `ACTIVE`.
- `document_content_hash == SnapshotMember.content_sha256`.
- `sha256(chunk_text UTF-8) == KnowledgeChunk.content_hash`.
- Snapshot/member/source lifecycle 조건이 Index adapter 검증과 일치.
- stable coordinate 중복이 없고 순서가 결정적.
- 합성 embedding/vector key로 기존 `persist_complete_index()` integration test를 통과.

Phase 2A는 embedding 값을 Document/Chunk에 기록하지 않는다. Index build 단계가 별도 adapter를 통해
embedding을 만들고 완전한 Index transaction을 소유한다.

## 10. 테스트 설계

### 10.1 parser 회귀

- 기존 ingestion fixture를 public parser seam으로 읽은 결과가 기존 plan 결과와 동일하다.
- XML encoding/DTD/entity/data attribute 처리 결과가 바뀌지 않는다.
- seam은 NFC 정규화·trailing space 제거·빈 줄 축약을 수행하지 않는다 (raw parsed structure 보존 경계).
- `inspect_xml()` report의 키 순서·값과 ValueError 의미가 develop 기준과 동일하다.
- 깊게 중첩된 2 MiB 미만 유효 XML에서 `RecursionError`가 발생하지 않는다.
- `repr(ParsedMfdsLabelDocument)`에 Source 본문·제목·`root`·canonical structure가 없다.
- EE/UD/NB 구조 및 NN 7개 exact title 검증을 그대로 유지한다.
- malformed XML, DOCTYPE/entity 위반, section mismatch는 기존 reason을 유지한다.

### 10.2 pure chunking과 정규화

- 같은 parsed tree + 같은 policy는 byte-identical chunk text/index/hash를 만든다.
- NFD 입력과 NFC 입력이 동일한 canonical `chunk_text`를 만든다.
- 동일한 canonical `chunk_text`로부터 동일한 `KnowledgeChunk.content_hash`가 생성된다.
- **반대로** NFD 입력과 NFC 입력의 `document_content_hash`는 **서로 달라야** 한다
  (`document_content_hash == member.content_sha256`이고 raw bytes가 다르므로). 정규화 결과가 같다는 이유로
  document hash를 같게 만들면 기존 Index 계약 위반이다 (§5.2 참조).
- newline 표현 차이(연속 빈 줄 / trailing space)가 canonical `chunk_text` 결과를 바꾸지 않는다.
- parser root의 원문 표현과 Task 2 canonical projection 책임이 혼합되지 않는다 (seam 출력은 정규화 이전).
- 입력 source document tuple 순서를 뒤섞어도 **draft** 순서가 `SECTION_ORDER` 기준으로 동일하다.
- 같은 입력의 반복 실행이 **전 필드 동일 draft**를 만든다 (draft에는 `is_exact_replay`가 없다).
- Repository 반환 순서 / SQL row 순서와 receipt 순서·동일성 검증은 **Task 3/4 범위**다. Task 2는 draft만
  반환하므로 receipt를 검증 대상으로 두지 않는다 (§10.4, §10.5 참조).
- ARTICLE source order와 nested heading order를 보존한다.
- paragraph 및 inline text/tail이 중복·누락되지 않는다.
- table caption/header/unit/row/cell/footnote 순서를 보존하고 `\t`/`\n` 규칙이 고정된다.
- title-only ARTICLE은 source title만 있는 결정적 결과를 만들되, 빈 최종 text는 거부한다.
- source에 없는 설명·요약·marker가 추가되지 않는다.
- NN 공식 빈 항목은 `CHUNK_POLICY_UNSUPPORTED`; EE/UD/NB draft는 독립적으로 성공한다.
- unsupported policy version은 fail-closed.

### 10.3 reader와 provenance

- snapshot에 속하지 않는 member, 다른 품목 혼합, 중복 member, 필수 section 누락, 추가 section
  결합, `EE+NN` 부분 혼합을 `REQUEST_INVALID`로 거부한다.
- `artifact_kind`, `artifact_key`, `object_key`, `storage_backend`, `content_type`, `page_number`,
  `reject_code`, `parser_location` 각각의 mutation을 `SOURCE_BINDING_INVALID`로 거부한다.
- `page_number`가 section 기대값(`EE`=1, `UD`=2, `NB`=3, `NN`=4)과 다르거나 `None`이면 거부한다.
- `content_type`이 `OBSERVED_CONTENT_TYPE` exact match가 아니면 거부한다.
- IngestionRun `run_status`가 `SUCCEEDED` 이외(`SUCCEEDED_WITH_REJECTIONS`·`RUNNING`·`FAILED`·`NO_CHANGE`)이면 `SOURCE_NOT_ELIGIBLE`로 거부한다.
- 다른 품목 또는 다른 Snapshot Member의 artifact 재사용을 거부한다.
- 공개 오류 문자열이 enum reason 값으로 한정되고 `object_key`·경로·SQL·원문 XML·parser ValueError detail이
  `str(error)` / `repr(error)`에 없다.
- reader `ValueError` → reason 매핑 테스트는 typed boundary 결정 전까지 **작성하지 않는다** (Task 2b 보류).
  단, reader 호출 전에 판정되는 `object_key`·`RawArtifactMetadata` 두 항목은 지금도 검증 가능하다.
- `MaterializationSourceDocument`만으로 `RawArtifactMetadata`를 구성해 기존 reader를 호출할 수
  있음을 확인한다.
- member locator의 section과 parser section 불일치를 거부한다.
- artifact size/raw hash/member hash 중 하나라도 다르면 write가 0건이다.
- path escape, symlink, writable artifact, root outside는 기존 reader가 거부하며 path가 로그에 새지 않는다.
- snapshot canonical checksum을 document/chunk hash로 잘못 사용하는 mutation test가 실패한다.

### 10.4 repository와 transaction

- fresh request는 3개 Document와 기대 Chunk를 한 transaction으로 insert한다.
- 두 번째 동일 request는 row count/UUID/created_at 불변인 exact replay다.
- 같은 natural key의 다른 document hash, chunk text/hash/count/index/policy는 `CONTENT_CONFLICT`다.
- 중간 Chunk insert 실패 시 그 request의 Document/Chunk가 모두 rollback된다.
- 기존 부분 집합만 있는 비정상 DB 상태는 나머지를 채우지 않고 conflict다.
- 두 concurrent request는 한 집합만 만들고 두 receipt의 UUID/hash가 같다.
- transaction 전 조회 후 Snapshot/member 상태나 hash를 바꾼 race는 저장 전 재검증에서 실패한다.
- lifecycle gate 각 조건의 실패가 `SOURCE_NOT_ELIGIBLE`이고 write는 0건이다.
- **commit 전 같은 transaction 안에서** receipt를 재구성·비교하고 mutation/누락은 `RECEIPT_MISMATCH`로
  commit 전에 rollback된다 (기존 `persist_complete_index()`와 동일한 순서).
- commit 이후 경로에 rollback 시도가 없다. commit 뒤 확인은 rollback 불가능한 선택적 audit이며 실패를
  `RECEIPT_MISMATCH`로 보고하지 않는다.
- row lock 순서가 기존 Index flow와 동일한 공통 순서를 따른다 (mock session의 SQL 순서 검증).
- lock 획득 전에 artifact I/O가 완료되고 transaction 내부에 object read가 없다.
- lock 후 재조회한 Snapshot checksum이 pre-read와 다르면 `ARTIFACT_INTEGRITY_MISMATCH`이고 write는 0건이다.
- 선택된 member 집합으로 snapshot checksum을 재계산하는 코드 경로가 없다.

### 10.5 Index compatibility integration

합성 PostgreSQL fixture에서 Phase 2A repository로 rows를 만든 뒤 그 receipt로
`KnowledgeChunkIdentity`와 기존 Index draft를 구성한다.

- 정상 결과는 기존 Index repository의 full source binding 검증과 commit을 통과한다.
- Document hash를 member hash와 다르게 mutation하면 실패한다.
- Chunk text 또는 hash만 mutation하면 exact UTF-8 검증에서 실패한다.
- locator/external ID/chunk index/snapshot checksum mutation이 각각 실패한다.
- 일반 runtime role은 materialization insert를 못 하고 builder role만 기존 허용 범위에서 성공한다.
- Document의 `publisher`/`source_url`/`document_version`을 non-NULL로 변조한 뒤 재실행하면
  `CONTENT_CONFLICT`이고 rollback된다.
- Chunk의 `embedding_model`/`vector_store_key`를 legacy 값으로 채운 뒤 재실행해도 `CONTENT_CONFLICT` 없이
  exact replay로 종료하고 두 값이 보존된다.
- 동일 Snapshot에 대한 materialization exact replay와 Index build를 동시 실행해도 교착 없이 완료되거나
  정의된 fail-closed 결과를 반환하며, partial document/chunk write가 없고 rollback 후 재실행이 가능하다.

### 10.6 검증 명령 계획

구현 PR에서는 가장 작은 순서로 실행한다.

Worker 단위 테스트는 저장소 공식 helper(`scripts/ci/test_environment.sh`)를 사용한다. 개인 절대 경로나
`.venv` 직접 호출을 문서에 남기지 않는다.

```bash
cd "$(git rev-parse --show-toplevel)"
export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
source scripts/ci/test_environment.sh
run_with_worker_test_environment pytest ai_worker/tests/rag/source_ingestion/test_mfds_label.py -q
run_with_worker_test_environment pytest ai_worker/tests/rag -q
```

Lint·type 검사는 `CONTRIBUTING.md`의 "완료 전 검사" 명령을 따른다.

```bash
uv run ruff check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
uv run ruff format --check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
uv run mypy backend/app ai_worker
```

Task 2~4가 추가될 때의 명령은 `docs/testing.md`의 `#634` 절과 계획서를 정본으로 사용한다. 격리 PostgreSQL
lane은 `prepare_test_environment` + `run_with_integration_test_environment`를 쓴다.

실행 순서는 가장 작은 범위부터: Task 1 parser → pure kernel → mock repository → 격리 PostgreSQL →
`bash scripts/ci/run_test.sh` → `git diff --check`와 전체 diff 검토.

외부 MFDS API나 실제 credential은 unit/integration test에 사용하지 않는다.

## 11. 로컬 합성 구현과 서버 권한 단계

### 11.1 지금 독립적으로 가능한 로컬 합성 범위

- public parser seam 추출과 기존 parser 회귀 고정
- pure request/draft/receipt 타입과 v1 renderer
- in-memory verified reader fixture를 이용한 hash·chunk unit tests
- SQLAlchemy repository와 isolated PostgreSQL transaction/concurrency tests
- synthetic Source/Endpoint/Operation/Snapshot/Member/Artifact rows를 이용한 Index 호환 test
- proposed materialization contract 초안 작성

위 범위는 실제 MFDS artifact, 서버 mount, production credential 없이 구현 가능하다.

### 11.2 서버 권한·실데이터가 필요한 범위

- `#591` actual Source ingestion 및 새 session requery로 확정된 Snapshot/member receipt 확보
- `#593`의 실제 Source/Endpoint/Operation ID와 lifecycle/acquisition 상태 승인
- `#613`의 read-only artifact consumer mount, OS identity, journal/finalizer/cleanup E2E 확인
- standalone materializer 실행 주체가 기존 knowledge builder DB credential을 안전하게 받는지 확인
- reader root와 DB builder role이 일반 AI worker runtime에 함께 노출되지 않는 실행 경계 승인
- actual artifact read → parse → draft의 dry-run evidence
- 합의된 lifecycle gate 하의 actual persistence 승인
- persistence 뒤 별도 session 재조회와 Index compatibility evidence

서버 조건이 해소되기 전에는 actual 데이터 행을 만들지 않으며, 합성 성공을 Source handoff 완료로
보고하지 않는다.

## 12. 구현 순서와 각 단계의 종료 조건

### 단계 0 — governance와 계약 접점 고정

- `#634`에 구현 담당 `@ceohwj`, 단일 책임 reviewer `@phina-io`, specialist evidence
  `@Jye-rookie`를 기록했다. reviewer 지정은 실제 review 승인과 구분한다.
- `external_document_id`/locator 후보, lifecycle persistence gate, v1 chunk policy를 Proposed 계약에
  기록하고 affected owner 검토를 받는다.
- NN 공식 빈 항목 표현은 별도 결정으로 남겨도 EE/UD/NB 구현은 진행한다.

종료 조건: 구현 PR이 참조할 Issue와 reviewer, EE/UD/NB 좌표·policy가 문서에 명시됨.

### 단계 1 — parser seam과 pure materializer

- 기존 parser를 public seam으로만 추출하고 회귀 test를 먼저 고정한다.
- pure input validation, renderer, draft/hash/receipt 타입을 구현한다.
- storage/DB import가 없는 unit test로 결정성과 fail-closed 동작을 증명한다.

종료 조건: 기존 ingestion 결과 무변경, pure tests 통과, NN 보류가 명시적 reason으로 분리됨.

### 단계 2 — synthetic PostgreSQL persistence

- Source read model과 transaction repository를 구현한다.
- advisory lock, provenance revalidation, all-or-nothing insert, exact replay/conflict를 검증한다.
- update/delete 경로를 만들지 않는다.

종료 조건: transaction·rollback·concurrency·idempotency integration tests 통과.

### 단계 3 — 기존 Index 호환 검증

- materialization receipt로 기존 `KnowledgeChunkIdentity`를 구성한다.
- 기존 Index adapter의 source/hash/identity 검증을 그대로 통과시키고 negative mutation tests를
  추가한다.

종료 조건: 별도 compatibility shim 없이 정상/negative integration tests 통과.

### 단계 4 — 서버 gate 해소와 entrypoint

- `#591/#593/#613` actual handoff·mount·role 증거를 확인한다.
- 일반 runtime과 분리된 standalone entrypoint를 활성화한다.
- actual dry-run은 read/parse/hash/계획 출력만 하고 DB write를 하지 않는다.

종료 조건: private 경로·raw text를 노출하지 않는 dry-run receipt와 권한 증거 확보.

### 단계 5 — 승인된 actual persistence

- 승인된 정확한 Snapshot/member 요청만 실행한다.
- commit 후 새 session requery, row count/hash/identity receipt, Index compatibility를 확인한다.
- actual data·server evidence는 지정된 private/approved 위치에만 기록한다.

종료 조건: 지정 reviewer가 Source handoff와 materialization receipt를 승인. 이는 embedding/Index build
완료나 Phase 2 전체 완료를 의미하지 않는다.

각 단계는 별도 작은 PR로 나눌 수 있으나 migration·환경·실데이터를 끼워 넣지 않는다. 구현 중에도
자동 commit/push/원격 게시를 전제로 하지 않는다.

## 13. 미확정 결정과 보류 접점

> 책임 리뷰어가 판단할 항목은 `MFDS_RAG_Phase2A_Plan.md`의 **「미결정 사항 결정 표」(D1~D6)** 가 단일 출처다.
> 아래 표는 그 결정들이 설계 어디에 닿는지를 보여주는 보조 표이며, 선택지를 따로 늘리지 않는다.

| 결정 | 현재 근거 | 보류 범위 | 독립 진행 가능 범위 |
|---|---|---|---|
| Phase 2A의 주 Issue와 reviewer | `#634`; 구현 `@ceohwj`, 단일 책임 reviewer `@phina-io`, specialist `@Jye-rookie` 지정. 실제 review 승인은 아직 없음 | reviewer 승인 전 병합 및 실제 서버 persistence | 설계·로컬 구현·검증 |
| `external_document_id`/locator의 계약 지위 | `#591` precheck의 후보 | DB persistence 활성화 | parser/chunk draft |
| persistence lifecycle gate | Index gate는 존재, materialization 전용 계약 없음 | actual/ACTIVE 저장 | pure draft, synthetic 양방향 test |
| NN 공식 빈 ARTICLE 표현 | parser는 보존, Knowledge schema는 상태 표현 없음 | 해당 NN member 저장 | EE/UD/NB 전체, nonempty NN draft |
| chunk policy 향후 versioning | stable coordinate에 policy version 없음 | 같은 Source version의 rechunk | 고정 v1 및 mutation tests |
| actual 12 Rx/OTC Source handoff | `#591/#593/#613` 미완료 | actual run | 합성 Source fixture |
| standalone server execution identity | builder role policy는 있으나 mount/실행 경계 미확정 | actual entrypoint 배포 | DI 기반 service/repository |
| Proposed 계약의 Current 승격 | 구현·migration/OpenAPI/test/evidence 동반 원칙 | 승격 | Proposed 문서와 구현 증거 준비 |
| `KnowledgeChunk.embedding_model` / `vector_store_key` authoritative owner | 기존 Index adapter가 읽지·쓰지 않고, `knowledge_index_builder`에 두 컬럼 UPDATE 권한이 없다. 값이 관측되는 유일 사례는 `LEGACY_V1` migration 보존 회귀 row | 두 컬럼의 write 경로 설계·확장 | owner와 write 경로 확정 (Backend·DB 책임 리뷰어). Phase 2B 착수 전 | insert NULL + replay 비교 제외 + 기존 값 보존 |
| `SUCCEEDED_WITH_REJECTIONS` 허용 여부 | MFDS label 경로는 `_validate_metadata()`에서 `rejected_record_count == 0`을 강제하므로 이 상태를 만들 수 없다 | enum 존재만으로 허용 입력에 포함 | 실제 MFDS 생성 경로 변경 또는 `@phina-io`의 명시적 계약 결정. Task 3 착수 전 | `SUCCEEDED` 단일 허용 구현 |
| **두 flow의 공유 ordering key 또는 상위 serialization 방식** | 테이블 순서는 Index와 정렬했으나 member 순회 기준이 다르다 (Index `knowledge_chunk_id.bytes` / materialization `SECTION_ORDER`). `FOR UPDATE OF` 테이블 목록이 같다고 실제 row 획득 순서가 같아지지 않는다 | 테이블 순서 일치만으로 deadlock이 배제된다는 판단 | **Task 3 시작 전** `@phina-io`가 전략을 확정해야 한다. Task 4는 확정된 전략을 *검증*하는 단계이며 전략을 *결정*하는 단계가 아니다 | 전략 확정 전에는 Repository lock 구현·mock 순서 검증을 시작하지 않는다 |
| **Artifact reader 오류의 typed boundary** | reader 생성자 오류는 커널 전달 전에 발생하고, `read_verified()` 실패는 크기·checksum·I/O·path 이탈이 모두 구분 없는 `ValueError`다 | 메시지 문자열 비교 없이 3-way reason 분류가 가능하다는 판단 | **결정 표 D1 참조** (`MFDS_RAG_Phase2A_Plan.md`). Revision 12에서 **옵션 B(래핑) 권고를 철회**했다 — 래핑만으로는 크기·checksum·I/O를 구분할 수 없고 구분하려면 금지된 메시지 비교가 필요하다. 이제 **A의 최소 변경**을 권고하며 C(단일 `DEPENDENCY_ERROR`)가 대안이다. `@Jye-rookie` 전문 검토 + `@phina-io` 책임 결정 모두 필요. Task 2b 착수 전 | Task 2a(순수 renderer/chunker, reader 미사용)는 독립 진행 가능 |
| commit 이후 audit을 성공 조건에 포함할지 | 기존 Index는 receipt 비교를 commit 전 같은 transaction에서 끝낸다 | post-commit 불일치를 rollback할 수 있다는 기술 | audit 포함 여부 결정. Task 3 착수 전 | commit 전 receipt 비교 구현 |

### 13.1 `#591` 실데이터 gate — 증거 등급별 분리

`#649`는 **2026-09-16T07:47:32Z에 `2f003eac`로 병합**됐다(이전 Revision의 "open" 표기는 낡았다).
"공개 기록에서 미확인"을 "실제 미인계"로 단정하지 않기 위해 증거를 4등급으로 나눈다.

- **공개 확인**: 저장소 문서·PR·Issue로 직접 읽어 확인했다.
- **제한 인계 자료 존재**: 값이 제한 접근 경로에 보존돼 있음이 공개 기록으로 확인되나, 값 자체는 이 세션에서 보지 않았다.
- **consumer 직접 검증 필요**: `@ceohwj`가 인계 후 재조회로 대조해야 확정된다.
- **미확인**: 위 어디에도 근거가 없다.

| 확인 항목 | 등급 | 근거와 값 |
|---|---|---|
| EE·UD·NB 기대 byte size | **공개 확인** | `novasc-first-ingestion-record.md` 및 `novasc-label-probe-summary.json`: EE 757 / UD 965 / NB 22750 |
| EE·UD·NB 기대 raw SHA-256 | **공개 확인** | 같은 출처: EE `e0481cf0…53eb` / UD `3abaaeea…8bf4` / NB `2970de8f…d29e`. 두 문서의 값이 서로 일치한다 |
| `content_type` / encoding | **공개 확인(수집 시점 관측값)** + **consumer 직접 검증 필요(저장 컬럼 값)** | `novasc-label-probe-summary.json`의 EE·UD·NB 3개 모두 `application/download; UTF-8; charset=UTF-8` (2026-09-15 HTTP probe). **Revision 11에서 "미인계"로 적은 것은 오류이며 정정한다.** 다만 이는 수집 시점 응답 헤더 관측값이고, 저장된 `IngestionArtifact.content_type` **컬럼 값**이 이와 같은지는 대조된 적이 없다. `#649` 미완 항목 "content type/encoding·권한 증빙을 제한된 채널로 전달"은 **저장 측 증빙**을 가리킨다 |
| Snapshot `verification_status` | **미확인** | `#649` 기록이 "이번 출력은 verification_status를 포함하지 않으므로 추정하지 않는다"고 **명시**한다. 제한 인계 자료에 있다는 근거도 없다 |
| EE·UD·NB Member/Artifact 대응 | **제한 인계 자료 존재 + consumer 직접 검증 필요** | `#649`: "개별 Member/Artifact UUID는 이 요약 출력에 없다". 다만 `requery_mfds_label_persistence`가 Member 참조와 각 원문 size·checksum을 reader로 검증하고 통과했으므로, **대응 자체는 서버에서 검증됐고 UUID 값만 미인계**다. `member_count=3`은 공개 확인 |
| Snapshot ID / canonical checksum / source_version | **공개 확인** | `073ee706-d039-49b5-8ca5-e92cd021f087`, `a5df7649…9cd3`, `api:2026-09-16T01:16:39.187000Z:a5df7649…9cd3` |
| lifecycle 상태 (Source/Endpoint/Operation) | **공개 확인** | Source ACTIVE, Endpoint VERIFIED/ENABLED/APPROVED, Operation ENABLED/APPROVED, `registration_ready=true` |
| consumer read-only artifact mount | **미확인 — 준비 안 됨** | `docs/runbooks/source-snapshot-handoff-env-593.md:151`이 "finalizer와 writer read-only·executor delete·**consumer read-only mount**가 준비되고 합성 cleanup E2E가 통과했다"를 **미체크**로 두고 `#613`에서 처리한다고 명시. `#613` 최신 코멘트도 consumer read-only Artifact mount를 **미완료**로 기록한다. 즉 consumer acceptance는 인계 자료 부족이 아니라 **mount 미준비로 아직 실행할 수 없다** |

**이 표의 운용 규칙**

- 위 값들을 **기대값으로 삼아 PASS 처리하지 않는다.** consumer acceptance는 인계받은 기준값과 독립 재조회 값을 대조하는 절차이며, 같은 출처를 양쪽에 놓으면 검증이 아니다.
- 이 `#634` 세션에서 acceptance 도구를 새로 만들지 않고, 다른 agent와 중복으로 서버 검증을 실행하지 않는다. `#591`/`#613`에 이미 준비된 서버 안내·스크립트·절차를 사용한다.
- credential, `consumer.env`, SSH 개인키, raw XML은 모델 입력·출력에 넣지 않는다. 위 표의 size·checksum·content type은 이미 저장소에 공개된 비민감 식별값이다.
- consumer acceptance가 완료돼도 actual persistence, embedding·index·검색·Guide/Chat으로 **자동 진행하지 않는다.** 각 단계는 별도 gate다.

#### 실측 근거로 확정한 설계 판단 (보류 아님)

| 판단 | 실측 근거 |
|---|---|
| `NO_CHANGE` run을 입력으로 허용하지 않는다 | PR `#649`가 재적재 시 `NO_CHANGE` run 생성을 실측했다. 그러나 Snapshot member의 `ingestion_artifact_id`는 최초 `SUCCEEDED` run의 artifact를 계속 가리키므로, `member.ingestion_artifact_id == artifact.id` ∧ `artifact.ingestion_run_id == run.id`를 요구하는 gate에서는 항상 최초 `SUCCEEDED` run에 도달한다. 따라서 `NO_CHANGE` 거절이 정상 경로를 막지 않는다 (§1.4.1) |
| `object_key`는 `raw_checksum`에서 도출되고 **공개 helper가 이미 있다** | `LocalPrivateSourceArtifactStore.object_key_for_checksum(raw_checksum)` (public `@staticmethod`, docstring: "cleanup 요청도 writer와 같은 content-addressed key를 사용하게 합니다")를 그대로 호출한다. `f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"`이고 locator 기반이 아니다. Revision 10에서 "공용 helper 승격이 필요한 미확정 결정"으로 적은 것은 오류였고 **철회한다** — 승격 없이 재사용하면 된다. 인스턴스 생성이 필요 없는 staticmethod이므로 root 검증을 거치지 않고 순수 함수로 쓸 수 있다 |
| receipt 비교는 commit 전 같은 transaction | `persist_complete_index()`가 `session.begin()` 블록 안에서 `_load_and_recompute_receipt()`를 호출한다 |

보류 접점은 fail-open 기본값으로 채우지 않는다. 특히 실제 Source가 있다고 가정하거나,
`document_content_hash`를 canonical hash로 바꾸거나, NN 빈 본문에 합성 문구를 넣지 않는다.

## 14. Phase 2A 완료 정의

Phase 2A 구현 완료는 다음 모두가 fresh evidence로 충족될 때만 주장할 수 있다.

- `#634`에 구현 담당과 단일 책임 reviewer가 명시되고, Proposed materialization 계약에 대한 실제
  reviewer 승인이 있다.
- Source ingestion과 동일 parser·보안 reader를 실제로 재사용한다.
- EE/UD/NB의 Document/Chunk가 결정적으로 생성되고 한 transaction으로 저장된다.
- exact replay, content conflict, rollback, concurrency가 검증된다.
- Document/member hash와 Chunk/text hash가 기존 Index adapter 검증을 통과한다.
- 실제 서버 실행을 주장한다면 `#591/#593/#613` handoff·mount·role 증거와 post-commit requery가 있다.
- NN 미확정 경로가 조용히 누락되지 않고 명시적 보류로 남는다.
- embedding/Index/Search 완료를 Phase 2A 완료와 혼동하지 않는다.

현재 시점의 결론은 **설계 및 로컬 합성 구현 준비 완료, actual Source handoff와 공유 계약 접점은
미확정**이다.
