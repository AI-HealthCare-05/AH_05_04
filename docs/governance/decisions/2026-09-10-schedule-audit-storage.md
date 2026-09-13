# PD-423-20260910: Schedule Audit 저장 통제 보완

| 항목 | 값 |
| --- | --- |
| 상태 | 사용자 작업 지시에 따른 구현 · 지정 도메인 리뷰/머지 대기 |
| 선행 승인 | [PD-417](2026-09-10-track-b-schedule-contract.md), PR #424의 D1–D5 |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 책임 리뷰 | 송은영 (`phina-io`) — Backend·DB·Security; 남한솔 (`solia142`) — revision/소비 의미 |
| 작업 근거 | 2026-09-10 현재 작업 대화에서 baseline JSON·감사 보호 보완안을 제시한 뒤 사용자가 `feat/423-schedule-audit` 작업 및 commit/push/PR 생성을 지시; 이후 **DB trigger 추가 금지**를 명시 |
| 계약 | [일정 정합화 v1](../../contracts/targets/post-mvp-1/track-b-schedule-reconciliation-v1.md) |

## 기존 승인과 이번 보완의 구분

PD-417은 별도 migration 증빙 baseline과 append-only 감사 이력을 요구하지만 보관 매체를 정하지
않았고, 설계 설명에는 새 DB trigger가 필요하지 않다고 적었다. 이 문서는 그 두 구현 선택을
명시한다. PR #424의 승인에 이번 보완까지 포함됐다고 소급 주장하지 않는다. 사용자의 최종 지시에 따라 새 DB trigger는 추가하지 않는다. 기존 USER/SCHEDULER,
revision·snapshot·공개 API·거래 순서는 변경하지 않는다.

- baseline은 새 공유 테이블을 만들지 않고 migration 실행자가 지정한 별도 JSON artifact로 보존한다.
- 감사 Repository는 append만 제공하고 #398의 기존 최소 권한 정책에 감사 테이블을 등록한다.
  Runtime은 SELECT/INSERT만 허용하며 UPDATE/DELETE/TRUNCATE는 DB ACL이 거부한다.
  Schedule 상태 전이와 감사 append는 Service에서 같은 transaction으로 수행한다.
- DB는 FK RESTRICT·revision step·unique·actor constraint를 담당한다. 테이블 owner/관리자는 별도
  운영 신뢰 경계이며 Runtime 권한으로 감사 이력을 수정·삭제할 수 없다. 기존 B1/B3 migration을
  수정하거나 새 DB trigger·RULE·stored procedure로 우회하지 않는다.

## Baseline 생성·접근·보존

`423a1b2c3d4e` upgrade는 Schedule과 Time을 잠근 상태에서 현재 schedule ID·revision·설정과 마지막
설정 time 집합을 수집한다. 기존 row가 있으면 Alembic 실행 시
`-x schedule_baseline_path=/protected/directory/baseline.json`이 필요하다. 없으면 무변경 실패한다.
빈 신규 DB에는 baseline artifact가 필요하지 않다.

경로는 저장소 밖의 절대 경로이며, 부모 디렉터리는 실행자 소유·0700이어야 한다. 파일은 0600으로
독점 생성하고 기존 파일/심볼릭 링크는 덮어쓰지 않는다. payload를 표준 출력·로그·오류에 출력하지
않는다. 운영자는 이 파일을 제한된 migration 증빙 저장소에 보존하며 Git에 추가하지 않는다.
기존 Schedule/감사 이력에 적용되는 승인된 보존·계정 삭제·legal hold 정책을 함께 적용한다.
이번 구현은 별도 TTL이나 자동 삭제 작업을 만들지 않는다.

`captured_at`은 증빙 수집 시각이며 과거 변경 시각이 아니다. baseline은 감사 테이블에 넣지 않고,
과거 actor나 revision 전이를 만들지 않는다. 기존 CANCELLED occurrence의 `cancelled_at`은 null로
남긴다. baseline time은 기존 저장 정밀도를 그대로 기록한다.

파일 쓰기와 PostgreSQL commit은 하나의 분산 transaction이 아니므로 artifact의
`database_commit_asserted=false`는 의도적이다. 실행자는 Alembic version과 성공 실행 증빙을 별도로
연결한다. DB rollback 이후 파일이 남을 수 있으며 재시도는 새 경로로 수행한다. 파일만으로 upgrade
성공을 주장하거나 남은 파일을 자동 삭제하지 않는다.

## 이력 보호와 rollback

감사 parent/actor FK는 RESTRICT이며 revision step·schedule/to_revision unique·actor 조합을 DB가
검증한다. 최신 develop의 #398 정책에 따라 명시적 Service→Repository 호출과 savepoint로
실패한 mutation을 복원한다. ORM event나 DB trigger를 사용하지 않는다. Migration은 새 테이블의
상속 기본 ACL을 회수하고, upgrade 이후 기존 `provision_database_roles.py`를 재실행해 Runtime에
SELECT/INSERT만 부여해야 한다. 직접 SQL도 Runtime의 UPDATE/DELETE/TRUNCATE는 거부되며
Source Writer에는 접근을 허용하지 않는다. Owner/관리자 권한은 이 Runtime 보호 범위 밖이다.

Downgrade는 ACCESS EXCLUSIVE 잠금 아래 감사 행 또는 non-null 취소 시각이 있으면 거부한다.
기존 nullable 취소 시각만 있고 새 감사 이력이 없으면 새 스키마를 내릴 수 있으며 외부 baseline
artifact는 삭제하지 않는다. 실제 기록이 있는 시스템의 downgrade는 데이터 삭제로 우회하지 않는다.

[검증 기록](../../validation/issue-423-schedule-audit.md)과 #202·#203 인계 기준을 함께 검토한다.
기존 Privacy Production 및 Track C/F 공개 gate는 유지한다.
