# AI 평가

의료 AI의 재현 가능한 평가 사례와 결과를 관리하는 영역입니다. 실제 환자 데이터는 포함하지 않고, 비식별 합성·공개 허용 데이터와 출처·버전을 기록합니다.

현재 저장소에는 OCR 엔진 비교·측정 자료가 `tests/evals/ocr/`에 있습니다. 아래 `evals/` 하위 영역은 Post-MVP 평가 체계의 준비 디렉터리이며, 생성·안전·RAG·OTC 평가가 현재 MVP의 자동 배포 게이트로 구현된 상태는 아닙니다.

## 영역

- `ocr/`: 약품명, 용량·단위, 횟수·복용 시점, 저신뢰 검토 요청
- `retrieval/`: Recall@K, 출처 버전, 검색시간과 선택 근거
- `generation/`: 처방 일치, Citation coverage, Faithfulness와 안전한 거절
- `safety/`: 응급·중대한 약물 위험 Recall, 금지된 복용 변경 권고
- `otc/`: 성분 매칭, 중복·상호작용 탐지, 정보 부족 Fallback

Post-MVP 평가 기능을 배포 게이트로 전환할 때는 결과에 데이터셋, 모델, 프롬프트, 검색 인덱스와 임계값 버전을 함께 기록합니다. 합의된 임계값, 재현 가능한 실행 명령과 CI 연결이 완료된 항목만 자동 배포 차단 기준으로 사용합니다.

자동 평가 체계가 아직 없다는 이유로 의료 안전 검증을 통과한 것으로 간주하지 않습니다. 현재 운영 가능 여부는 `SECURITY.md`, `docs/privacy-safety.md`와 `docs/deployment.md`의 수동 승인·차단 기준을 따릅니다.

## RAG foundation 계약 검증

다음 명령은 Issue #122의 합성 DEV dataset과 연결된 schema, hash, provenance, privacy 경계를 검증하고 별도의 validation receipt만 기록합니다.

```bash
uv run python -m ai_worker.tasks.evaluation validate \
  --manifest evals/retrieval/manifests/dev-foundation-v1.dataset.json \
  --result evals/validation-results/dev-foundation-v1.validation.json
```

이 명령은 validation 전용입니다. Evaluation Run, Metric, Gate, PASS/FAIL, Markdown report를 생성하지 않고 Provider를 호출하지 않으며 `PUBLIC_TRACK_F`를 변경할 수 없습니다. 기존 result나 lock은 덮어쓰거나 자동 삭제하지 않습니다. 생성되는 `evals/validation-results/` 파일은 로컬 검증 산출물이며 Git 추적 대상이 아닙니다.

## RAG 합성 DEV Runner·Reporter

Issue #157의 `run-dev` 명령은 versioned execution request와 합성 DEV Dataset graph를 검증한 뒤
`evals/results/<run-id>/`에 기계 Artifact와 비정본 Markdown report를 발행합니다. 세 Experiment Type은
각각 다음 명령으로 실행합니다.

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json \
  --run-id 123e4567-e89b-42d3-a456-426614174000 \
  --executed-by ceohwj
```

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/dev-foundation-answer-grounding-safety-v1.execution.json \
  --run-id 123e4567-e89b-42d3-a456-426614174001 \
  --executed-by ceohwj
```

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/dev-foundation-end-to-end-rag-v1.execution.json \
  --run-id 123e4567-e89b-42d3-a456-426614174002 \
  --executed-by ceohwj
