# Issue #273 DEV Gold Review Packet

> 이 문서는 준비 전용(PREPARATION_ONLY) 검토 입력입니다. 사람의 검토 결과, 승인, Freeze를 기록하지 않습니다.
> 이 JSON/Markdown은 저장소 로컬 비런타임 projection이며 공유 schema 또는 runtime contract가 아닙니다.

## 검토 범위

- Dataset: `rag-natural-language-retrieval-dev@1.0.0` (`DRAFT`)
- 대상: 20 origins / 60 한국어 DEV 질문 / 20 Gold / 80 hard negatives
- 공개 가능한 합성 DEV만 포함하며 HOLDOUT은 포함하지 않습니다.
- OTC 추천·상호작용 질문은 포함하지 않으며 해당 범위는 Issue #278에서 다룹니다.

## 원본 결속

- Dataset manifest self hash: `a6461ca49c6021b242bd5b13f3d9b1b52bf564bea186a47894cd254a40600291`
- Packet self hash: `fd98d6b3b88f80f858f32275ab8769f77dca152d55ea31e7583a2f88af180f1b`
- `authoring_identity`: `retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json` / `0535464bb16c2e4ba71e9d94b733fd33d0f9d1a2bb8be83ee893e2e5a99105dc`
- `dataset_manifest`: `retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json` / `cd9f93940c7592f179aac8c8a83b6488ef92908b5875979de1cac5c0ebf63e19`
- `evaluation_labels`: `retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/evaluation-labels.json` / `89b08cf0ee1d12c6e918c1c9075600522a6912c13b0dc8e2e412745cfbe76c24`
- `evidence_mapping`: `retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json` / `23be3f085b3beded2c8a235a285e8bb733521d10e6b51c839d58796000bb9d70`
- `retrieval_index`: `retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json` / `f8559cc16371ffe0d6d1c70912c37518afb96ce2ca8b7dca87b34ab82033940d`

## 담당 리뷰어 확인 기준

각 origin을 직접 확인하고 다음 기준을 모두 판단합니다.

- Gold Evidence가 세 질문의 의도를 모두 충족하는지 확인합니다.
- Gold가 필요한 내용을 담은 최소 단일 Evidence인지 확인합니다.
- 네 hard negative 중 실제 정답인 false negative가 없는지 확인합니다.
- 실제 환자·제품·Provider 데이터가 아닌 합성 데이터인지 확인합니다.
- 세 표현 변형이 같은 검색 의도를 보존하는지 확인합니다.
- OTC 추천·상호작용 질문이 포함되지 않음을 확인합니다.

## 검토 결과 기록 방법

검토 결과는 이 파일에 미리 쓰지 않습니다. 담당 리뷰어가 모든 20개 origin을 확인한 뒤 GitHub Pull Request review event에 검토 범위와 결과를 명시합니다. 변경이 필요하면 origin ID를 지정합니다. PR #316의 승인은 Gold review 증빙으로 재사용하지 않습니다. 실제 event가 생성된 뒤 별도 기록 PR에서 immutable event reference를 결속하고 provenance를 전이합니다.

- REVIEWED event actor는 `EVALUATION_REVIEWER` 역할이어야 합니다.
- APPROVED event actor는 `DATASET_CUSTODIAN` 역할이어야 합니다.
- 작성자·REVIEWED actor·APPROVED actor는 서로 다른 실제 사람이어야 합니다.
- APPROVED event를 요청하기 전에 Issue와 PR에 별도 승인 담당자를 명시하고 실제 계정·역할 매핑을 확인합니다. 확인되지 않은 계정을 추정해 기록하지 않습니다.

첫 실제 내용 검토 event에는 아래 REVIEWED 문구를 사용합니다.

```text
Gold review result: REVIEWED
packet_sha256: fd98d6b3b88f80f858f32275ab8769f77dca152d55ea31e7583a2f88af180f1b
dataset_manifest_sha256: a6461ca49c6021b242bd5b13f3d9b1b52bf564bea186a47894cd254a40600291
reviewed_origins: 20/20
review_commit_oid: <reviewed commit OID>
```

수정 요구가 모두 해소된 뒤 별도의 승인 event에는 아래 APPROVED 문구를 사용합니다.

```text
Gold review result: APPROVED
packet_sha256: fd98d6b3b88f80f858f32275ab8769f77dca152d55ea31e7583a2f88af180f1b
dataset_manifest_sha256: a6461ca49c6021b242bd5b13f3d9b1b52bf564bea186a47894cd254a40600291
approved_origins: 20/20
review_commit_oid: <approved commit OID>
```

