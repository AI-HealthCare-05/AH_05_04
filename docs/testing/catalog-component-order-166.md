# #166 D-04 Component 순서 검증

기준 develop: `8010dfc`. 합성 fixture만 사용. MFDS API 재호출은 수행하지 않았다.

## 수정 전 재현

서로 다른 Ingredient가 같은 Product 순서를 사용하는 입력이 기존 구성원 검증을
통과했다. Service도 승인 verifier 호출에 진입했다. 재현 테스트에서 2 failed, 2 passed를 확인했다.

## 수정 후 집중 검사

`ai_worker/tests/rag/catalog/` 전체 204 passed.
새 사례는 순서 충돌 거부·승인 및 저장 호출 0건·기존 검증 report를 전달한 저장 준비의
재검증·입력 순열과 정확 중복의 bytes/hash 보존·기존 반복 성분 충돌 유지다.

검증한 것은 명시된 Component order의 일관성이다. 실제 MFDS 성분별 원본 키나 순서의
안정성을 검증한 것이 아니며, Authority DB 자연키 전환을 완료한 것도 아니다.
새 RLS·Trigger·업무용 DB 함수·migration은 없다.

## 전체 로컬 검사 (2026-09-11)

Backend·Worker 의존성을 설치한 전용 작업 환경에서 전체 CI 스크립트가 통과했다.
최초 실행은 새 환경의 Backend 의존성 미설치로 중단됐고, app/worker 그룹 설치 후 재실행했다.

| 검사 | 결과 |
| --- | --- |
| Migration | 202 passed, 3 skipped |
| Backend·계약·PostgreSQL | 1896 passed, 65 skipped |
| Redis | 23 passed |
| Worker | 2979 passed, 8 skipped |
| Ruff·format·Mypy | 통과 (Mypy 575개 파일) |

집중 검사와 전체 검사의 수치는 합산하지 않는다. 원격 CI·담당 리뷰·실제 MFDS 재수집
증빙은 아니다. #454 코드와 공용 설정·문서 목차는 이번 브랜치에 반영하지 않았다.

## #454 병합 반영

2026-09-11 develop `7f6cc85`를 fast-forward로 반영했다. 위 전체 검사 수치는
반영 전 `8010dfc` 기준 기록이다. D-04 변경을 보존했으며 파일 충돌은 없었다.

반영 후 Catalog 전체와 OCR LLM Worker 연결·독립 import 검사를 함께 실행해
217 passed를 확인했다. 반영 후 전체 CI 재실행 결과로 표현하지 않는다.

## MFDS 상세 응답 확인 도구

공식 내장 Swagger에서 주성분 상세 Operation과 후보 필드를 확인했다.
`scripts/rag/probe_mfds_component_fields.py`의 합성 검사 5 passed:
후보키 중복·누락, 응답 envelope, Provider 오류 문구 비노출, 인증 URL·키 비노출.
기본 실행은 NOT_RUN이며 실제 API 응답은 아직 확인하지 않았다. 키는 사용자가 별도 보관한다.

## 원본 키·반복 성분·DB 전환 구현 후 검사 (2026-09-11)

기준 develop `7f6cc85` + D-04 작업 변경. 위 초기 단계의 "migration 없음"·"미전환" 설명은
당시 기록이다. 최신 범위는 [D-04 결정안](../governance/decisions/2026-09-11-catalog-component-occurrences.md)을 따른다.
사용자 제공 MFDS 통계는 [설계 기록](../designs/jye-rookie/issue-166-d04-component-order.md)에
SAMPLED_NOT_VERIFIED로 연결했다. 실제 원문이나 인증키는 저장하지 않았다.

| 검사 | 결과 |
| --- | --- |
| Catalog + MFDS probe 집중 검사 | 241 passed |
| Migration | 202 passed, 3 skipped |
| Backend·계약·PostgreSQL 전체 실행 | 1900 passed, 65 skipped |
| Redis 통합 | 24 passed |
| Worker | 3029 passed, 8 skipped |
| Ruff·format·Mypy | 통과 (Mypy 581개 파일) |

전체 CI 스크립트 exit 0. 전용 PostgreSQL 17·Redis 7과 합성 자료만 사용했다.
최종 migration head `e8c41a09d652`, Trigger·RLS·제거 대상 함수 0개.
최초 CI preflight에서 신규 revision ID 중복을 발견해 새 ID로 정정한 후 전체 검사를 통과했다.
기존 적용 migration은 수정하지 않았다. 로컬 검증이며 원격 CI·리뷰 승인·운영 적용을 뜻하지 않는다.

검증 경계:

- 선택 원본 키가 없는 기존 입력의 golden bytes·digest 유지.
- 명시적인 다른 원본 키를 가진 반복 성분의 별도 ref, 순열·정확 중복에 대한 안정성.
- 같은 원본 키의 다른 내용 또는 같은 제품의 같은 order를 승인·저장 전에 거부.
- 실제 DB에서 반복 성분·함량 문자열·release_profile을 저장·복원하고 Set 재사용 및 Candidate 인계.
- 기존 순서 충돌 시 upgrade 무보정 거부. 반복 성분/방출형 손실 시 downgrade 거부.
- MFDS 후보키·필수 값 누락 또는 원료 매핑 불일치 거부. 배열 index/일련번호로 order를 추론하지 않음.

전체 실행 중 두 경계 사례를 보강했으므로 위 전체 검사 수치에 추가 사례를 합산하지 않는다.
최종 테스트 파일의 별도 집중 실행 결과는 아래에 기록한다.

최종 `tests/integration/rag/test_catalog_storage_roundtrip.py`: **27 passed**.
방출형 단독 downgrade 차단과 DB 방출형 변조 read-back 거부를 포함한다.
기존 순서 충돌 무보정 차단, 반복 성분만 있는 경우의 downgrade 차단, 기존 행의
upgrade/downgrade 왕복도 각각 통과했다. 위 전체 검사와 중복되므로 합산하지 않는다.
실제 MFDS 전체 고유성·공식 Ingredient 매핑·순서 승인·자동 적재 완료 증빙은 아니다.

## 전체 키·매핑·순서 추가 확인 도구

[MFDS 추가 확인 기록](mfds-component-key-audit-166.md)을 추가했다. 전체 페이지 감사 도구와
기존 소량 probe의 합성 검사 15 passed, Ruff·format 통과. 기존 probe의 통신 부분을 재사용한다.
이 추가 변경은 위 전체 CI 실행 이후이며 전체 CI를 다시 실행한 것으로 표현하지 않는다.
실제 전체 조회는 사용자 키를 숨김 입력하는 로컬 실행이 필요하다. 현재 전체 고유성·공식
매핑·순서 의미가 검증 완료된 것은 아니다.

## 전체 관찰 결과 보충

사용자 제공 126,825행 × 2회 전체 페이지 결과를 수령했다. 완전 키 95,128행은 중복 0건,
불완전 키는 31,697행이다. 반복 제품·원료 그룹 3,613개, 동일 이름·복수 원료코드 그룹
281개가 관찰됐다. 이전 실행 대기 기록 이후의 상태와 남은 의미 확인은
[전체 감사 결과](mfds-component-key-audit-166.md)를 따른다. 실제 자동 적재 완료나 공식 매핑 승인으로 해석하지 않는다.
