# Issue #273 HOLDOUT Freeze 준비 설계

## 상태와 결정

- 이슈: `#273`
- 구현 브랜치: `273-holdout-freeze-preparation`
- 구현 담당: 정현우 (`@ceohwj`, `EVALUATION_IMPLEMENTER`)
- Product/Evaluation 검토: 권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)
- 접근 통제·Dataset Custodian 승인 요청 대상: 송은영 (`@phina-io`, `DATASET_CUSTODIAN`)
- 역할 분리 예외: `@phina-io`가 접근 통제를 직접 구현하면 독립 승인은 김지혜 (`@Jye-rookie`)가 담당한다.
- 시작 기준: PR #358 병합 뒤 DEV 60개와 Gold 20개가 승인 provenance에 결속된 상태
- 이 단계의 완료 상태: `PHASE_B2_HOLDOUT_FREEZE_PREPARATION / PREPARATION_READY`

이 단계는 HOLDOUT을 작성하거나 동결하지 않는다. 저장소에는 실제 접근 통제를 만들었다는 주장 대신,
보호 환경 소유자가 이후 구현·승인해야 할 최소 조건과 공개 가능한 증빙 형식만 결정적으로 기록한다.
HOLDOUT 질문·Gold·hard negative·authoring identity·fingerprint·HMAC 값과 보호 위치는 일반 저장소에
들어오지 않는다.

## 목표와 비목표

목표는 다음과 같다.

- HOLDOUT 40개 작성 전에 필요한 접근 통제·감사·역할 분리 조건을 고정한다.
- 실제 접근 승인과 Freeze가 아직 없다는 상태를 기계 검증 가능하게 유지한다.
- 향후 비공개 `StudySplitReceipt`와 공개 가능한 Freeze evidence envelope의 입력 계약을 명시한다.
- 현재 승인된 DEV Dataset manifest와 준비 패킷을 hash로 결속한다.
- 상태와 `report.md`가 Phase B2 준비 상태를 정확히 설명하게 한다.

비목표는 HOLDOUT 콘텐츠 작성, 보호 저장소·계정·키 생성, 접근 권한 부여, 실제 Freeze Receipt 생성,
Retriever Adapter·Runner 구현, 100개 실행, Metric·Baseline·Release 판정, OTC #278 범위다.

## 선택한 구조

`natural_language_retrieval_holdout_preparation.py`가 저장소에 이미 승인된 DEV graph를 읽고 다음 두
artifact를 결정적으로 생성한다.

- `docs/validation/rag/issue-273/holdout-freeze-preparation.json`
- `docs/validation/rag/issue-273/holdout-freeze-preparation.md`

이는 `REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION`이며 공유 runtime schema가 아니다. Schema Set 1.3의
`StudySplitReceipt@1.0.0`은 실제 DEV/HOLDOUT graph와 승인 receipt가 존재하는 보호 환경에서만 만든다.
따라서 이번 PR은 `StudySplitReceipt`, portable JSON Schema, Loader 계약을 변경하지 않는다.

## 공개 준비 패킷 계약

JSON은 다음 의미를 고정한다.

- `purpose=PREPARATION_ONLY`, `preparation_status=PREPARATION_READY`
- `protected_runner_issue_status=NOT_CREATED`와
  `access_control_start_gate=[PROTECTED_RETRIEVAL_RUNNER_ISSUE_CREATED]`
- 현재 DEV Dataset ref와 manifest SHA-256
- `planned_holdout_questions=40`, 5개 주제별 정확히 8개
- 네 leakage 축: `question_template`, `source_segment`, `medication_family`, `transform_origin`
- 최소 권한, 기본 거부, 일반 개발자·일반 CI 접근 금지, 감사 이벤트 요구
- 구현 담당·검토자·Custodian의 분리와 구현자/Custodian 충돌 시 대체 승인자
- 실제 작성 시작 조건과 실제 Freeze 시작 조건
- 보호 환경 안에서 생성할 비공개 `StudySplitReceipt` 필드 요구사항
- Freeze 뒤 저장소에 반입할 수 있는 공개 evidence envelope의 허용 필드
- 아직 충족되지 않은 승인·Freeze·Adapter·Runner blocker
- 패킷 self-hash

공개 evidence envelope는 향후 실제 receipt의 `id`, `version`, raw file SHA-256, actor, UTC timestamp,
DEV/HOLDOUT 수량과 네 축의 `intersection_count=0`만 받을 수 있다. raw query, Gold, record label,
fingerprint/HMAC 값, key material, 보호 저장 위치는 받을 수 없다.