각 event 제출 후 기록 PR이 GitHub API에서 실제 review ID, actor, submitted timestamp를 수집하고 event body와 commit OID에 함께 결속합니다. 템플릿 placeholder를 provenance 값으로 사용하지 않습니다. APPROVED event 전에는 Dataset을 Freeze하지 않습니다.

## NLR-MI01

- Topic: `TOPIC_MEDICATION_INFORMATION`
- Product code: `NLR-MI01`
- 질문:
  - `rag-nlr-dev-001` · `EXPRESSION_CANONICAL`: NLR-MI01 제품의 성분 정보에 대해 알려 주세요.
  - `rag-nlr-dev-002` · `EXPRESSION_SYNONYM`: NLR-MI01 제품, 어떤 원료로 만들어졌는지 알고 싶어요.
  - `rag-nlr-dev-003` · `EXPRESSION_COLLOQUIAL`: NLR-MI01 제품 성분 정보가 궁금해요.
- Gold: `ev-nlr-a13ddae19bb6282d` · `SYNTHETIC_NLR_CHUNK_016` · `$.records[67]`
  - 평가용 가상 설정에서 NLR-MI01 제품의 성분 정보는 청색 결정 성분 하나로 구성됩니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-bc75632fbdc90d84`: 평가용 가상 설정에서 NLR-MI01 제품의 포장 관리 코드는 PKG-01이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-c2c9f052d303e5b4`: 평가용 가상 설정에서 NLR-MI02 제품의 의약품 정보 자료는 CARD-02 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-328598553a53ab6d`: 평가용 가상 설정에서 NLR-MI01 제품의 의약품 정보 자료는 합성 색인의 IDX-01 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-f0df3e3428eb9fb9`: 평가용 가상 설정에서 NLR-PC01 제품의 주의 안내 자료에는 성분 정보 항목이 REF-05 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MI02

- Topic: `TOPIC_MEDICATION_INFORMATION`
- Product code: `NLR-MI02`
- 질문:
  - `rag-nlr-dev-004` · `EXPRESSION_WORD_ORDER_PARTICLE`: 제형과 외형 정보 알려 주세요, NLR-MI02 제품이요.
  - `rag-nlr-dev-005` · `EXPRESSION_FRAGMENT`: NLR-MI02 제형 외형?
  - `rag-nlr-dev-006` · `EXPRESSION_LIMITED_TYPO`: NLR-MI02 제품의 제형과 외형 정보에 대해 알려 주새요.
- Gold: `ev-nlr-4432ebed12ad8a04` · `SYNTHETIC_NLR_CHUNK_007` · `$.records[27]`
  - 평가용 가상 설정에서 NLR-MI02 제품의 제형과 외형 정보는 연보라색 삼각 필름 형태와 점 무늬 두 개입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-a3cf48fc5905bea3`: 평가용 가상 설정에서 NLR-MI02 제품의 포장 관리 코드는 PKG-02이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-7d97a919bcac0e03`: 평가용 가상 설정에서 NLR-MI03 제품의 의약품 정보 자료는 CARD-03 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-e0314ae9bc61d8b5`: 평가용 가상 설정에서 NLR-MI02 제품의 의약품 정보 자료는 합성 색인의 IDX-02 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-d3a4a75731593594`: 평가용 가상 설정에서 NLR-PC02 제품의 주의 안내 자료에는 제형과 외형 정보 항목이 REF-06 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MI03

- Topic: `TOPIC_MEDICATION_INFORMATION`
- Product code: `NLR-MI03`
- 질문:
  - `rag-nlr-dev-007` · `EXPRESSION_CANONICAL`: NLR-MI03 제품의 사용 목적 정보에 대해 알려 주세요.
  - `rag-nlr-dev-008` · `EXPRESSION_WORD_ORDER_PARTICLE`: 사용 목적 정보 알려 주세요, NLR-MI03 제품이요.
  - `rag-nlr-dev-009` · `EXPRESSION_COLLOQUIAL`: NLR-MI03 제품 사용 목적 정보가 궁금해요.
- Gold: `ev-nlr-fbb3767acf028845` · `SYNTHETIC_NLR_CHUNK_019` · `$.records[98]`
  - 평가용 가상 설정에서 NLR-MI03 제품의 사용 목적 정보는 가상 분류표의 단계 A 표식을 확인하는 연습으로 정의됩니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-0e4f970594acfe4e`: 평가용 가상 설정에서 NLR-MI03 제품의 포장 관리 코드는 PKG-03이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-60c836cf3b355da1`: 평가용 가상 설정에서 NLR-MI04 제품의 의약품 정보 자료는 CARD-04 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-5deb63c7c8172a01`: 평가용 가상 설정에서 NLR-MI03 제품의 의약품 정보 자료는 합성 색인의 IDX-03 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-b88bc3677adc67b2`: 평가용 가상 설정에서 NLR-PC03 제품의 주의 안내 자료에는 사용 목적 정보 항목이 REF-07 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MI04