```

이 결과는 로컬·CI의 DEV infrastructure evidence이며 Release `PASS`, HOLDOUT 승인 또는 Baseline Freeze가
아닙니다. 실제 Provider Adapter는 등록하지 않으므로 production 명령의 미구현 결과는
`NOT_IMPLEMENTED/null`로 기록되며 성공 판정으로 해석하지 않습니다. 합성 retrieval replay Adapter와 Metric은
아래 #158 전용 DEV workflow에서만 사용합니다.

- `run-dev`는 `HOLDOUT`·`SAFETY_REGRESSION`을 load·execute·observe할 수 없습니다.
- 기존 Run ID와 lock은 덮어쓰거나 자동 삭제하지 않습니다. 재실행에는 새 canonical UUID Run ID가 필요합니다.
- #158~#161 DEV Metric과 승인된 Comparison/Evaluation Policy가 준비되기 전 HOLDOUT 단계는
  `WAITING_FOR_APPROVED_COMPARISON_POLICY`입니다.
- `--baseline-run-id`가 없는 기존 DEV 명령은 `comparison.json`을 생성하지 않습니다. `gate.json`과
  `baseline-freeze-receipt.json`은 어떤 `run-dev` 명령으로도 생성하지 않습니다.
- `evals/results/`의 실행 결과는 Git 추적 대상이 아닙니다.

### RAG Retrieval 합성 DEV Baseline·Candidate

Issue #158의 다음 명령은 동일한 합성 DEV Dataset과 평가 Policy에서 `RET-L` lexical replay Baseline과
`RET-HR` hybrid-plus-rerank replay Candidate를 순서대로 실행합니다. 실행 전 저장소가 clean 상태여야 하며,
예시 Run ID가 이미 존재하면 기존 결과를 삭제하거나 덮어쓰지 말고 새 canonical UUID를 사용해야 합니다.

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/rag-retrieval-dev-ret-l-v1.execution.json \
  --run-id 15800000-0000-4000-8000-000000000001 \
  --executed-by ceohwj
```

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/rag-retrieval-dev-ret-hr-v1.execution.json \
  --run-id 15800000-0000-4000-8000-000000000002 \
  --executed-by ceohwj \
  --baseline-run-id 15800000-0000-4000-8000-000000000001
```

각 Run Bundle은 `evals/results/<run-id>/`에 저장됩니다. Baseline은 7개 파일, Candidate는 Baseline과의
`comparison.json`을 포함한 8개 파일을 가집니다. 다음 read-only 명령은 runtime Schema, content manifest와
파일 hash를 검증하고 성공 시 semantic content hash 한 줄만 출력합니다.

```bash
uv run python -m ai_worker.tasks.evaluation verify-result \
  --run-id 15800000-0000-4000-8000-000000000001
uv run python -m ai_worker.tasks.evaluation verify-result \
  --run-id 15800000-0000-4000-8000-000000000002
