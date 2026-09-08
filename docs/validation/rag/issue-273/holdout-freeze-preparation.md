# Issue #273 HOLDOUT Freeze Preparation

> 이 문서는 저장소 로컬 비런타임 준비 projection입니다.
> `PREPARATION_READY`는 접근 승인이나 Freeze 완료가 아닙니다.

## 현재 상태

- Dataset: `rag-natural-language-retrieval-dev@1.0.0` (`DRAFT`, unfrozen)
- Dataset manifest SHA-256: `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`
- HOLDOUT: 계획 `40` / 작성 `0`
- 접근 승인: `false`; Freeze 기록: `false`; Actual Run: `NOT_CREATED`
- Release eligible: `false`; Production은 닫혀 있습니다.

## 담당자와 역할 분리

- 구현 담당: 정현우 (`@ceohwj`, `EVALUATION_IMPLEMENTER`)
- Product/Evaluation 검토: 권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)
- 요청 Dataset Custodian: 송은영 (`@phina-io`, `DATASET_CUSTODIAN`)
- `@phina-io`가 접근 통제를 구현하면 김지혜 (`@Jye-rookie`)가 독립 승인합니다.

## HOLDOUT 계획

- `TOPIC_LIFESTYLE_MANAGEMENT`: `8`
- `TOPIC_MEDICATION_INFORMATION`: `8`
- `TOPIC_MISSED_DOSE`: `8`
- `TOPIC_PRECAUTIONS`: `8`
- `TOPIC_STORAGE`: `8`

Leakage 검증 축은 `question_template`, `source_segment`, `medication_family`, `transform_origin` 네 개입니다.

## 접근 통제 완료 조건

- 기본 거부와 명시적 작성자·Custodian·보호 Runner identity만 허용합니다.
- 일반 개발 checkout, 일반 CI, PR artifact, Issue와 로그에는 HOLDOUT을 노출하지 않습니다.
- grant, revoke, read, write, freeze, run을 actor·UTC timestamp·logical ID로 감사합니다.
- 접근 통제 구현자는 자신의 구현을 최종 승인할 수 없습니다.

## 정확한 후속 시점

1. 이 준비 PR 병합 직후 전용 protected Retrieval Runner Issue를 생성합니다.
2. 전용 Issue에서 보호 환경 ACL·감사·service identity를 구현합니다.
3. 독립 Dataset Custodian의 접근 승인 event가 생성된 뒤에만 HOLDOUT 40개 작성을 시작합니다.
4. 보호 환경에서 네 leakage 축의 교집합이 모두 0이고 40개 검토가 끝난 뒤 Freeze합니다.
5. 저장소에는 raw content 없이 receipt id/version/raw SHA-256, actor, timestamp와 집계만 기록합니다.
6. #178의 실제 Retriever Adapter와 보호 Runner가 준비된 뒤에만 DEV 60 + HOLDOUT 40을 실행합니다.

## 공개 금지

HOLDOUT 질문·Gold·hard negative label·authoring identity digest·fingerprint/HMAC 값·key material·credential·보호 저장 위치는 이 저장소에 기록하지 않습니다.
OTC 범위는 Issue #278에서 별도로 진행하며 #273 HOLDOUT에 혼합하지 않습니다.

## 남은 Blocker

- `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`
- `BLOCKED_BY_RAG_14_ADAPTER`
- `WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION`
- `WAITING_FOR_HOLDOUT_FREEZE`

Preparation self hash: `b07c06ff49c5b2f833db864c0b5ee95240b98e18275a96a4090bb585a0acb65a`