- Topic: `TOPIC_MEDICATION_INFORMATION`
- Product code: `NLR-MI04`
- 질문:
  - `rag-nlr-dev-010` · `EXPRESSION_SYNONYM`: NLR-MI04 제품, 겉면 표시로 어떻게 구분하는지 알고 싶어요.
  - `rag-nlr-dev-011` · `EXPRESSION_FRAGMENT`: NLR-MI04 라벨 표시?
  - `rag-nlr-dev-012` · `EXPRESSION_LIMITED_TYPO`: NLR-MI04 제품의 라벨 식별 정보에 대해 알려 주새요.
- Gold: `ev-nlr-3b6087cadb9506d7` · `SYNTHETIC_NLR_CHUNK_006` · `$.records[21]`
  - 평가용 가상 설정에서 NLR-MI04 제품의 라벨 식별 정보는 문자 MI04와 주황색 마름모 표식의 조합입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-b4ab197cb9fbd1dd`: 평가용 가상 설정에서 NLR-MI04 제품의 포장 관리 코드는 PKG-04이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-19d886871f591533`: 평가용 가상 설정에서 NLR-MI01 제품의 의약품 정보 자료는 CARD-01 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-aa3d1ba6726e38be`: 평가용 가상 설정에서 NLR-MI04 제품의 의약품 정보 자료는 합성 색인의 IDX-04 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-f926215ffbd09e82`: 평가용 가상 설정에서 NLR-PC04 제품의 주의 안내 자료에는 라벨 식별 정보 항목이 REF-08 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-PC01

- Topic: `TOPIC_PRECAUTIONS`
- Product code: `NLR-PC01`
- 질문:
  - `rag-nlr-dev-013` · `EXPRESSION_CANONICAL`: NLR-PC01 제품의 복용 전 주의사항에 대해 알려 주세요.
  - `rag-nlr-dev-014` · `EXPRESSION_SYNONYM`: NLR-PC01 제품, 먹기 전에 무엇을 조심해야 하는지 알고 싶어요.
  - `rag-nlr-dev-015` · `EXPRESSION_COLLOQUIAL`: NLR-PC01 제품 복용 전 주의사항이 궁금해요.
- Gold: `ev-nlr-e565363f47f37459` · `SYNTHETIC_NLR_CHUNK_018` · `$.records[91]`
  - 평가용 가상 설정에서 NLR-PC01 제품의 복용 전 주의사항은 봉인선과 확인표의 세 칸을 점검하는 절차입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-a8f285b4605871aa`: 평가용 가상 설정에서 NLR-PC01 제품의 포장 관리 코드는 PKG-05이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-5ccb2d6694dc6e87`: 평가용 가상 설정에서 NLR-PC02 제품의 주의 안내 자료는 CARD-06 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-6af43d92e0b0f518`: 평가용 가상 설정에서 NLR-PC01 제품의 주의 안내 자료는 합성 색인의 IDX-05 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-640493b0ae724c3e`: 평가용 가상 설정에서 NLR-LM01 제품의 생활 관리 자료에는 복용 전 주의사항 항목이 REF-09 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-PC02

- Topic: `TOPIC_PRECAUTIONS`
- Product code: `NLR-PC02`
- 질문:
  - `rag-nlr-dev-016` · `EXPRESSION_WORD_ORDER_PARTICLE`: 알레르기 경고 정보 알려 주세요, NLR-PC02 제품이요.
  - `rag-nlr-dev-017` · `EXPRESSION_FRAGMENT`: NLR-PC02 알레르기 경고?
  - `rag-nlr-dev-018` · `EXPRESSION_LIMITED_TYPO`: NLR-PC02 제품의 알레르기 경고 정보에 대해 알려 주새요.
- Gold: `ev-nlr-67f79700d44af1f3` · `SYNTHETIC_NLR_CHUNK_011` · `$.records[41]`
  - 평가용 가상 설정에서 NLR-PC02 제품의 알레르기 경고 정보는 별표 모양 성분 표식이 있으면 가상 확인 카드 B를 조회하라는 내용입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-45f7e66c0b58680b`: 평가용 가상 설정에서 NLR-PC02 제품의 포장 관리 코드는 PKG-06이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-ca605953220e5b32`: 평가용 가상 설정에서 NLR-PC03 제품의 주의 안내 자료는 CARD-07 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-74d90e8aced4f7ae`: 평가용 가상 설정에서 NLR-PC02 제품의 주의 안내 자료는 합성 색인의 IDX-06 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-5f5d1635aadb36c4`: 평가용 가상 설정에서 NLR-LM02 제품의 생활 관리 자료에는 알레르기 경고 정보 항목이 REF-10 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-PC03