```

이 Run Bundle은 Git 비추적 로컬·CI DEV Artifact입니다. CI에서 보존할 때는
`rag-evaluation-<run-id>` Artifact 이름을 사용하며 소스 PR에 결과 파일을 commit하지 않습니다. Candidate를
검증할 때는 참조 baseline bundle도 같은 `evals/results/` root에 있어야 하므로, 두 Artifact를 따로 보존했다면
각 Run ID 디렉터리를 같은 root 아래 복원한 뒤 `verify-result`를 실행합니다. 비교 delta와
`INCONCLUSIVE` 판정은 진단 evidence일 뿐이고, 승인된 Release threshold가 없으므로 HOLDOUT Baseline Freeze가
아닙니다. 또한 Release `PASS`, 임상적 유효성, Privacy·Source·Production 승인을 의미하지 않습니다. report의
`SYNTHETIC_REPLAY_DEV`, HOLDOUT `NOT_PERFORMED`, production integration
`BLOCKED_BY_RAG_07A_07B_OR_08` 경계를 유지합니다.

### Issue #273 자연어 Retrieval DEV authoring

`rag-natural-language-retrieval-dev@1.0.0`은 실제 환자 발화나 운영 traffic에서 수집하지 않은 한국어 자연어
합성 DEV 질문 60개를 담은 `DRAFT` Dataset이다. 다섯 Topic, 여섯 Expression 유형, 20개 독립
`transform_origin` group과 합성 Gold 20개를 가지며, study-wide 합성 corpus는 Gold 20개와 hard negative
80개로 구성된 100개 record다. 60개 Case, Evidence Mapping, Dataset Manifest의 Gold provenance는 PR #341의
실제 리뷰 이벤트와 PR #354의 Dataset Custodian 승인 이벤트에 결속된 `APPROVED`다. Dataset은 계속 `DRAFT`이며
Dataset Freeze와 HOLDOUT Freeze는 아직 이루어지지 않았다. Dataset
Manifest의 canonical self-hash는
`b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`이다.

HOLDOUT Freeze 준비는 `PREPARATION_READY`다. 이는 접근 승인이나 Freeze 완료를 뜻하지 않는다. 전용
Protected Retrieval Runner Issue #368은 `CREATED`이고 인프라 독립 policy foundation은 `IMPLEMENTED`다.
다만 이는 synthetic adapter로 역할·승인·감사·revocation, 성공 결과 멱등 반환과 UNKNOWN 자동 재실행 차단을
검증한 상태일 뿐이다. 독립 승인 reconciliation adapter와 실제 인프라 enforcement·adapter는
`NOT_IMPLEMENTED`다. 공개 저장소에는 비민감 상태와 증빙만 있고
HOLDOUT 질문·Gold·fingerprint/HMAC 값·credential·보호 위치는 없다. `@phina-io`가 database/schema,
역할, protected credential 환경, append-only audit와 보존 정책을 검토·승인한 뒤 후속 adapter를 연결한다.
독립 Dataset Custodian의 실제 접근 승인 event가 기록된 뒤에만 보호 환경에서 HOLDOUT 40개 작성을 시작하고,
네 leakage 축의 교집합 0과 전수 검토가 끝난 뒤에만 Freeze한다.

검색 대상 artifact와 평가 라벨은 분리되어 있다. `synthetic-knowledge-index.json`의 `records`는
`evidence_ref_id`·`statement`·`product_code`·`topic`·`content_sha256`만 담으며, `record_kind`·
`negative_type`·`adversarial_for_transform_origin`·`transform_origin`은 같은 디렉터리의
`evaluation-labels.json`에만 있다. `record_kind` 하나로 corpus 100건에서 Gold 20건을 그대로 골라낼 수
있으므로, 이 라벨이 색인 대상에 남으면 후속 Adapter가 내용이 아니라 정답 표시로 Gold를 구분해 Recall·MRR이
무효가 된다. 색인 파일은 sidecar를 `evaluation_label_ref`로 hash 결속하므로
`Dataset manifest → Evidence Mapping → 색인 → 라벨` 사슬은 그대로 검증 가능하다.

다만 Loader는 sidecar를 읽지 않는다. 전역 schema를 바꾸지 않는 범위에서 sidecar를 Dataset Manifest나
Protected Artifact Receipt에 등록할 자리가 없기 때문이다. 따라서 sidecar 무결성은 `load_dataset()`이 아니라
`ai_worker/tests/evaluation/`의 fixture·report 테스트가 고정한다. sidecar를 Loader 계약에 편입하려면 Schema
Set 확장이 필요하며 이는 후속 작업이다.

이 `APPROVED`는 60개 DEV Case, Evidence Mapping, Dataset Manifest의 review provenance에 한정된다. Dataset
lifecycle은 계속 `DRAFT`이며 Dataset Freeze와 HOLDOUT Freeze는 완료되지 않았다. 실제 Knowledge Evidence
Retrieval Adapter `knowledge-evidence-retrieval.actual.v1`은 이제 `IMPLEMENTED`이고, DEV 60개 Case에 대해
RET-L·RET-D·RET-H를 각각 2회씩 실제로 실행한 actual retrieval Run과 diagnostic Metric이 존재한다. 이 결과는
`DIAGNOSTIC_ONLY` 관찰값이며 threshold나 baseline 승인으로 전이하지 않았다. 따라서 이 DEV Dataset은 여전히
Release `PASS`를 만들 수 없고 Production 공개 근거가 아니다. HOLDOUT 질문 본문은 저장소에 없으며, 접근
승인·protected runner·HOLDOUT Freeze는 후속 차단 조건으로 남아 있다. 증상 기반 OTC 후보·
상호작용 평가는 별도 Issue #278 범위이며 #273을 차단하지 않는다. 현재 기계 상태와 결정적 Markdown
projection은 `docs/validation/rag/issue-273/`에 있다. 상세 수치·hash·재현성 증빙은 여기에 중복하지 않고
날짜별 experiment log가 담당하며, 목록은 `docs/validation/rag/issue-273/experiments/README.md`에서 본다.
후속 재실험은 기존 로그를 덮어쓰지 않고 같은 디렉터리에 새 날짜 파일로 추가한 뒤 그 index에 한 줄을 더한다.

### Evaluation Schema Sets

- `evals/schemas/1.0.0/`: Issue #122의 기존 DEV foundation 계약. canonical bytes와 loader 동작을 유지한다.
- `evals/schemas/1.1.0/`: Issue #216의 18-member implemented candidate. Case·Dataset Manifest는 member `1.1.0`, 나머지 16개 member는 `1.0.0`을 byte-for-byte 재사용한다.
- `evals/schemas/1.2.0/`: Issue #241의 review provenance compatibility 계약. Case·Manifest·Evidence Mapping·Rubric·Profile·Suite·Evaluation Policy·Protected Artifact Receipt 8개 member는 `1.2.0`, 나머지 10개 member는 이전 canonical bytes를 재사용한다.
- `evals/schemas/1.3.0/`: Issue #273의 `Candidate · Review Required` provenance 확장. Dataset Manifest만 member `1.3.0`으로 교체하고 1.2의 나머지 17개 member를 byte-for-byte 재사용하며, Authoring Identity Manifest·Index Build Receipt·Study Split Receipt를 member `1.0.0`으로 추가한 21-member 후보이다.
- `evals/schemas/1.4.0/`: Issues #160·#161의 `Candidate · Review Required` Grounding/Safety projection 확장. 1.3의 21개 member를 byte-for-byte 재사용하고 Claim–Citation Observation·Grounding Signal을 member `1.0.0`으로 추가한 23-member 후보이다.

Schema Set `1.1.0`의 불변 참조는 `rag-eval.schema-set@1.1.0`, SHA-256 `5cfb113e45a4c333fef05830b0d7c2401975ce66b53dc68ff054b08ba79822c0`이다. #216/PR #222에서 승인·병합된 초기 호환성 계약이다.

Schema Set `1.2.0`의 불변 참조는 `rag-eval.schema-set@1.2.0`, SHA-256 `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06`이다. DRAFT artifact는 reviewer identity·timestamp·review evidence를 기록하지 않으며, 실제 팀 검토부터 `reviewed_by.role=EVALUATION_REVIEWER`와 immutable review evidence를 기록한다. 이 내부 역할은 외부 의료 검토가 아니며 #214 Dataset Freeze 승인 입력은 지정 책임 리뷰 승인 전까지 계속 후보 상태다.

Schema Set `1.3.0` 후보 참조는 `rag-eval.schema-set@1.3.0`, SHA-256 `ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325`이다. 상태는 `Candidate · Review Required`이며, 책임 리뷰어 권가빈 (`@hazelnutflavoured`)의 실제 Pull Request review event가 승인 전환에 필요하다. 기존 Schema Set과 exporter 기본 version은 변경하지 않는다.

Schema Set `1.4.0` 후보 참조는 `rag-eval.schema-set@1.4.0`, SHA-256 `0f6b69b460af5ea840e009f55b86256942f896be324c7885d709883600799e98`이다. 상태는 `Candidate · Review Required`이며, 책임 리뷰어 김지혜 (`@Jye-rookie`)의 실제 Pull Request review event가 승인 전환에 필요하다. 신규 두 artifact는 Evaluation projection contract만 구현하며 #160·#161 metric kernel, Runtime, HOLDOUT/SAFETY_REGRESSION, Baseline Freeze, Release와 공개는 포함하지 않는다. 기존 Schema Set과 exporter 기본 version은 변경하지 않는다.

Schema Set `1.5.0` 후보 참조는 `rag-eval.schema-set@1.5.0`, SHA-256 `cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8`이다. 상태는 `Candidate · Review Required`이며, 책임 리뷰어 권가빈 (`@hazelnutflavoured`)의 실제 Pull Request review event가 승인 전환에 필요하다. 신규 세 artifact는 #159 DEV metric 입력 및 paired comparison manifest contract만 구현하며 human judgment consumption, ANSWER_CORRECTNESS/RELEVANCE scorer, comparison builder, Runtime, HOLDOUT, Baseline Freeze, Release와 공개는 포함하지 않는다. 기존 Schema Set과 exporter 기본 version은 변경하지 않는다.

여섯 버전은 다음 명령으로 별도 출력한다. 기본값은 하위 호환을 위해 `1.0.0`이다.

```bash
uv run python -m ai_worker.tasks.evaluation.schema_exports \
  --output /tmp/rag-eval-schemas-1.5.0 \
  --schema-set-version 1.5.0