## 접근 통제와 역할 분리

보호 환경 구현은 다음 조건을 모두 충족해야 한다.

1. 기본 접근은 거부하며 명시적으로 승인된 작성자·Custodian·보호 Runner service identity만 접근한다.
2. 일반 개발 checkout, 일반 CI, PR artifact, Issue 본문과 로그에서는 HOLDOUT 콘텐츠를 읽을 수 없다.
3. grant, revoke, read, write, freeze, runner execution을 actor·UTC timestamp·대상 logical ID와 함께 감사한다.
4. 구현 담당자는 자신의 접근 통제 구현을 최종 승인할 수 없다.
5. Dataset Custodian은 콘텐츠 작성 완료 후 변경 불가 snapshot과 receipt hash를 검토한다.
6. key material과 실제 HMAC 값은 보호 환경 밖으로 내보내지 않는다.

이번 준비 패킷은 위 조건을 정의할 뿐 조건 충족 증빙이 아니다. 따라서 `access_authorized=false`,
`holdout_authored=false`, `freeze_recorded=false`를 고정한다.

## 단계 전이

1. **이번 PR:** 준비 패킷을 생성하고 `PREPARATION_READY`로 기록한다. HOLDOUT은 0개이며 Freeze는 `NOT_STARTED`다.
2. **전용 Issue 생성:** 이번 준비 PR 병합 직후, 접근 통제 구현이나 HOLDOUT 작성 전에 #273 전용 protected Retrieval Runner Issue를 생성하고 구현 담당자와 Product·Safety·Privacy 책임 리뷰어를 명시한다.
3. **보호 환경 작업:** 전용 Issue에서 접근 통제 구현자가 ACL·감사·service identity를 구성한다.
4. **접근 승인:** 독립 Dataset Custodian이 실제 통제 증빙을 검토하고 immutable authorization event를 만든다.
5. **HOLDOUT 작성 PR이 아닌 보호 작업:** 승인 event 이후에만 한국어 HOLDOUT 40개와 Gold를 보호 환경에서 작성한다.
6. **Freeze:** 네 leakage 축 교집합 0을 보호 환경에서 검증하고 비공개 `StudySplitReceipt`와 Freeze Receipt를 생성한다.
7. **공개 provenance PR:** 비민감 receipt envelope와 hash만 저장소에 기록한다.
8. **실제 평가:** #178 Adapter와 보호 Runner가 준비된 뒤에만 DEV 60 + HOLDOUT 40을 실행한다.

준비 계약과 `PREPARATION_READY` 상태 전이는 #273에 남기고, protected root loader·실행 승인·접근 audit
구현은 기존 #273 정본 설계가 요구하는 전용 후속 Issue가 소유한다.

## 실패 조건

다음 중 하나라도 있으면 준비 artifact 생성을 거부한다.

- DEV manifest가 승인된 정확한 Dataset ref/hash와 다르다.
- DEV Dataset이 `DRAFT`가 아니거나 HOLDOUT count가 0이 아니다.
- 주제별 HOLDOUT 계획이 8개씩, 합계 40개가 아니다.
- 필수 leakage 축이 누락·중복·재정렬된다.
- 구현자·검토자·Custodian이 동일하거나 대체 승인 규칙이 사라진다.
- `access_authorized`, `holdout_authored`, `freeze_recorded` 중 하나가 `true`다.
- 공개 artifact에 query, Gold body, record label, fingerprint/HMAC 값, key/credential, 보호 위치가 들어간다.
- 실제 실행 또는 Metric·Release 필드가 추가된다.
- 전용 protected Retrieval Runner Issue가 생성되지 않았는데 접근 통제 시작 gate가 충족된 것으로 표시된다.

## 검증과 완료 경계

- 준비 패킷 JSON과 Markdown이 fresh build와 byte-for-byte 일치한다.
- Phase B2 status와 report가 패킷 raw SHA-256과 내부 self-hash를 구분해 결속한다.
- DEV 60, HOLDOUT 0, Dataset `DRAFT/unfrozen`, HOLDOUT Freeze `NOT_STARTED`, actual run 없음, `release_eligible=false`를 유지한다.
- 보호 Runner, #178 Adapter, 접근 승인, HOLDOUT Freeze가 모두 blocker로 남는다.
- 관련 pytest, 전체 evaluation pytest, Ruff, Mypy, `git diff --check`를 통과한다.

완료는 HOLDOUT 접근 승인, 작성, Freeze, actual baseline 또는 Release 준비를 뜻하지 않는다.