- Topic: `TOPIC_PRECAUTIONS`
- Product code: `NLR-PC03`
- 질문:
  - `rag-nlr-dev-019` · `EXPRESSION_CANONICAL`: NLR-PC03 제품의 이상 반응 관찰 정보에 대해 알려 주세요.
  - `rag-nlr-dev-020` · `EXPRESSION_WORD_ORDER_PARTICLE`: 이상 반응 관찰 정보 알려 주세요, NLR-PC03 제품이요.
  - `rag-nlr-dev-021` · `EXPRESSION_COLLOQUIAL`: NLR-PC03 제품 이상 반응 관찰 정보가 궁금해요.
- Gold: `ev-nlr-b994f613fa47b6bc` · `SYNTHETIC_NLR_CHUNK_017` · `$.records[76]`
  - 평가용 가상 설정에서 NLR-PC03 제품의 이상 반응 관찰 정보는 상태 카드의 초록·노랑·빨강 세 표식을 기록하는 방식입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-36c0c18c75383733`: 평가용 가상 설정에서 NLR-PC03 제품의 포장 관리 코드는 PKG-07이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-971e4a96379f552f`: 평가용 가상 설정에서 NLR-PC04 제품의 주의 안내 자료는 CARD-08 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-8e8e863419593838`: 평가용 가상 설정에서 NLR-PC03 제품의 주의 안내 자료는 합성 색인의 IDX-07 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-690d096eb2855408`: 평가용 가상 설정에서 NLR-LM03 제품의 생활 관리 자료에는 이상 반응 관찰 정보 항목이 REF-11 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-PC04

- Topic: `TOPIC_PRECAUTIONS`
- Product code: `NLR-PC04`
- 질문:
  - `rag-nlr-dev-022` · `EXPRESSION_SYNONYM`: NLR-PC04 제품, 언제 의료진에게 물어봐야 하는지 알고 싶어요.
  - `rag-nlr-dev-023` · `EXPRESSION_FRAGMENT`: NLR-PC04 전문가 확인?
  - `rag-nlr-dev-024` · `EXPRESSION_LIMITED_TYPO`: NLR-PC04 제품의 전문가 확인이 필요한 조건에 대해 알려 주새요.
- Gold: `ev-nlr-ffa5c5e6affeebe5` · `SYNTHETIC_NLR_CHUNK_020` · `$.records[99]`
  - 평가용 가상 설정에서 NLR-PC04 제품의 전문가 확인이 필요한 조건은 가상 확인표가 빨강일 때 절차 C를 조회하는 경우입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-f246e2d8561b565b`: 평가용 가상 설정에서 NLR-PC04 제품의 포장 관리 코드는 PKG-08이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-6acf3d223bfea44d`: 평가용 가상 설정에서 NLR-PC01 제품의 주의 안내 자료는 CARD-05 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-50cc53787df12231`: 평가용 가상 설정에서 NLR-PC04 제품의 주의 안내 자료는 합성 색인의 IDX-08 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-9b562f91be29c768`: 평가용 가상 설정에서 NLR-LM04 제품의 생활 관리 자료에는 전문가 확인이 필요한 조건 항목이 REF-12 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-LM01

- Topic: `TOPIC_LIFESTYLE_MANAGEMENT`
- Product code: `NLR-LM01`
- 질문:
  - `rag-nlr-dev-025` · `EXPRESSION_CANONICAL`: NLR-LM01 제품의 수분 섭취 안내에 대해 알려 주세요.
  - `rag-nlr-dev-026` · `EXPRESSION_SYNONYM`: NLR-LM01 제품, 물을 얼마나 마시라고 하는지 알고 싶어요.
  - `rag-nlr-dev-027` · `EXPRESSION_COLLOQUIAL`: NLR-LM01 제품 수분 섭취 안내가 궁금해요.
- Gold: `ev-nlr-89f0915650860ea4` · `SYNTHETIC_NLR_CHUNK_014` · `$.records[55]`
  - 평가용 가상 설정에서 NLR-LM01 제품의 수분 섭취 안내는 기록 카드의 물컵 세 칸을 차례로 표시하는 방식입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-400b11bfa3f14659`: 평가용 가상 설정에서 NLR-LM01 제품의 포장 관리 코드는 PKG-09이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-c5b941cc66c22a3e`: 평가용 가상 설정에서 NLR-LM02 제품의 생활 관리 자료는 CARD-10 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-7aedfe0947772201`: 평가용 가상 설정에서 NLR-LM01 제품의 생활 관리 자료는 합성 색인의 IDX-09 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-11f43352c80e8f91`: 평가용 가상 설정에서 NLR-ST01 제품의 보관 안내 자료에는 수분 섭취 안내 항목이 REF-13 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-LM02