```

## RAG HOLDOUT·SAFETY_REGRESSION Dataset Freeze

`dev-foundation-v1`은 구현 중 반복 검증과 튜닝에 사용하는 합성 `DEV` fixture다. 별도 Dataset
`rag-holdout-safety@1.0.0`은 60개 `HOLDOUT`과 93개 `SAFETY_REGRESSION` Case를 고정하기 위한 합성
후보다. 향후 승인된 임상 데이터가 필요해도 이 합성 Dataset에 섞지 않고 별도 보호·승인 경계를
따른다.

현재 커밋의 `rag-holdout-safety@1.0.0`은 `status=FROZEN`이고 Schema Set `1.2.0`을 사용한다.
Dataset Manifest와 153개 Case, Evidence Mapping, Critical Claim Rubric은 `@Jye-rookie`의 실제 PR #256
검토 event `5102210603` (`2026-09-03T13:00:53Z`)와 `@hazelnutflavoured`의 승인 event `5102473823`
(`2026-09-03T13:25:03Z`)를 immutable provenance로 결속했다. Freeze 상태를 포함한 최신 PR HEAD의 최종
검토는 별도 PR review 절차로 수행한다.
외부 의료 검토는 `PENDING`이며 Profile, Evaluation Policy, Suite 및 integrity receipt는 별도의
non-release 계약 상태를 유지한다.

현재 review-recorded Dataset graph는 다음 validation-only 명령으로 검증한다.

```bash
uv run python -m ai_worker.tasks.evaluation validate \
  --manifest evals/retrieval/manifests/rag-holdout-safety-v1.dataset.json \
  --result evals/validation-results/rag-holdout-safety-v1.validation.json
