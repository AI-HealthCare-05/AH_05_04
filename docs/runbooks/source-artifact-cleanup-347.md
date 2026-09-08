# #347 Source Artifact 정리 — 합성 검증 및 운영 인계 초안

상태: Local 합성 구현 검토용. 운영 실행 승인·#347 전체 완료 인계는 아직 아니다.
정책: [#335 Proposed 정책](../contracts/proposed/post-mvp-1/source-artifact-retention-cleanup.md)
증빙: [단계별 검증](../testing/source-artifact-cleanup-347.md)

## 현재 실행 가능한 범위

`SyntheticCleanupLab`가 직접 만든 임시 ASCII 파일을 pytest에서 정리하는 경로만 제공한다.
기존 Source 저장소 경로·사용자 파일·S3 bucket을 입력하는 삭제 API/CLI는 없다.
실제 Local reader는 소유/생성 시각을 추정하지 않으며 SQL reader의 count=0도 전체 무참조
증명이 아니므로 실제 파일은 보류한다. 정기 자동 삭제·Source Runtime 활성화는 하지 않는다.

합성 테스트는 fixture 생성 시 31일 전 생성 사실을 선언한다. 실 파일의 mtime/ctime을 생성
시각으로 인정한 것이 아니다. 합성 승인자·DB 식별자는 운영 권한 검증을 대체하지 않는다.

## 합성 재현

저장소 루트, Python 3.13 및 저장소 의존성이 준비된 환경에서 실행한다.

```bash
PYTHONPATH=. uv run pytest ai_worker/tests/rag/source_cleanup -q
```

임시 파일의 생성·조회·실제 unlink·파일 journal 재읽기까지 포함한다. 테스트 종료 시 fixture
전체를 정리하는 것은 테스트 자원 정리이며 운영 감사 이력을 지워도 된다는 정책이 아니다.
운영 경로에 이 fixture API를 연결하지 않는다.

## PostgreSQL 조회 통합 재현

별도 일회용 PostgreSQL을 생성하고 DB 이름을 `source_cleanup347_test`로 지정한다.
예시 값은 이 합성 DB 전용이다. 사용자 서비스 DB에 아래 migration이나 테스트를 적용하지 않는다.

```bash
export DB_HOST=127.0.0.1 DB_PORT=55450 DB_EXPOSE_PORT=55450
export DB_USER=synthetic DB_PASSWORD=synthetic DB_NAME=source_cleanup347_test
export PYTHONPATH=backend:.
export SOURCE_CLEANUP_TEST_DATABASE_URL=postgresql+asyncpg://synthetic:synthetic@127.0.0.1:55450/source_cleanup347_test
uv run alembic -c backend/alembic.ini upgrade head
uv run pytest tests/integration/rag/test_source_cleanup_references.py -q
```

테스트는 명시된 Local 전용 DB 이름을 검사한다. 앱 기본 DB로 fallback하지 않는다.
별도 URL이 없으면 통합 테스트는 skip되므로 일반 CI 성공만으로 이 8건 실행을 주장하지 않는다.
한 테스트는 commit 가시성을 검사하기 위해 합성 행을 남긴다. 완료 후 전용 컨테이너·볼륨을
폐기한다. 기존 Source 행 삭제 trigger를 해제하는 cleanup을 수행하지 않는다.

## 실행 결과 해석

| 결과 | 의미 | 다음 조치 |
| --- | --- | --- |
| COMPLETED | 해당 합성 배치의 모든 대상에 DELETED 기록 또는 기존 성공+객체 부재 확인 | 합성 증빙으로만 사용 |
| REVIEW_REQUIRED | 일부 UNKNOWN/BLOCKED 또는 불완전 실행 | 대상별 journal 확인, 전체 성공으로 보고하지 않음 |
| EXECUTION_OR_AUDIT_UNAVAILABLE | 검사·guard·감사 실패. 일부 파일이 이미 삭제됐을 수도 있음 | 디스크의 INTENT/결과 기준으로 조사 |
| MISSING_REQUIRES_RECONCILIATION | 의도 이후 객체 부재지만 성공 기록 없음 | UNKNOWN 유지, 별도 근거 없이 성공 확정하지 않음 |
| RECREATED_AFTER_SUCCESS | 성공 기록 이후 같은 key에 새 객체 존재 | 과거 승인으로 삭제하지 않음 |
| RETRY_REQUIRES_REVIEW | 명시적 재시도 요청/한도 조건 미충족 | 새 승인·참조·세대 확인 후 제한된 수동 재시도 |

새로운 함수 호출은 disk journal을 재조회한다. 이전 성공 건은 반복 삭제하지 않는다.
실패한 파일이 남아 있어도 과거 승인만으로 재시도하지 않는다. 기록 손상 시 원본 journal을
수정/삭제해서 진행하지 않는다. 결과 불명확·감사 장애를 운영에서 해소하는 권한과 절차는 미확정이다.

## 운영 연결 전 인계표

| 항목 | 담당/검토 | 현재 상태 및 필요한 산출물 |
| --- | --- | --- |
| 구현 | 김지혜 | 합성 코드·직접 참조 조회·테스트·이 문서 |
| 물리 생성·Source 소유·세대 증거(Q1) | 김지혜 제안, 송은영·정현우 검토 | 실제 adapter 증거 포맷/보존 경계 미확정 |
| DB/namespace 귀속·전체 참조 목록(Q2) | 송은영·정현우 | 직접 Artifact 전체 조회 구현. downstream/외부 증빙·새 schema 인계 필요 |
| 모든 writer와 삭제 경합(Q3) | 송은영·정현우, 김지혜 연결 | 합성 cleanup 간 flock만 검증. Source 파일 쓰기/재사용/참조 commit 공유 경계 미연결 |
| 정책·PM 배치 승인(Q4) | 권가빈, 송은영 | 실제 신원·권한·만료·철회 verifier 및 승인 보관 위치 미연결 |
| 감사 불변성(Q5) | 송은영, 김지혜 | fsync 파일 append API만 구현. 비특권 수정/삭제 거부·durable storage/권한 미확정 |
| 재시도·UNKNOWN 해소(Q6) | Backend/운영·검토자 | 합성 상한은 운영 정책 아님. 횟수/간격·장기 실패 인계·결과 확정자 지정 필요 |
| 실제 실행자 | Backend·운영 담당 중 지정 | 미지정. 구현 담당이 자동으로 삭제 권한을 갖지 않음 |
| 종료 후 감사 관리자·위치·보존 종료 | 권가빈·송은영 및 실행 담당 | 관련 provenance 동안 보존. 실제 보관 위치/관리자/종료 판단 근거 인계 대기 |

현재 코드에 운영 삭제 진입점을 추가하기 전에 위 실제 연결·권한·증거를 갖추고 담당 리뷰를 받는다.
서비스 종료 자체는 참조 객체의 삭제 승인이나 유예 면제 사유가 아니다.
후속 테이블이 추가되면 직접/간접 참조 목록을 갱신하기 전 전체 무참조로 판단하지 않는다.

## 실행 절차 인계 기준

1. 범위·정책·실행자·감사 관리자와 DB/namespace를 고정하고 필요한 승인 증빙을 연결한다.
2. 읽기 전용 목록에서 Source 소유·생성 근거·종류를 확인한다. 불명확한 대상은 보류한다.
3. 직접/간접/외부 참조·수집 중 상태를 모두 확인하고 배치 목록 hash를 검토·승인한다.
4. 모든 writer와 공유되는 보호 경계에서 승인·전체 참조·실제 bytes/세대를 재검사한다.
5. 의도 영속화 성공 후 다시 검사하고 삭제·결과 기록을 수행한다. 감사 실패 시 완료 선언하지 않는다.
6. 성공·실패·UNKNOWN을 분리하고 실패 대상만 재검토한다. 성공 전 DB 참조/metadata를 지우지 않는다.
7. 결과·미완료 대상·감사 보관 위치·관리자를 인계하고 #335/#165/#323과 연결한다.

이 절차는 인계 초안이다. 현재 합성 adapter가 1~7의 운영 조건을 충족한다는 선언이 아니다.
PR은 `Related #347`로 연결하며 미완료 조건이 남아 있는 동안 `Closes #347`을 사용하지 않는다.
