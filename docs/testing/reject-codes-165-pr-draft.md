# PR 제목

✨ feat: #165 제품 reject_code v1 판정·실패 감사·Run 계약 버전 구현

# PR 본문

## 작업 내용

MFDS 제품 수집에서 ITEM_SEQ 누락·타입 오류·페이지 간 중복을 전체 페이지 기준으로 판정하고,
안전한 reject_code와 원본 위치를 실패 Run 및 REJECTS Artifact에 연결합니다.
첫 오류에서 중단하거나 중복의 첫 행을 통과시키지 않고, 해당 레코드를 모두 기록합니다.
필수 식별자 오류는 양수 Hard Limit에서도 FAILED / PARSER_VALIDATION_FAILED로 처리하며
Snapshot 생성과 CURRENT 변경을 차단합니다.

## 관련 Issue

Refs #165, #362. 선행 PR #436 기준 head `c1587f7`에서 구현했습니다.
#165 전체 완료를 의미하지 않으므로 Closes를 사용하지 않습니다.
#166 / PR #372 Receipt 소비 연결과 합의된 D-02 후속 범위는 이번 변경에 포함하지 않습니다.

## 📄 상세 내용

- [x] `source-reject-codes@1`의 세 코드와 적용 Operation을 Python에서 검증합니다.
  코드: `ITEM_SEQ_REQUIRED`, `INVALID_ITEM_SEQ_TYPE`, `DUPLICATE_ITEM_SEQ`.
- [x] 전체 페이지 2-pass 판정으로 레코드당 대표 코드 하나를 기록합니다.
  원문 ITEM_SEQ를 trim·문자열 강제 변환·dedupe하지 않습니다.
- [x] `page[n].record[i]` 원본 위치와 Artifact 결속을 검사하고, 동일 위치의 재저장을 차단합니다.
- [x] 실제 클라이언트가 PK 오류로 pages를 비우는 경우, 보존 원본의 checksum·페이지·본문·총건수·Receipt를
  재검증해 실패 감사 입력만 복원합니다. 기존 실패 결과를 Snapshot 적격 상태로 승격하지 않습니다.
- [x] Run에 nullable `reject_code_contract_version`을 저장하고 Receipt 조회에 반영합니다.
  과거 NULL은 보정하지 않습니다. 새 실행은 `mfds-product-reject-parser@1`에 결속합니다.
- [x] 미등록 코드·미지원 버전·범위 밖 Operation을 Parser 실패로 차단합니다.
  기존 통신 실패는 원래 실패 코드와 반환 유형을 유지합니다.
- [x] 파일 보존 전 Service와 DB Repository에서 재검증합니다.
  호출자 transaction·최소 권한·기존 미참조 파일 정리 경로를 유지합니다.

신규 migration `165a0b1c2d3e`는 #436의 `362c3d4e5f60`을 부모로 둡니다.
새 버전 컬럼에는 lifecycle UPDATE 권한을 추가하지 않습니다.
버전 이력이 있는 DB의 downgrade는 데이터 보존을 위해 차단하며, 이력이 없으면 되돌릴 수 있습니다.
모든 migration을 일괄적으로 forward-fix 전용으로 바꾸는 변경은 아닙니다.

계약은 `docs/contracts/proposed/post-mvp-1/source-reject-codes-v1.md`에 두고,
Decision과 테스트를 함께 갱신했습니다. 담당 리뷰 전이므로 승인 완료나 current 계약으로 표시하지 않습니다.
이 계약을 적용하는 호출자는 새 `ingest_and_persist_product_run` 경로를 사용해야 합니다.
기존 내부 저장 함수의 과거 parser 호환 입력은 유지하되, 버전 없는 새 REJECTS 저장은 차단합니다.
외부 수집 스케줄러·Runtime 활성화·사용자 입력 정규화는 변경하지 않습니다.

## 구조 복잡성 검토

기존 Service/Repository·Artifact 저장소와 transaction을 사용합니다.
checksum 이전 실패에는 #436 canonical provenance를 만들 수 없어 Run 버전 컬럼이 필요합니다.
새 프레임워크·의존성·스케줄러·캐시는 추가하지 않았습니다.
비즈니스 로직과 데이터 무결성 검증은 Python에 두고 Trigger·RLS·업무 DB 함수/프로시저를 추가하거나 복원하지 않았습니다.

## Backend 변경 검토

- [x] 코드·DB·문서의 계약 버전 필드와 오류 의미를 정렬했습니다.
- [x] 기존 Writer 최소 권한을 유지하고 새 버전 컬럼 수정 불가를 검증했습니다.
- [x] 합성 데이터만 사용하고 원문·미등록 코드 입력의 오류 메시지 노출을 방지했습니다.
- 사용자 리소스 조회·수정 API 변경은 없습니다.

## 테스트

- 신규 DB 통합 25건 + Source 단위 418건: **443 passed**.
- 전체 migration: **180 passed**.
- RAG·Evaluation: **2429 passed, 8 skipped**.
- Writer·프로비저닝·이미지·Backend Source 경계: **25 passed**.
- 전용 DB 설정 후 보존·삭제 경로: **57 passed**.
- 기존 Snapshot lifecycle: **31 passed**.
- Ruff·format·Mypy, DB 로직 금지·보호 쓰기·테스트 inventory 검사 통과.
- 전용 PostgreSQL 17에서 최신 head `165a0b1c2d3e` 검증 통과:
  Trigger/RLS/제거 대상 함수 **0개**.

위 실행들은 일부 테스트가 중복되므로 합산한 총 테스트 수로 표시하지 않습니다.
`scripts/ci/run_test.sh`는 `envs/.local.env` 부재로 준비 단계에서 중단했고,
관련 검증은 작업 전용 DB 환경에서 직접 실행했습니다. 전체 CI 통과를 의미하지 않습니다.
원격 CI 결과는 푸시 후 확인해야 합니다. 운영·AWS·팀원 DB에는 적용하지 않았습니다.

## 보안·데이터 확인

- [x] 인증정보·`.env`·실제 환자정보·원본 진료기록을 포함하지 않습니다.
- [x] 기존 적용 migration 이력과 금지 DB 로직 예외 목록을 확대하지 않았습니다.

## 리뷰 요청 사항

- 구현 담당자: 김지혜
- 담당 리뷰어: 송은영 — Run 버전·Writer 권한·transaction·migration 및 기존 데이터 보존
- Source/Parser 검토: 정현우 — 제공된 v2 의견과 세 코드의 판정·실패 의미 정합성
- 제품·Safety 관련 검토: 권가빈 — 해당 공개 게이트와 기존 안전 경계 유지
- 사전 승인 완료로 간주하지 않습니다. 공개·Production 게이트는 기존 상태를 유지합니다.

## 🧪 PR Checklist

- [x] 단계별 커밋과 관련 구현·계약·migration·회귀 검증을 완료했습니다.
- [ ] #436 병합 후 최신 develop과 base/head 및 migration head를 최종 확인했습니다.
- [ ] develop 대상 PR을 생성하고 원격 CI를 확인했습니다.
- [ ] 담당 리뷰어를 지정하고 승인을 받았습니다.
- [ ] blocking 리뷰 코멘트를 해결했습니다.

---

이 파일은 로컬 초안입니다. 원격 PR 생성·푸시는 하지 않았습니다. **푸시 필요.**