```

검증 성공은 Dataset 구조·hash·privacy·leakage 계약이 일치한다는 뜻일 뿐, HOLDOUT 실행이나 Release
`PASS`, 임상·의료·약학·Privacy·Source·Production 승인을 뜻하지 않는다. 이 Dataset에 연결된
Comparison Policy의 필수 `approved_by`에는 SYSTEM actor `rag-eval-draft-validator`가 들어 있지만,
이는 non-release graph를 load하기 위한 진단용 validation envelope 표시일 뿐 사람의 Dataset/Policy
승인이 아니다. Policy 자체도 `holdout_execution_authorized=false`다.

Dataset Freeze 뒤에도 HOLDOUT 실행용 Comparison/Evaluation Policy 승인이 별도로 필요하다. #157의 DEV
Runner 작업은 이 Freeze 이후 시작하며, 최초 HOLDOUT 실행은 독립 실행 Policy가 승인될 때까지
`WAITING_FOR_APPROVED_COMPARISON_POLICY` 상태를 유지한다.

이 Dataset의 채점 대상 자연어 표면(query, Gold claim, 금지 semantic rule, Evidence statement,
Rubric description)은 한국어(`ko-KR`)다. 불변 식별자·enum·reason code·locator와 `FICTIONAL_*`,
`SYNTHETIC_*` 토큰은 계약 호환성을 위해 원문 표기를 유지한다.

### #157 Freeze 인계 참조

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

Receipt 표의 SHA-256은 Dataset Manifest가 참조하는 canonical file hash다. Receipt 내부 self-hash는
`1b88575a5454131d315d71774dceeb6e979fd440959ed3ce3f2635b02c0a7fa7`이며, 이 receipt는 153개 Case
resource만 보호하고 Evidence·Rubric·Profile·Policy·Suite 승인을 증명하지 않는다.

Dataset가 `FROZEN`된 뒤에는 `rag-holdout-safety@1.0.0`의 Case, Gold, Evidence Mapping,
Critical Claim Rubric, Leakage 배치를 제자리에서 수정하지 않는다. 변경이 필요하면 새 Dataset version을
만들고, 튜닝용 파생 Case는 `DEV`에 둔다. Profile·Policy·Suite는 독립 version을 사용하므로 각각의
변경도 새 불변 참조로 연결한다.

## Chat history 평가

`generation/chat-v2-history-eval-v1.json`, `generation/chat-v2-history-eval-v2.json`, `generation/chat-v3-history-eval-v1.json`, `generation/chat-v3-history-eval-v2.json`, `generation/chat-v4-conversation-quality-eval-v1.json`은 `SYNTHETIC`으로 분류된 불변 평가셋입니다. 각 버전의 기준선과 처리 경로는 같은 prompt version을 사용하며, 차이는 각각 `history=[]`와 합성 history뿐입니다. 결정론적 replay는 실제 `ChatGenerator`의 메시지 조립·검증 경로를 실행합니다.

| 버전 | Case 수 | 상태 | 비고 |
| --- | --- | --- | --- |
| `chat-v2-history-eval-v1` | 10 | 동결 | Issue #129 / PR #145에서 승인된 불변 버전이다. 제자리에서 수정하지 않는다. |
| `chat-v2-history-eval-v2` | 11 | 동결 | Issue #293 조사에서 확인한 커버리지 갭을 메운 `followup-earlier-subject-over-latest`를 추가했다. v1의 10 case는 byte-for-byte 재사용한다. |
| `chat-v3-history-eval-v1` | 16 | 동결 | v2의 11 case를 유지하고 Issue #306의 대상 불명확 처방약 사례, 단일 약물 암시 질문 회귀, 대상 불명확 현재 호흡곤란과 과거 증상 해소 뒤 현재 의식 저하·경련을 결합한 응급 우선 사례, 30회 live 분류 설정을 추가했다. `gpt-4o-mini` 실행 기준이다. |
| `chat-v3-history-eval-v2` | 16 | 동결 | v1의 16개 `cases` payload를 byte-for-byte 재사용하고 Issue #567의 `gpt-4o` 모델 전환만 반영했다. |
| `chat-v4-conversation-quality-eval-v1` | 27 | canonical | v3-v2의 16 case와 `gpt-4o` 설정을 보존하고 Issue #581의 문맥·정정·주제 복귀·OTC·시간대 약 묶음·범위 제한·중복 및 과량 복용 사례 11건과 독립 품질 축 expectation을 추가했다. |

다섯 버전 모두 저장소에 유지하지만 현재 runner는 선택한 dataset과 관계없이 현재 runtime의 `ChatGenerator`·prompt를 사용합니다. 따라서 v1·v2·v3 경로 실행은 과거 prompt 실행 결과 재현이 아니라 현재 runtime prompt로 historical dataset을 다시 채점하는 용도입니다. 과거 prompt·hash·Provider 조합을 재현하려면 해당 실행 근거가 생성된 commit을 checkout해야 합니다. 결과 artifact의 `prompt_provenance.execution_semantics=CURRENT_RUNTIME_PROMPT`와 `historical_prompt_reproduction=false`가 이 경계를 명시합니다. 현재 기본 실행은 v4를 사용하며 runner의 canonical 경로·`dataset_id`·고정 SHA-256은 v4를 가리킵니다.

```bash
# canonical(v4)
PYTHONPATH=backend:. uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --output evals/results/chat-v4-conversation-quality-eval-v1-local-deterministic.json

