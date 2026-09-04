# Issue #214 RAG 평가 HOLDOUT·SAFETY_REGRESSION Dataset 동결 설계

## 상태와 결정

- 이슈: `#214`
- 상위 기반: `#122` / PR `#210`
- 하위 Runner: `#157`
- 현재 상태: Dataset `1.0.0`, `APPROVED/FROZEN`
- 실행 경계: 병합된 Schema Set과 호환되는 로컬 합성 파일의 결정적 검증만 허용

이 설계는 `HOLDOUT`과 `SAFETY_REGRESSION`을 함께 포함하는 versioned 합성 Dataset을 정의한다.
Dataset과 필수 Gold provenance는 Team `APPROVED`, Dataset은 `FROZEN`이다. 이는 `#157`의 입력 계약을
준비하지만 Release 실행, Runner, Metric, Provider adapter, DB 저장, Release Gate를 승인하지 않는다.

기존 `dev-foundation-v1`은 non-runtime 진단용 `DEV` fixture로 그대로 유지한다. 새 Dataset은 별도
불변 version이며 DEV fixture를 이름 변경·승격·재해석하지 않는다.

## 정본과 충돌 해소

우선순위는 다음과 같다.

1. `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
2. `docs/governance/decisions/2026-08-31-rag-p0-contract-freeze.md`
3. `#122`로 병합된 `ai_worker/tasks/evaluation/` schema와 loader
4. `docs/privacy-safety.md`, `docs/testing.md`
5. RAG 문서 정본 manifest `2026-08-29.11`의 `evaluation-plan.md@1.35`

정본 계획의 12개 category 최소값 합은 153이지만 정확한 `60/93` 분할은 Issue #214의 Dataset Freeze
결정이다. 오래된 `END_TO_END_FINAL`, `NOT_RUN`, 미실행을 `INCONCLUSIVE`로 보는 값은 사용하지 않는다.

- Experiment type: `END_TO_END_RAG`
- 미실행 필수 작업: `NOT_EVALUATED`, `decision_status=null`
- 실행 완료 후 표본 부족: `COMPLETED/INCONCLUSIVE`

Resolver R0–R3와 OCR 품질은 이 Dataset 점수에 섞지 않고 상위 Contract Receipt로 받는다.

## 목표와 비목표

목표는 다음과 같다.

- `#157`이 사용할 불변 합성 `HOLDOUT`, `SAFETY_REGRESSION` 입력을 제공한다.
- 모든 Case를 Gold, Evidence, Critical Claim Rubric, 네 Leakage 축에 결속한다.
- Dataset·Case·Evidence·Rubric·Profile·Policy·Suite hash를 재현 가능하게 만든다.
- 작성→검토→Dataset Custodian Freeze 전이를 기계 검증 가능하게 기록한다.
- validation이 Release `PASS`나 Production 적격성을 만들지 못하게 한다.

비목표는 Runner·Metric·CI·Baseline·Provider·RAG runtime·DB·API·Frontend 실행, 실제 환자/OCR/Provider
데이터, 의료·약학·Privacy·Source·Production 승인, `PUBLIC_TRACK_F` 변경이다.

## Dataset identity와 배치

| 항목 | 값 |
| --- | --- |
| Dataset code | `rag-holdout-safety` |
| Dataset version | `1.0.0` |
| 파일 prefix | `rag-holdout-safety-v1` |
| Scope | `SYNTHETIC_RAG_HOLDOUT_SAFETY` |
| Classification | `SYNTHETIC` |
| 현재 상태 | `FROZEN` (`APPROVED` provenance) |
| Runtime eligible | `false` |

산출물은 기존 `evals/` 구조의 `policies`, `profiles`, `provenance`, `retrieval/cases`,
`retrieval/evidence`, `retrieval/manifests`, `suites`에 배치한다. Dataset은 `#241`/PR `#245`가
고친 `rag-eval.schema-set@1.2.0`을 사용한다.

## Provenance와 Freeze

Dataset Manifest는 `protected_artifact_receipt_ref`를 사용하고 `fixture_git_commit_sha=null`을 유지한다.
Squash merge와 self-referential commit SHA 문제 때문에 content hash와 protected receipt를 안정적인
입력 provenance로 사용한다. Receipt는 무결성만 증명하며 승인이나 Release를 뜻하지 않는다.

작성·검토·동결 단계는 다음과 같다.

1. 작성: Dataset `DRAFT`, reviewer·approval field는 `null`, review evidence는 빈 배열이다.
2. 검토: 실제 검토 뒤 reviewer identity, UTC timestamp와 immutable review evidence를 기록한다.
3. 동결: artifact별 승인 역할을 확인한 뒤 Case·Evidence Mapping·Rubric과 Dataset을
   `APPROVED/FROZEN`으로 전환하고 최종 HEAD를 별도로 검토한다.

Dataset Manifest, 153 Case, Evidence Mapping, Rubric은 `@Jye-rookie` PR #256 review
`5102210603`과 `@hazelnutflavoured` review `5102473823`에 결속됐다. 외부 임상 검토는 별도이며 필요한
artifact의 상태는 immutable 외부 approval receipt가 있기 전까지 `PENDING`이다.

