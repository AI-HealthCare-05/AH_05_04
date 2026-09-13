# Decision 제안: #419 공통 복약 집계

| 항목 | 값 |
| --- | --- |
| Decision ID | PD-419-20260913 |
| 상태 | Proposed — 사용자 구현 진행 확인, 지정 리뷰 대기 |
| 구현·제품 담당 | 권가빈 (`hazelnutflavoured`) |
| 담당 리뷰 | 송은영 (`phina-io`): Backend/API·DB·Security; 남한솔 (`solia142`): Frontend 소비 계약 |
| 추적 | [Issue #419](https://github.com/AI-HealthCare-05/AH_05_04/issues/419) |

## 문제와 제안

#419는 두 계산식은 정의하지만 기간·적용 처방·0분모·반올림·응답 DTO를
제품 및 Frontend와 결정하도록 남겼다. 기존 API의 단순 내부 확장이 아닌 새 공유 계약이다.

[공통 복약 리포트 v1 제안](../../contracts/proposed/medication-report-v1.md)에
KST 종료일 포함 7/30일, SELF의 과거 version 보존, 저장된 현재 Check-in 집계,
두 지표 분자·분모·nullable 백분율, 원래 날짜별 기록과 정정 metadata를 명시했다.
상세 필드와 검증 사례의 정본은 해당 문서 하나로 유지한다.

## 대안 및 선택 요청

- 종료일 포함 기간을 제안한다. 완료된 날짜만 보여야 한다면 기본 종료일을 어제로 바꿔야 하므로 제품 결정이 필요하다.
- 기존 날짜별 API처럼 SELF의 보존된 과거 version을 포함한다. 현재 활성 처방만 계산하는 대안은 과거 기록이 처방 변경 때 사라진다.
- 분모 0은 null을 제안한다. 0%는 복용 실패와 기록 부재를 구분하지 못한다.
- 저장된 결과만 집계하고 기한이 지난 PENDING 수를 별도 표시한다. 조회에서 무응답을 UNCONFIRMED로 추정하는 대안은 원본 기록과 불일치한다.
- 한 SELECT의 행들로 상세와 집계를 함께 계산한다. 별도 리포트 저장 모델·cache·LLM·DB schema 추가는 필요하지 않다.

## 상태와 다음 단계

2026-09-13 사용자가 제안된 집계 기준으로 구현 진행을 확인했다.
이 확인을 송은영·남한솔의 지정 리뷰 승인이나 공개 승인으로 대신 기록하지 않는다.

Router → Service → Repository, 리포트 전용 DTO, OpenAPI·실제 PostgreSQL 통합 테스트,
Frontend 합성 fixture와 API 문서를 같은 작업 브랜치에서 구현했다.
[검증 기록](../../validation/track-b/issue-419-medication-report.md)에 실행 결과를 남긴다.
계약은 Proposed에 유지하며 지정 리뷰·병합·Current 승격은 아직 하지 않았다.