# historical v1 dataset을 현재 runtime prompt로 재채점
PYTHONPATH=backend:. uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --dataset evals/generation/chat-v2-history-eval-v1.json \
  --output evals/results/chat-v2-history-eval-v1-local-deterministic.json
```

결과에는 rule ID와 집계값만 기록하고 원시 질문·history·응답과 PII sentinel은 기록하지 않습니다. 응급 사례는 공백·Unicode·종결부호를 정규화한 전체 응답이 승인된 긴급 행동 문장과 일치할 때만 통과해 부정·유예·후행 상쇄 문장을 fail-closed로 거부합니다. v4는 v3의 Issue #306 30회 대상 불명확 분류와 세 응급 case blocking gate를 보존하고, #581의 이미 발생한 중복 복용과 과량 복용을 별도 case로 나눠 각각의 baseline/history 4개 경로를 필수 gate에 추가합니다. 어느 필수 경로라도 실패하면 live runner는 exit code 1을 반환합니다.

새 11 case는 전체 case expectation과 별도의 `quality_expectations`를 사용해 `context_resolution`, `redundant_clarification`, `user_correction`, `topic_continuity`, `colloquial_language`, `safety`, `medication_consistency`, `naturalness`를 독립적으로 채점합니다. `이부프로펜이랑`, `아침약`, `점심약`, `배고프지` 원 재현 흐름과 별도 과량 복용 case를 포함합니다. 결과의 `{dimension}_evaluated_case_count`, `{dimension}_history_pass_count`, `{dimension}_history_violation_count`는 해당 축 expectation만 소비하며 multi-tag case 전체 통과 여부를 복제하지 않습니다. 실제 Provider 실행 전에는 이 결정론적 결과를 생성 품질 통과로 해석하지 않습니다.

2026-09-08 합성 OpenAI live 실행은 이전 13-case fixture에서 85 response를 사용했으므로 현재 근거로 사용하지 않습니다. [2026-09-09 동결 v1 / `gpt-4o-mini` historical live evidence](../docs/validation/issue-306-chat-live-evaluation.md)는 당시 blocking gate를 통과했지만 현재 canonical v4의 `gpt-4o` 검증 근거, 전체 모델 품질 또는 Production 승인으로 확대 해석하지 않습니다. v4 Provider 평가와 현행 prompt/model 대 후보 prompt/model의 blind A/B는 아직 `NOT_RUN`이며 #581에서 계속 추적합니다. 결정론적 27/27만 실제 모델 품질이나 Production 승인으로 확대 해석하지 않습니다.

### Chat prompt blind A/B

현재 runtime prompt는 #581 짧은 후속 질문 보강 버전 `chat-prompt-v5`입니다. 기존 v3/v4 blind config와 snapshot은 해당 두 버전을 재현하는 자료이며 v5 평가 결과가 아닙니다. `chat-v5-short-followup-eval-v1.json`은 기존 v4 27-case를 그대로 보존하고 비교 대상 교체, 구어체 이유 질문, 첫 병용 답변의 근거 없는 안전 단정 사례를 추가한 30-case 합성 평가셋입니다. 기대 문구는 리뷰 대상이며 실제 모델 출력이 아닙니다.

```bash
PYTHONPATH=backend:. uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --dataset evals/generation/chat-v5-short-followup-eval-v1.json \
  --output evals/results/chat-v5-short-followup-replay.json