- Topic: `TOPIC_LIFESTYLE_MANAGEMENT`
- Product code: `NLR-LM02`
- 질문:
  - `rag-nlr-dev-028` · `EXPRESSION_WORD_ORDER_PARTICLE`: 식사 습관 안내 알려 주세요, NLR-LM02 제품이요.
  - `rag-nlr-dev-029` · `EXPRESSION_FRAGMENT`: NLR-LM02 식사 습관?
  - `rag-nlr-dev-030` · `EXPRESSION_LIMITED_TYPO`: NLR-LM02 제품의 식사 습관 안내에 대해 알려 주새요.
- Gold: `ev-nlr-0e5289d3e0842ad2` · `SYNTHETIC_NLR_CHUNK_002` · `$.records[5]`
  - 평가용 가상 설정에서 NLR-LM02 제품의 식사 습관 안내는 아침·낮·저녁 기록 칸을 같은 순서로 채우는 방식입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-8b6373a7344bd348`: 평가용 가상 설정에서 NLR-LM02 제품의 포장 관리 코드는 PKG-10이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-9e6adf6fd0d0c3aa`: 평가용 가상 설정에서 NLR-LM03 제품의 생활 관리 자료는 CARD-11 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-8b9fa520d0fdf3ac`: 평가용 가상 설정에서 NLR-LM02 제품의 생활 관리 자료는 합성 색인의 IDX-10 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-62c9d905449eba74`: 평가용 가상 설정에서 NLR-ST02 제품의 보관 안내 자료에는 식사 습관 안내 항목이 REF-14 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-LM03

- Topic: `TOPIC_LIFESTYLE_MANAGEMENT`
- Product code: `NLR-LM03`
- 질문:
  - `rag-nlr-dev-031` · `EXPRESSION_CANONICAL`: NLR-LM03 제품의 활동 안내에 대해 알려 주세요.
  - `rag-nlr-dev-032` · `EXPRESSION_WORD_ORDER_PARTICLE`: 활동 안내 알려 주세요, NLR-LM03 제품이요.
  - `rag-nlr-dev-033` · `EXPRESSION_COLLOQUIAL`: NLR-LM03 제품 활동 안내가 궁금해요.
- Gold: `ev-nlr-3a2601d25eadb244` · `SYNTHETIC_NLR_CHUNK_005` · `$.records[20]`
  - 평가용 가상 설정에서 NLR-LM03 제품의 활동 안내는 걷기와 휴식 표식을 번갈아 기록하는 방식입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-096c3a1524166cc9`: 평가용 가상 설정에서 NLR-LM03 제품의 포장 관리 코드는 PKG-11이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-030007791be4f5ce`: 평가용 가상 설정에서 NLR-LM04 제품의 생활 관리 자료는 CARD-12 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-b8e0e7ab16a2ef07`: 평가용 가상 설정에서 NLR-LM03 제품의 생활 관리 자료는 합성 색인의 IDX-11 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-10fff27b73bcc004`: 평가용 가상 설정에서 NLR-ST03 제품의 보관 안내 자료에는 활동 안내 항목이 REF-15 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-LM04

- Topic: `TOPIC_LIFESTYLE_MANAGEMENT`
- Product code: `NLR-LM04`
- 질문:
  - `rag-nlr-dev-034` · `EXPRESSION_SYNONYM`: NLR-LM04 제품, 몸 상태를 어떻게 적어 두라고 하는지 알고 싶어요.
  - `rag-nlr-dev-035` · `EXPRESSION_FRAGMENT`: NLR-LM04 상태 기록?
  - `rag-nlr-dev-036` · `EXPRESSION_LIMITED_TYPO`: NLR-LM04 제품의 상태 기록 안내에 대해 알려 주새요.
- Gold: `ev-nlr-2f0a774dbb35ee13` · `SYNTHETIC_NLR_CHUNK_004` · `$.records[16]`
  - 평가용 가상 설정에서 NLR-LM04 제품의 상태 기록 안내는 날짜·가상 코드·확인 표시 세 항목을 남기는 방식입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-be9ab236232e3188`: 평가용 가상 설정에서 NLR-LM04 제품의 포장 관리 코드는 PKG-12이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-eb8da0a59fcc0bf3`: 평가용 가상 설정에서 NLR-LM01 제품의 생활 관리 자료는 CARD-09 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-852770d3db44b40b`: 평가용 가상 설정에서 NLR-LM04 제품의 생활 관리 자료는 합성 색인의 IDX-12 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-32f153ca6aa5c887`: 평가용 가상 설정에서 NLR-ST04 제품의 보관 안내 자료에는 상태 기록 안내 항목이 REF-16 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-ST01