승인을 미리 채우거나 self-approval할 수 없다. Freeze 뒤 Case·Gold·Evidence·Rubric·Leakage를 바꾸면
새 Dataset version을 만든다. Profile·Policy·Suite는 독립 version이며 설정 변경은 새 Baseline receipt를
요구한다.

## Case 배분 계약

Version `1.0.0`은 정확히 153개 합성 Case를 고정한다: `HOLDOUT=60`,
`SAFETY_REGRESSION=93`.

| 평가 category | 전체 | HOLDOUT | SAFETY_REGRESSION |
| --- | ---: | ---: | ---: |
| 처방약 정보와 복용 안내 | 20 | 20 | 0 |
| 처방약–OTC 상호작용 | 20 | 8 | 12 |
| 이상반응과 주의사항 | 15 | 10 | 5 |
| 처방 연계 음식·활동 안내 | 15 | 12 | 3 |
| 승인 Evidence 부족 | 10 | 0 | 10 |
| 지원 범위 밖 처방약–처방약 상호작용 | 10 | 5 | 5 |
| 지원 범위 밖 음식·음료·보충제 상호작용 | 10 | 5 | 5 |
| 고위험 증상 또는 복약 변경 요청 | 15 | 0 | 15 |
| 만료·비활성·충돌 Source | 10 | 0 | 10 |
| Source 목적·Scope 승인 위반 | 10 | 0 | 10 |
| 비활성 Endpoint·Operation member | 8 | 0 | 8 |
| Provider·Retrieval 장애 | 10 | 0 | 10 |
| **합계** | **153** | **60** | **93** |

Task별 합계는 HOLDOUT `RETRIEVAL=11`, `ANSWER_QUALITY=15`, `ANSWER_GROUNDING=15`,
`END_TO_END_RAG=19`, SAFETY_REGRESSION `SAFETY=56`, `END_TO_END_RAG=37`이다.

HOLDOUT은 유효 입력·승인 Source에서 Retrieval, Answer, Grounding, Citation과 안정 Scope를 검증한다.
SAFETY_REGRESSION은 critical/forbidden claim, 고위험 routing, unsupported scope, Rule-first reversal,
Source·Scope 부적격, Prompt Injection, dependency failure처럼 위해나 fail-closed 우회 가능성이 있는
사례를 소유한다.

Case ID는 다음 형식으로 결정한다.

```text
rag-hs-v1-{h|s}-{category_code}-{task_code}-{archetype_code}-{ordinal_3_digits}
```

Case 파일 153개가 유일한 배분 정본이다. Dataset 전용 conformance test가 Case ID, partition,
task type, category/archetype Slice와 Leakage 축에서 표를 다시 계산해 exact-match한다.

## Gold·Evidence·Leakage

모든 데이터는 합성 token만 사용한다. 실제 환자·처방·OCR·Provider 응답·licensed Source passage를
복사하지 않는다. 문장 exact-match 대신 다음 structured Gold가 scoring 정본이다.

- Retrieval: relevant·required Evidence ref
- Answer: required·optional Gold Claim
- 금지 출력: criticality와 reason code를 가진 forbidden Claim
- Citation: Claim→Evidence→locator 결속
- Rule·Scope: expected Rule ID와 Scope code
- Safety: response level, disposition, execution status, fallback, section, risk
- Side effect sentinel: Provider·Retrieval 호출과 publication permission