```

결정론적 실행은 준비된 답변으로 평가 규칙과 입력 전달을 검증합니다. 관찰된 실패 문구를 주입하는 회귀도 함께 실행하며 실제 Provider의 수정 전후 개선 증거로 해석하지 않습니다. 이 새 dataset은 기존 live CLI allowlist에 포함하지 않았으므로 실제 v4/v5 비교에는 별도로 고정한 config·snapshot과 Local 실행 준비가 필요합니다. 기존 live CLI의 27-case 실행은 실행 당시 runtime prompt version/hash를 기록하므로 dataset 이름만 보고 v4 실행으로 해석하지 않습니다. 자세한 결과는 [v5 합성 회귀 기록](../docs/validation/issue-581-short-followup-v5.md)을 참고하세요.

배치는 실행마다 생성하는 비공개 256-bit seed로 무작위화합니다. 전체 54개 item을 하나의 균형 블록으로 두고 response 1 arm 위치 27개씩을 섞으며, 단순 교대 배치를 사용하지 않습니다. seed와 독립적인 256-bit commitment nonce는 권한 `0600`의 assignment artifact에만 보관합니다. 재현은 판단 제출 후 공개한 private artifact로 수행합니다.

review packet과 judgment template에는 assignment 내용의 SHA-256 commitment를 고정합니다. 리뷰어는 전달받은 packet·template을 보관하고 이 commitment를 유지한 judgment를 제출합니다. unblind는 제출된 commitment와 assignment를 재계산한 hash가 일치해야 진행하므로 사후 mapping 변경을 거부합니다. hash 순환을 피하기 위해 assignment 자체의 commitment 필드와 review packet hash만 commitment 계산에서 제외합니다. 판단 후 template이나 judgment를 새로 생성하면 사전 결속 증거가 사라지므로 원래 전달·제출 파일을 보존해야 합니다.

canonical `quality_expectations`의 dimension 선호는 history item에만 적용합니다. baseline은 전체 응답 선호만 판단하고 `dimension_preferences`는 빈 객체로 제출합니다. history item은 해당 case에 선언된 dimension을 정확히 모두 제출해야 합니다.

`generation/chat-conversation-quality-blind-ab-v1.json`은 #581의 `chat-prompt-v3` 대 `chat-prompt-v4` 비교를 위한 Local 전용 실행 설정입니다. 두 arm은 같은 canonical 27-case dataset, `gpt-4o`, timeout과 출력 token 상한을 사용하고 prompt snapshot만 다릅니다. `generation/prompts/chat-prompt-v3.txt`와 `generation/prompts/chat-prompt-v4.txt`는 각 prompt 문자열의 불변 snapshot이며 config가 dataset·prompt SHA-256을 모두 고정합니다. 이 PR은 runner와 실행 설정만 준비하며 실제 Provider 실행 상태는 `NOT_RUN`입니다.

Live 실행은 arm당 113개, 총 226개의 Provider 응답을 생성합니다. 자동 결과에는 case·품질 축·blocking safety gate, baseline/history/max-history p95 latency, Provider가 반환한 input/output/total token 사용량을 기록합니다. 승인된 버전 고정 요금표가 없으므로 비용은 `NOT_CALCULATED`이며 token 사용량을 비용으로 오인하지 않습니다.

```bash
RUN_OPENAI_CHAT_BLIND_AB_EVAL=1 ENV=local \
PYTHONPATH=backend:. uv run python -m app.evaluation.chat_blind_ab_runner run \
  --review-packet evals/results/chat-blind-ab-v1-review.json \
  --judgment-template evals/results/chat-blind-ab-v1-judgments.json \
  --assignment evals/results/chat-blind-ab-v1-assignment.json
