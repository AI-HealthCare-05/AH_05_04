# Decision: #419 공통 복약 집계

| 항목 | 값 |
| --- | --- |
| Decision ID | PD-419-20260913 |
| 상태 | Accepted · Implemented — Backend #478, Frontend #574 병합 |
| 구현·제품 담당 | 권가빈 (`hazelnutflavoured`) |
| 리뷰 기준 | 구현 PR별 책임 reviewer 1명. Backend #478은 송은영 (`phina-io`), Frontend #574는 권가빈 (`hazelnutflavoured`) APPROVED; blocking comment 0건 |
| 추적 | [Issue #419](https://github.com/AI-HealthCare-05/AH_05_04/issues/419) |

## 문제와 결정

#419는 두 계산식만 정의하고 남겨 둔 기간·적용 처방·0분모·반올림·응답 DTO를
제품 및 Frontend 계약으로 결정했다. 기존 API의 단순 내부 확장이 아닌 새 공유 계약이다.

[공통 복약 리포트 v1](../../contracts/current/medication-report-v1.md)에
KST 종료일 포함 7/30일, SELF의 과거 version 보존, 저장된 현재 Check-in 집계,
두 지표 분자·분모·nullable 백분율, 원래 날짜별 기록과 정정 metadata를 명시했다.
상세 필드와 검증 사례의 정본은 해당 문서 하나로 유지한다.

## 채택한 기준

- 종료일을 포함한 기간을 채택한다. 완료된 날짜만 보여 주는 대안과 달리 기본 종료일은 오늘이다.
- 기존 날짜별 API처럼 SELF의 보존된 과거 version을 포함한다. 현재 활성 처방만 계산하는 대안은 과거 기록이 처방 변경 때 사라진다.
- 분모 0은 null을 채택한다. 0%는 복용 실패와 기록 부재를 구분하지 못한다.
- 저장된 결과만 집계하고 기한이 지난 PENDING 수를 별도 표시한다. 조회에서 무응답을 UNCONFIRMED로 추정하는 대안은 원본 기록과 불일치한다.
- 한 SELECT의 행들로 상세와 집계를 함께 계산한다. 별도 리포트 저장 모델·cache·LLM·DB schema 추가는 필요하지 않다.

## 구현 및 승인 상태

2026-09-13 확인한 집계 기준은 Backend #478과 Frontend #574로 develop에 병합됐다.
Backend #478은 송은영 (`phina-io`), Frontend #574는 현재 팀 규칙에 따른 책임 reviewer 1명인
권가빈 (`hazelnutflavoured`)이 승인했고 blocking comment는 0건이다. Frontend #574에 대한
`phina-io`의 추가 승인은 closeout 조건이 아니다.

Router → Service → Repository, 리포트 전용 DTO, OpenAPI·실제 PostgreSQL 통합 테스트와
Frontend 소비 UI가 현재 runtime에 반영됐다.
[검증 기록](../../validation/track-b/issue-419-medication-report.md)에 실행 결과를 남긴다.
계약은 현재 코드·OpenAPI·테스트와 함께 `current/`에서 관리한다. Production 공개 승인은 별도다.