- Topic: `TOPIC_STORAGE`
- Product code: `NLR-ST01`
- 질문:
  - `rag-nlr-dev-037` · `EXPRESSION_CANONICAL`: NLR-ST01 제품의 보관 온도 정보에 대해 알려 주세요.
  - `rag-nlr-dev-038` · `EXPRESSION_SYNONYM`: NLR-ST01 제품, 어느 정도 온도에서 두어야 하는지 알고 싶어요.
  - `rag-nlr-dev-039` · `EXPRESSION_COLLOQUIAL`: NLR-ST01 제품 보관 온도 정보가 궁금해요.
- Gold: `ev-nlr-1aa28e0e1d30f949` · `SYNTHETIC_NLR_CHUNK_003` · `$.records[13]`
  - 평가용 가상 설정에서 NLR-ST01 제품의 보관 온도 정보는 가상 눈금 B 구간으로 지정됩니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-17ffd3d4ed85b527`: 평가용 가상 설정에서 NLR-ST01 제품의 포장 관리 코드는 PKG-13이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-11e98f2835b06d44`: 평가용 가상 설정에서 NLR-ST02 제품의 보관 안내 자료는 CARD-14 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-21ffe5178558ea30`: 평가용 가상 설정에서 NLR-ST01 제품의 보관 안내 자료는 합성 색인의 IDX-13 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-7adf81cd591a606a`: 평가용 가상 설정에서 NLR-MD01 제품의 복용 누락 안내 자료에는 보관 온도 정보 항목이 REF-17 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-ST02

- Topic: `TOPIC_STORAGE`
- Product code: `NLR-ST02`
- 질문:
  - `rag-nlr-dev-040` · `EXPRESSION_WORD_ORDER_PARTICLE`: 빛과 습기 차단 정보 알려 주세요, NLR-ST02 제품이요.
  - `rag-nlr-dev-041` · `EXPRESSION_FRAGMENT`: NLR-ST02 빛 습기 차단?
  - `rag-nlr-dev-042` · `EXPRESSION_LIMITED_TYPO`: NLR-ST02 제품의 빛과 습기 차단 정보에 대해 알려 주새요.
- Gold: `ev-nlr-a0dbcfe69c5dd7e5` · `SYNTHETIC_NLR_CHUNK_015` · `$.records[66]`
  - 평가용 가상 설정에서 NLR-ST02 제품의 빛과 습기 차단 정보는 남색 덮개와 마른 잎 표식을 함께 사용하는 것입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-9fc0f315ef2f6c23`: 평가용 가상 설정에서 NLR-ST02 제품의 포장 관리 코드는 PKG-14이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-459eef9a4943bc71`: 평가용 가상 설정에서 NLR-ST03 제품의 보관 안내 자료는 CARD-15 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-ee8fc504ee8b0363`: 평가용 가상 설정에서 NLR-ST02 제품의 보관 안내 자료는 합성 색인의 IDX-14 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-9bfc634ede75e949`: 평가용 가상 설정에서 NLR-MD02 제품의 복용 누락 안내 자료에는 빛과 습기 차단 정보 항목이 REF-18 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-ST03

- Topic: `TOPIC_STORAGE`
- Product code: `NLR-ST03`
- 질문:
  - `rag-nlr-dev-043` · `EXPRESSION_CANONICAL`: NLR-ST03 제품의 안전한 보관 위치 정보에 대해 알려 주세요.
  - `rag-nlr-dev-044` · `EXPRESSION_WORD_ORDER_PARTICLE`: 안전한 보관 위치 정보 알려 주세요, NLR-ST03 제품이요.
  - `rag-nlr-dev-045` · `EXPRESSION_COLLOQUIAL`: NLR-ST03 제품 안전한 보관 위치 정보가 궁금해요.
- Gold: `ev-nlr-0879269c045e9bf1` · `SYNTHETIC_NLR_CHUNK_001` · `$.records[2]`
  - 평가용 가상 설정에서 NLR-ST03 제품의 안전한 보관 위치 정보는 가상 보관함의 위쪽 C 칸으로 지정됩니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-6c7387e441da4478`: 평가용 가상 설정에서 NLR-ST03 제품의 포장 관리 코드는 PKG-15이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-841b4f9df5340f23`: 평가용 가상 설정에서 NLR-ST04 제품의 보관 안내 자료는 CARD-16 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-44fe7ed1aa3ccacf`: 평가용 가상 설정에서 NLR-ST03 제품의 보관 안내 자료는 합성 색인의 IDX-15 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-d8161b58ace5bcc2`: 평가용 가상 설정에서 NLR-MD03 제품의 복용 누락 안내 자료에는 안전한 보관 위치 정보 항목이 REF-19 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-ST04