```

`review` artifact에는 합성 질문·history·medications와 `response_1`·`response_2`만 있으며 variant id, prompt version, model과 assignment를 넣지 않습니다. PII sentinel fixture 문자열은 `[SYNTHETIC_SENTINEL_REDACTED]`로 치환합니다. 담당 리뷰어에게는 review artifact와 arm mapping이 없는 judgment template만 전달하고 assignment artifact는 판단 제출 전까지 공개하지 않습니다. assignment artifact에는 arm mapping, 자동 점수, latency와 token 사용량이 들어가며 원시 질문·history·응답은 들어가지 않습니다.

담당 리뷰어의 judgment JSON은 review item 54개를 각각 정확히 한 번 포함하고 `preference` 및 필요한 `dimension_preferences`에 `RESPONSE_1`, `RESPONSE_2`, `TIE` 중 하나를 기록합니다. coordinator가 review packet SHA-256만 전달하고 arm mapping은 전달하지 않습니다. 다음 명령은 판단을 variant id로 집계하지만 자동으로 winner를 선택하지 않으며 최종 상태를 `PENDING_RESPONSIBLE_REVIEWER_APPROVAL`로 유지합니다.

```bash
PYTHONPATH=backend:. uv run python -m app.evaluation.chat_blind_ab_runner unblind \
  --assignment evals/results/chat-blind-ab-v1-assignment.json \
  --judgments evals/results/chat-blind-ab-v1-judgments.json \
  --output evals/results/chat-blind-ab-v1-result.json
```

Blind A/B artifact는 합성 Local 평가 근거일 뿐 실제 의료·약학 안전성, Production history 전송, Privacy 승인 또는 공개 근거가 아닙니다. 어느 arm이든 기존 blocking safety gate를 통과하지 못하면 run 명령은 exit code 1을 반환하며 선택 후보가 될 수 없습니다.

실제 OpenAI 평가는 `RUN_OPENAI_CHAT_HISTORY_EVAL=1`, `ENV=local`, 공백이 아니고 저장소 placeholder와 일치하지 않는 `OPENAI_API_KEY`가 모두 있을 때만 `--mode live`로 실행할 수 있습니다. live 모드는 저장소의 canonical `chat-v4-conversation-quality-eval-v1` 경로, `dataset_id`, `SYNTHETIC` 분류와 고정 SHA-256이 모두 일치하는 경우만 허용하며 임의 `--dataset`과 변경된 fixture를 OpenAI client 생성 전에 거부합니다. SHA-256은 Windows CRLF checkout과 LF checkout을 동일하게 취급하도록 CRLF를 LF로 정규화한 bytes에 계산하며, 줄바꿈 외 내용 변경은 계속 거부합니다. 결과 artifact에는 실행에 사용한 dataset·prompt SHA-256, live gate 구성요소, `full_suite_passed`와 전체 `passed`를 기록합니다. live blocking 기준 미달은 artifact를 남기고 exit code 1, 구성·Provider 실행 오류는 exit code 2를 반환합니다. 실행하지 않은 Provider 품질·latency·token 결과는 `NOT_RUN`으로 유지하며, 결정론적 replay 결과를 실제 모델 품질이나 Production 승인 근거로 해석하지 않습니다.
