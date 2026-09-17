# Product Decision Candidate: REQUEST Authority Persistence Contract (#713)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-713-20260917` |
| 상태 | Proposed / Review pending · Issue #713 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend·Data·Security / DB schema · migration · Repository 쓰기 경계 |
| 필요 교차 리뷰 | 김지혜 (`@Jye-rookie`) — Worker & Source Provenance / 권가빈 (`@hazelnutflavoured`) — PM·Privacy Gate |
| 추적 Issue | [#713](https://github.com/AI-HealthCare-05/AH_05_04/issues/713) |
| 계약 문서 | [REQUEST Authority Persistence 계약 v1](../../contracts/proposed/post-mvp-1/request-authority-persistence-v1.md) |
| 상위·관련 결정 | [`PD-672-20260916`](./2026-09-16-sync-guide-evidence-authority.md), [`PD-180-EM-20260916`](./2026-09-16-endpoint-member-authority-contract.md) |

---

## 1. 배경

`PD-672-20260916`은 동기 Guide 경로의 authority assembly를 pure/read-only seam으로 확정하면서
Production DB 어댑터를 명시적으로 후속 범위로 남겼다. 사유는 "실제 프로덕션 저장소의 Decision
테이블 스키마 및 영속 위치가 미확정"이라는 것이었다.

Issue #709는 그 Production Reader 구현 가능 여부를 조사했고 Storage Authority Gate가 실패했다.
develop에는 REQUEST Guard·Source Decision·Member Decision의 authoritative historical
persistence가 존재하지 않으며, 가장 가까운 기존 표들은 모두 현재 상태이거나 호출자 주장이었다.
그 결과 #709는 `PRODUCTION_READER_BLOCKED_BY_AUTHORITY_PERSISTENCE`로 fail-closed 종료했다.

본 결정은 그 단일 차단 요인만 제거한다.

## 2. 결정

REQUEST 시점에 실제로 발행된 authority observation을 immutable historical 증거로 저장하고,
exact `ImmutableArtifactRef`로 조회하는 persistence 계약을 확정한다. 세부는 계약 문서를 따른다.

핵심:

1. **Historical · request-bound**: 현재 Source 승인 상태, CURRENT Snapshot, 호출자가 전달한
   opaque guard ref를 authority로 재사용하지 않는다. 조회 시점에 과거 Decision을 재구성하지 않는다.
2. **Typed 3표 분리**: `rag_request_guard_authority`, `rag_request_source_decision`,
   `rag_request_member_decision`. generic JSON authority 표를 만들지 않는다.
3. **Writer가 identity를 소유**: artifact identity는 기존 RFC 8785 JCS canonical helper로 계산한
   semantic projection digest다. 새 hash domain을 정의하지 않고 caller가 identity를 고를 수 없다.
4. **actual PASS/FAIL만 저장**: `ObservedDecisionOutcome` 어휘를 그대로 쓰며 unknown·NULL을 PASS로
   해석하지 않는다.
5. **Append-only**: update/delete API를 두지 않는다. 동일 identity·동일 내용 retry는 결정론적
   idempotent이고, 동일 identity·다른 내용은 덮어쓰지 않고 fail closed다.
6. **Exact lookup만**: `artifact_code`·`version`·`content_sha256` exact equality 조회만 제공하고
   latest/CURRENT fallback을 두지 않는다. 정상 no-row만 `None`이며 손상·중복은 드러낸다.
7. **DB 로직 금지 준수**: Trigger·RLS·Stored Procedure·사용자 정의 DB 함수를 추가하지 않는다.
   판정과 접근 검증은 Python Repository에서 명시적으로 수행한다.

## 3. 권위 한계

본 결정은 다음을 의미하지 않는다.

```text
#709 Production GuideEvidenceAuthorityReaderPort 구현
#180 runtime orchestration 완료
Track F PUBLIC 활성화
Decision 정책(어떤 조건에서 PASS인지) 확정
```

Writer는 Decision을 새로 평가하지 않는다. 이미 authoritative한 결과를 기록할 뿐이며, 그 결과를
누가 어떤 규칙으로 발행하는지는 #174/#180 범위로 남는다. 따라서 본 결정만으로 동기 Guide 경로가
실제 authority를 갖게 되는 것은 아니다.

## 4. 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| `catalog_source_approval` 재사용 | 운영자 기준 현재 승인 상태이며 요청·사용자·REQUEST stage에 결속되지 않는다 |
| `runtime_guard_decision_ref` 확장 | 호출자가 전달한 값을 그대로 기록하는 경로라 authoritative observation이 될 수 없다 |
| 단일 generic authority 표 + JSON payload | 종류별 NOT NULL·CHECK·FK 불변식을 표현할 수 없어 fail-closed 보장이 약해진다 |
| `source_snapshot_id`에 FK 부여 | Source cleanup·retention 수명주기와 authority 증거 보존을 결합시킨다. 정합성은 writer 검증으로 충분하다 |

## 5. 후속

`#713` 병합 후 `#709`를 재개해 Production `GuideEvidenceAuthorityReaderPort`를 본 read
primitive 위에 구현한다. Decision 발행 경로(#174/#180) 연결은 별도로 남는다.