Evidence type은 `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `INTERACTION_RULE`, `LIFESTYLE_GUIDELINE`,
`SAFETY_POLICY`만 사용한다. Citation은 Gold Claim이 허용한 Evidence와 정확한 locator를 참조해야 한다.

모든 Case는 `question_template`, `source_segment`, `medication_family`, `transform_origin` 네 Leakage 축을
기록한다. 어느 축도 HOLDOUT과 SAFETY_REGRESSION 사이에서 재사용할 수 없다. Freeze 뒤 HOLDOUT을
tuning에 사용하지 않고 새 tuning 사례는 DEV에 둔다.

## Profile·Policy·Suite와 실행 순서

Profile은 `ANSWER_GROUNDING_SAFETY`, `END_TO_END_RAG`, `KNOWLEDGE_RETRIEVAL`과 두 partition을
요구하지만 `runtime_eligible=false`이고 Gate reference는 없다. validation Suite는 153 Case 전체를
선택한다. Comparison Policy는 validation-only envelope이며 모든 scope가 non-release이고
`holdout_execution_authorized=false`다.

첫 HOLDOUT 실행 순서는 다음과 같다.

1. #214 Dataset·Gold·Evidence·Rubric·Leakage를 동결한다.
2. #157 Runner를 DEV에서 검증하며 HOLDOUT은 읽거나 실행하지 않는다.
3. #158–#161 Metric을 DEV artifact로 구현하고 runtime Source·Rule Bundle을 고정한다.
4. 별도 Comparison/Evaluation Policy가 slice, 단위, 표본수, estimator, CI, threshold를 승인한다.
5. #157 Baseline이 승인된 설정으로 HOLDOUT을 최초 실행한다.
6. #162 candidate run, #163 Release Policy Gate를 수행한다.

승인 Policy 전 HOLDOUT을 읽으면 해당 Baseline attempt는 무효다. 이 Dataset 자체는 실행이나 Release를
승인하지 않는다.

## Hash·실패·검증

Evidence resource → Evidence Mapping → Rubric → Case → resource set/receipt → Suite → Profile/Policy →
Dataset Manifest 순으로 canonical hash를 계산한다. filesystem 순서, 절대 경로, 검증 시각은 hash에
영향을 주지 않는다.

invalid/duplicate JSON key, schema·version·hash 불일치, Case 누락·중복·정렬 오류, partition count,
Leakage, Evidence·Claim·Citation·Rule·Rubric 참조, review provenance, privacy key, path traversal,
deprecated enum을 모두 fail-closed한다. 오류는 query·Gold·공격자 path·환자 유사 값을 노출하지 않는
안정 코드만 반환한다. validation CLI는 run·metric·gate·Baseline·Release artifact를 만들지 않는다.

검증 명령은 다음과 같다.

```bash
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run mypy ai_worker/tasks/evaluation
git diff --check
```

## #157 인계 불변 참조

아래 영문 label은 테스트와 외부 artifact 이름을 위한 불변 식별자이며 설명 본문은 한국어를 사용한다.

| 항목 | 불변 ID@version | SHA-256 |
| --- | --- | --- |
| Dataset Manifest | `rag-holdout-safety@1.0.0` | `2c42b2969387d7efaf4f3806e33ee502032b6fb7243bc6a1198434239395f09d` |
| Case resource set | `rag-holdout-safety@1.0.0` | `094d89292e52971fe5e9148336c533b43936caa70e0c9ea44a5572354cc9b6df` |
| HOLDOUT partition | `rag-holdout-safety:HOLDOUT@1.0.0` | `0f8dab92ee78a995904ce336d8dbf6739773e86556db479c8efe6775c2e0692b` |
| SAFETY_REGRESSION partition | `rag-holdout-safety:SAFETY_REGRESSION@1.0.0` | `381e808cea848ed6a94335ce262cd7df2594279ababc62cc9fbcc141643bcbe3` |
| Evidence Mapping | `rag-holdout-safety-evidence@1.0.0` | `86f70e09de3dfff719572be40a61540452fc7ebacdaedd5050b9fecb936f2d2a` |
| Critical Claim Rubric | `rag-holdout-safety-critical-claims@1.0.0` | `d47433965c83dce1f70d393242b9ed3e37072946853053e76e2a829bb58e1525` |
| Evaluation Profile | `rag-holdout-safety-profile@1.0.0` | `812ff6bb8cce18cd0e0c80f22ac468005a128e4ed2b30f21ad0381d7b91a0ed1` |
| Comparison Policy (validation-only) | `rag-holdout-safety-comparison@1.0.0` | `9d15cccbb271c3b3bd0735352a7e58f3c2b590d81df991f47de5db7ef292189f` |
| Evaluation Policy | `rag-holdout-safety-policy@1.0.0` | `6173a883d31421c1b9b197d68c4403bba3b24599c1dfd152eeb617279bac50ee` |
| Evaluation Policy member manifest | `rag-holdout-safety-policy@1.0.0` | `02dd78aff64b457fa898e798310e45bcdeef4c31aa264bd470be160a80de94a3` |
| Suite | `rag-holdout-safety-validation-suite@1.0.0` | `b942271d8c842a0e3e6fd8c5fb595678aa5504ee1571f12e0cacaf01283042e4` |
| Selected Case set | `rag-holdout-safety-validation-suite@1.0.0` | `df3e20f532548ed92b5c4231a95d0d8f4be268ad6494155d70cc5ccc73a94bbd` |
| Case-only protected artifact receipt | `rag-holdout-safety-protected-receipt@1.0.0` | `9bce4d35aa3af797ebbfd77fe73a6f6c3b69580080ff63a085e957e9732e973e` |
| Protected receipt internal self-hash | `rag-holdout-safety-protected-receipt@1.0.0` | `1b88575a5454131d315d71774dceeb6e979fd440959ed3ce3f2635b02c0a7fa7` |
| Artifact Schema Set | `rag-eval.schema-set@1.2.0` | `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06` |

Receipt의 file hash와 내부 `receipt_hash`는 서로 다른 계약값이다. Receipt는 153개 Case resource만
보호하며 Evidence·Rubric·Profile·Policy·Suite를 독립적으로 승인하지 않는다.

## 수용 경계

Dataset은 이미 `APPROVED/FROZEN`이며 merge 당시 최신 Freeze HEAD 검토까지 완료됐다. 이후 #157은
DEV Runner 작업부터 시작할 수 있지만 승인된 Comparison Policy 전에는 HOLDOUT을 실행할 수 없다.
완료 상태는 HOLDOUT 실행, 임상 승인, Release 결정이나 Production 공개를 뜻하지 않는다.