- Topic: `TOPIC_STORAGE`
- Product code: `NLR-ST04`
- 질문:
  - `rag-nlr-dev-046` · `EXPRESSION_SYNONYM`: NLR-ST04 제품, 처음 담겨 있던 통을 어떻게 쓰는지 알고 싶어요.
  - `rag-nlr-dev-047` · `EXPRESSION_FRAGMENT`: NLR-ST04 원래 용기?
  - `rag-nlr-dev-048` · `EXPRESSION_LIMITED_TYPO`: NLR-ST04 제품의 원래 용기 보관 정보에 대해 알려 주새요.
- Gold: `ev-nlr-57eb969e36a8c857` · `SYNTHETIC_NLR_CHUNK_010` · `$.records[34]`
  - 평가용 가상 설정에서 NLR-ST04 제품의 원래 용기 보관 정보는 주황색 용기와 삼각형 뚜껑 표식을 유지하는 것입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-960014e5511121fc`: 평가용 가상 설정에서 NLR-ST04 제품의 포장 관리 코드는 PKG-16이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-a80713b219473496`: 평가용 가상 설정에서 NLR-ST01 제품의 보관 안내 자료는 CARD-13 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-17eefbfe56b3ebe8`: 평가용 가상 설정에서 NLR-ST04 제품의 보관 안내 자료는 합성 색인의 IDX-16 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-e063b949b80f0171`: 평가용 가상 설정에서 NLR-MD04 제품의 복용 누락 안내 자료에는 원래 용기 보관 정보 항목이 REF-20 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MD01

- Topic: `TOPIC_MISSED_DOSE`
- Product code: `NLR-MD01`
- 질문:
  - `rag-nlr-dev-049` · `EXPRESSION_CANONICAL`: NLR-MD01 제품의 복용 누락을 일찍 알았을 때의 안내에 대해 알려 주세요.
  - `rag-nlr-dev-050` · `EXPRESSION_SYNONYM`: NLR-MD01 제품, 약을 빠뜨린 걸 금방 알아챘을 때 어떻게 하는지 알고 싶어요.
  - `rag-nlr-dev-051` · `EXPRESSION_COLLOQUIAL`: NLR-MD01 제품 복용 누락을 일찍 알았을 때의 안내가 궁금해요.
- Gold: `ev-nlr-7260631dfebc8a1e` · `SYNTHETIC_NLR_CHUNK_013` · `$.records[48]`
  - 평가용 가상 설정에서 NLR-MD01 제품의 복용 누락을 일찍 알았을 때의 안내는 기록 카드의 절차 A를 조회하는 것입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-1394c9e9fc3cef04`: 평가용 가상 설정에서 NLR-MD01 제품의 포장 관리 코드는 PKG-17이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-df0f62fd1e12f902`: 평가용 가상 설정에서 NLR-MD02 제품의 복용 누락 안내 자료는 CARD-18 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-a9ce24c83c329baf`: 평가용 가상 설정에서 NLR-MD01 제품의 복용 누락 안내 자료는 합성 색인의 IDX-17 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-689c6e38063f9cfe`: 평가용 가상 설정에서 NLR-MI01 제품의 의약품 정보 자료에는 복용 누락을 일찍 알았을 때의 안내 항목이 REF-01 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MD02

- Topic: `TOPIC_MISSED_DOSE`
- Product code: `NLR-MD02`
- 질문:
  - `rag-nlr-dev-052` · `EXPRESSION_WORD_ORDER_PARTICLE`: 다음 복용 시각이 가까울 때의 안내 알려 주세요, NLR-MD02 제품이요.
  - `rag-nlr-dev-053` · `EXPRESSION_FRAGMENT`: NLR-MD02 다음 복용 시간 가까울 때?
  - `rag-nlr-dev-054` · `EXPRESSION_LIMITED_TYPO`: NLR-MD02 제품의 다음 복용 시각이 가까울 때의 안내에 대해 알려 주새요.
