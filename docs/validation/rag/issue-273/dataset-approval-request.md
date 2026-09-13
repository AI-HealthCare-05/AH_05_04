# Issue #273 Dataset Approval Request

> 이 문서는 준비 전용(PREPARATION_ONLY) 승인 요청 입력입니다. 승인 결과나 Freeze를 선기록하지 않습니다.
> 저장소 로컬 비런타임 projection이며 공유 schema 또는 runtime contract가 아닙니다.

## 담당자와 현재 상태

- 구현 담당자: 정현우 (`@ceohwj`, `EVALUATION_IMPLEMENTER`)
- Gold 내용 검토자: 권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)
- 요청 Dataset Custodian: 송은영 (`@phina-io`, `DATASET_CUSTODIAN`)
- 현재 Dataset은 계속 `DRAFT`, Gold provenance는 `REVIEWED`, HOLDOUT은 `0`입니다.
- 이 준비 PR에서는 HOLDOUT 40개를 만들거나 Freeze하지 않습니다.
- 실제 Retriever Adapter가 없으므로 Baseline을 실행하지 않습니다.

## 승인 대상

- Dataset: `rag-natural-language-retrieval-dev@1.0.0`
- 범위: 20 origins / 60 한국어 DEV 질문 / 20 Gold / 80 hard negatives
- 현재 Dataset manifest self hash: `c4d54f4b17f84845ff3cec10f84958a9742357500db10b194535665735fbecff`
- Gold review evidence: `github-pr-341-review-5137833200` / `6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776`
- Approval request self hash: `032d6c8ce6110c500a8c5fe570381cd377ad73498b856f11c647b13197a15a8f`

## 원본 결속

- `dataset_manifest`: `retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json` / `5197003ba294a9bc4857dd78576f76a0bc854c2c330fb734cfbc462c8538c0ba`
- `evaluation_labels`: `retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/evaluation-labels.json` / `89b08cf0ee1d12c6e918c1c9075600522a6912c13b0dc8e2e412745cfbe76c24`
- `evidence_mapping`: `retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json` / `cd0e295e23aeb323814055957d867f003dabff0aa4a117a3c5fa5900bcb0dc72`
- `gold_review_evidence`: `provenance/rag-natural-language-retrieval-dev-v1.review-evidence.json` / `6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776`
- `retrieval_index`: `retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json` / `f8559cc16371ffe0d6d1c70912c37518afb96ce2ca8b7dca87b34ab82033940d`
- Case resources: `60`개; 각 path와 raw SHA-256은 JSON에 기록

## Dataset Custodian 확인 기준

- 위 해시가 PR의 현재 파일과 일치하고 60개 Case가 모두 동일한 Gold review evidence에 결속되는지 확인합니다.
- 구현 담당자, Gold 검토자, Dataset Custodian이 서로 다른 실제 사람인지 확인합니다.
- 공개 가능한 합성 DEV 데이터만 포함되고 실제 환자·제품·Provider 데이터와 HOLDOUT이 없는지 확인합니다.
- Dataset과 Evidence Mapping에 승인자·승인 시각·Freeze 시각이 미리 기록되지 않았는지 확인합니다.

## 실제 승인 방법

송은영님은 위 기준을 확인한 뒤 이 준비 PR에서 GitHub의 Approve 기능으로 review를 제출하고, 본문에 아래 문구를 사용합니다. placeholder는 실제 provenance 값이 아닙니다.
`<approved commit OID>`는 제출 전에 GitHub가 실제로 검토한 PR HEAD의 정확한 40자리 commit OID로 반드시 치환합니다.

```text
Gold review result: APPROVED
approval_request_sha256: 032d6c8ce6110c500a8c5fe570381cd377ad73498b856f11c647b13197a15a8f
dataset_manifest_sha256: c4d54f4b17f84845ff3cec10f84958a9742357500db10b194535665735fbecff
gold_review_evidence_sha256: 6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776
approved_origins: 20/20
review_commit_oid: <approved commit OID>
```

## 승인 후 후속 작업

실제 승인 event가 생성된 뒤에만 별도 provenance 기록 PR을 만듭니다. 그 PR에서 GitHub API로 review ID, actor, state, submitted timestamp, body, commit OID를 수집해 immutable evidence로 결속하고 60개 Case·Evidence Mapping·Dataset Manifest를 `APPROVED`로 전이합니다. 그 전에는 승인값을 기록하지 않습니다.

Dataset 승인과 HOLDOUT Freeze는 별개입니다. 접근 통제와 Freeze Receipt 계약이 확정된 뒤 별도 작업으로 HOLDOUT 40개를 준비하며, Retriever Adapter가 준비된 이후에만 100개 실제 baseline을 실행합니다.
