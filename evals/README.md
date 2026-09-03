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

### Evaluation Schema Sets

- `evals/schemas/1.0.0/`: Issue #122의 기존 DEV foundation 계약. canonical bytes와 loader 동작을 유지한다.
- `evals/schemas/1.1.0/`: Issue #216의 18-member implemented candidate. Case·Dataset Manifest는 member `1.1.0`, 나머지 16개 member는 `1.0.0`을 byte-for-byte 재사용한다.
- `evals/schemas/1.2.0/`: Issue #241의 review provenance compatibility 계약. Case·Manifest·Evidence Mapping·Rubric·Profile·Suite·Evaluation Policy·Protected Artifact Receipt 8개 member는 `1.2.0`, 나머지 10개 member는 이전 canonical bytes를 재사용한다.

Schema Set `1.1.0`의 불변 참조는 `rag-eval.schema-set@1.1.0`, SHA-256 `5cfb113e45a4c333fef05830b0d7c2401975ce66b53dc68ff054b08ba79822c0`이다. #216/PR #222에서 승인·병합된 초기 호환성 계약이다.

Schema Set `1.2.0`의 불변 참조는 `rag-eval.schema-set@1.2.0`, SHA-256 `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06`이다. DRAFT artifact는 reviewer identity·timestamp·review evidence를 기록하지 않으며, 실제 팀 검토부터 `reviewed_by.role=EVALUATION_REVIEWER`와 immutable review evidence를 기록한다. 이 내부 역할은 외부 의료 검토가 아니며 #214 Dataset Freeze 승인 입력은 지정 책임 리뷰 승인 전까지 계속 후보 상태다.

세 버전은 다음 명령으로 별도 출력한다. 기본값은 하위 호환을 위해 `1.0.0`이다.

```bash
uv run python -m ai_worker.tasks.evaluation.schema_exports \
  --output /tmp/rag-eval-schemas-1.2.0 \
  --schema-set-version 1.2.0
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

`generation/chat-v2-history-eval-v1.json`은 `SYNTHETIC`으로 분류된 불변 평가셋입니다. 기준선과 처리 경로 모두 `chat-prompt-v2`를 사용하며, 차이는 각각 `history=[]`와 합성 history뿐입니다. 결정론적 replay는 실제 `ChatGenerator`의 메시지 조립·검증 경로를 실행합니다.

```bash
cd backend
uv run python -m app.evaluation.chat_history_runner \
  --mode deterministic \
  --output ../evals/results/chat-v2-history-eval-v1-local-deterministic.json
```

결과에는 rule ID와 집계값만 기록하고 원시 질문·history·응답과 PII sentinel은 기록하지 않습니다. 실제 OpenAI 평가는 `RUN_OPENAI_CHAT_HISTORY_EVAL=1`, `ENV=local`, 공백이 아니고 저장소 placeholder와 일치하지 않는 `OPENAI_API_KEY`가 모두 있을 때만 `--mode live`로 실행할 수 있습니다. live 모드는 저장소의 canonical `chat-v2-history-eval-v1` 경로, `dataset_id`, `SYNTHETIC` 분류와 고정 SHA-256이 모두 일치하는 경우만 허용하며 임의 `--dataset`과 변경된 fixture를 OpenAI client 생성 전에 거부합니다. SHA-256은 Windows CRLF checkout과 LF checkout을 동일하게 취급하도록 CRLF를 LF로 정규화한 bytes에 계산하며, 줄바꿈 외 내용 변경은 계속 거부합니다. 실행하지 않은 Provider 품질·latency·token 결과는 `NOT_RUN`으로 유지하며, 결정론적 replay 결과를 실제 모델 품질이나 Production 승인 근거로 해석하지 않습니다.