- Gold: `ev-nlr-499647cd28b18bd8` · `SYNTHETIC_NLR_CHUNK_009` · `$.records[32]`
  - 평가용 가상 설정에서 NLR-MD02 제품의 다음 복용 시각이 가까울 때의 안내는 절차 B와 시계 표식을 확인하는 것입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-06c72472e394915f`: 평가용 가상 설정에서 NLR-MD02 제품의 포장 관리 코드는 PKG-18이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-43aabc2da71d452d`: 평가용 가상 설정에서 NLR-MD03 제품의 복용 누락 안내 자료는 CARD-19 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-1f33c55200eae9e0`: 평가용 가상 설정에서 NLR-MD02 제품의 복용 누락 안내 자료는 합성 색인의 IDX-18 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-9e4f02831cb72f54`: 평가용 가상 설정에서 NLR-MI02 제품의 의약품 정보 자료에는 다음 복용 시각이 가까울 때의 안내 항목이 REF-02 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MD03

- Topic: `TOPIC_MISSED_DOSE`
- Product code: `NLR-MD03`
- 질문:
  - `rag-nlr-dev-055` · `EXPRESSION_CANONICAL`: NLR-MD03 제품의 중복 복용 금지 안내에 대해 알려 주세요.
  - `rag-nlr-dev-056` · `EXPRESSION_WORD_ORDER_PARTICLE`: 중복 복용 금지 안내 알려 주세요, NLR-MD03 제품이요.
  - `rag-nlr-dev-057` · `EXPRESSION_COLLOQUIAL`: NLR-MD03 제품 중복 복용 금지 안내가 궁금해요.
- Gold: `ev-nlr-457a3dc255dad0f7` · `SYNTHETIC_NLR_CHUNK_008` · `$.records[29]`
  - 평가용 가상 설정에서 NLR-MD03 제품의 중복 복용 금지 안내는 X 표식을 한 번만 남기는 규칙입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-3c50e3bbff9f3c36`: 평가용 가상 설정에서 NLR-MD03 제품의 포장 관리 코드는 PKG-19이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-d02d613d4b6df099`: 평가용 가상 설정에서 NLR-MD04 제품의 복용 누락 안내 자료는 CARD-20 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-433f938e54b18ce5`: 평가용 가상 설정에서 NLR-MD03 제품의 복용 누락 안내 자료는 합성 색인의 IDX-19 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-c87c43e02c12eb43`: 평가용 가상 설정에서 NLR-MI03 제품의 의약품 정보 자료에는 중복 복용 금지 안내 항목이 REF-03 표에만 표시되고 실제 내용은 비어 있습니다.

## NLR-MD04

- Topic: `TOPIC_MISSED_DOSE`
- Product code: `NLR-MD04`
- 질문:
  - `rag-nlr-dev-058` · `EXPRESSION_SYNONYM`: NLR-MD04 제품, 자꾸 빠뜨릴 때 의료진과 어떻게 상의하는지 알고 싶어요.
  - `rag-nlr-dev-059` · `EXPRESSION_FRAGMENT`: NLR-MD04 반복 누락 상담?
  - `rag-nlr-dev-060` · `EXPRESSION_LIMITED_TYPO`: NLR-MD04 제품의 반복해서 복용을 놓쳤을 때의 전문가 상담 안내에 대해 알려 주새요.
- Gold: `ev-nlr-70c12dd79c829891` · `SYNTHETIC_NLR_CHUNK_012` · `$.records[47]`
  - 평가용 가상 설정에서 NLR-MD04 제품의 반복해서 복용을 놓쳤을 때의 전문가 상담 안내는 누락 표식 세 개가 쌓이면 가상 상담 카드 C를 조회하는 것입니다.
- Hard negatives:
  - `SAME_FAMILY_DIFFERENT_ATTRIBUTE` · `ev-nlr-3ea074d4ed6c66bd`: 평가용 가상 설정에서 NLR-MD04 제품의 포장 관리 코드는 PKG-20이고 상자 모서리에는 은색 원이 표시됩니다.
  - `SAME_TOPIC_DIFFERENT_FAMILY` · `ev-nlr-c7b503a2c9c15047`: 평가용 가상 설정에서 NLR-MD01 제품의 복용 누락 안내 자료는 CARD-17 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다.
  - `LEXICAL_OVERLAP_UNSUPPORTED` · `ev-nlr-c237b952a224891e`: 평가용 가상 설정에서 NLR-MD04 제품의 복용 누락 안내 자료는 합성 색인의 IDX-20 행에 등록되어 있습니다.
  - `CROSS_TOPIC_OVERLAP` · `ev-nlr-f77b00440bbae294`: 평가용 가상 설정에서 NLR-MI04 제품의 의약품 정보 자료에는 반복해서 복용을 놓쳤을 때의 전문가 상담 안내 항목이 REF-04 표에만 표시되고 실제 내용은 비어 있습니다.
