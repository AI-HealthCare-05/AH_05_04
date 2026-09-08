# #144 빈 검수 필드 생성 Decision 초안

- 상태: **Proposed · 승인 미기록 · 미구현**
- 작성일: 2026-09-08
- 작성 담당: 김지혜 (`Jye-rookie`)
- 검토: 송은영 (`phina-io`) — 공유 API·DB, 남한솔 (`solia142`) — DOC-03 소비 확인
- 근거: [이슈 #144](https://github.com/AI-HealthCare-05/AH_05_04/issues/144), [공동 범위 협의](https://github.com/AI-HealthCare-05/AH_05_04/issues/144#issuecomment-5489219395)
- 상세 변경안: [빈 검수 필드 생성 변경안](../../contracts/proposed/ocr-empty-review-fields-144.md)

## 배경

함량·단위를 인식하지 못하면 DB 행과 field_id가 없어 기존 PATCH로 입력할 수 없다.
이슈 댓글은 OCR·Backend 공동 작업 범위를 정했으며 상세 변경안의 승인 증거는 아니다.

## 제안과 이유

기존 약품 행에서 누락된 검수 필드를 구조화 단계에 보충한다.
LLM의 기존 네 필드에 함량·단위를 더한 여섯 유형을 두 경로에 적용한다.
규칙 경로의 용량 값·단위 동시 누락 테스트도 같은 집합으로 충족한다.
빈 필드 생성과 최종 필수값 검증을 분리하여 Optional의 의미를 유지한다.

신규 upsert API는 이슈 범위 밖이며 기존 field_id 기반 PATCH를 재사용한다.
화면에서만 빈칸을 추가하면 저장할 field_id가 없는 원인을 해결하지 못한다.
실패한 인식값을 유지하는 방식은 grounding 원칙과 맞지 않으므로 사용하지 않는다.

## 영향 및 검토 근거

공개 API 형태는 유지하지만 결과 필드의 존재 여부가 바뀌는 공유 계약 변경이다.
DB 모델은 nullable 값과 Optional null 확정을 이미 지원한다. 신규 migration 필요성은
실제 스키마·통합 검증으로 판단한다. 기존 결과 backfill과 날짜 정책 변경은 제외한다.

상세 정책·인수 테스트는 위 변경안 한 곳에서 관리한다. 코드·테스트·current 계약을
같은 구현 PR에서 정렬하고, 승인 시 실제 리뷰 링크를 기록한다.
이 초안은 사용자 공개 승인이나 #144 완료를 선언하지 않는다.
