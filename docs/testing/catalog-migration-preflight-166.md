# #166 기존 Catalog 이행 전 데이터 점검

- 구현 담당: 김지혜. DB·이행 검토: 송은영. Catalog 의미 검토: 정현우.
- 상태: 기존 스키마의 읽기 전용 집계 구현·합성 PostgreSQL 검증 완료.
- 이 도구는 migration 적용 허가나 D-02 실행 참조 인계를 대신하지 않는다.

## 실행과 출력

[점검 SQL](../../scripts/rag/catalog_migration_preflight.sql)은 `public`의 기존 Catalog 네 테이블을
한 SELECT로 조회한다. 같은 MVCC snapshot에서 모든 건수를 구하며 원문·코드·UUID를 반환하지 않는다.
운영 환경 데이터는 이번 검증에서 조회하지 않았다. 이행 대상의 격리 복제본에 대해 재실행해야 한다.

전체 행을 조회할 수 있는 읽기 전용 접속을 준비한 뒤 저장소 루트에서 실행한다. 접속 정보는
기존 libpq 환경 또는 service 설정을 사용하며 비밀번호를 명령·증빙 파일에 넣지 않는다.

```bash
PGOPTIONS='-c default_transaction_read_only=on -c row_security=off -c statement_timeout=30000' \
  psql -X -v ON_ERROR_STOP=1 -f scripts/rag/catalog_migration_preflight.sql
```

`row_security=off`는 접근 권한을 부여하거나 RLS를 우회하는 옵션이 아니다. RLS 때문에 일부 행만
보게 되는 접속에서는 오류로 중단해 불완전한 0건을 전체 결과로 오인하지 않도록 한다.
테이블·권한 부족, schema 변경, timeout 등 오류를 0건이나 성공으로 대체하지 않는다.
점검 기준 commit, 대상 복제본 식별, 실행 시각을 집계 결과와 함께 남긴다. SQL에는 접속 주소를 저장하지 않는다.

## 집계 해석

| 키 | 의미와 후속 처리 |
| --- | --- |
| product_rows / ingredient_rows / alias_rows / component_rows | 실제 보존 행수. 네 테이블이 비어 있다고 가정하지 않는다. |
| ingredient_missing_identity_rows | 성분 코드 체계 또는 코드 누락. 이름으로 Identity를 임의 생성하지 않는다. |
| ingredient_same_snapshot_identity_groups | 같은 Snapshot 내 동일 코드 체계·코드의 반복 그룹. 기존 unique가 유지되면 0이며 제약 정합성 조사에 사용한다. |
| product_identity_across_snapshots_groups | 복수 Snapshot에 등장한 동일 제품 Identity 그룹. 정상 재수집일 수 있으므로 중복 오류로 취급하지 않는다. |
| alias_approved_boolean_rows | 기존 is_approved=true 행수. 새 승인 receipt·현재성·유효성의 증거가 아니다. |
| alias_missing_target_identity_rows | 대상의 Snapshot 결속 또는 공식 코드가 없는 Alias. 근거 없이 대상 Identity를 보충하지 않는다. |
| component_numeric_without_text_rows | 숫자 함량은 있으나 amount_text가 없는 행. 원래 문자열의 소수점·표기 복원을 보장할 수 없다. |
| component_multiple_role_pairs | 동일 제품·성분 쌍에 복수 role이 있는 경우. role을 자연키에서 제거하기 전에 검토한다. |

모든 값이 0이어도 이행 준비 완료를 뜻하지 않는다. 허용 code system·문자열 정규화·Alias 출처와
실제 승인·Publication/normalization 실행·Source 상태·Set 범위는 별도 검사·인계 대상이다.
단일 snapshot의 통계이므로 점검 뒤 생기는 변경까지 동결하거나 보장하지 않는다.

## 합성 PostgreSQL 검증

Python 3.13 / PostgreSQL 16의 별도 폐기 가능한 DB에서 기존 Alembic head를 적용해 확인했다.
새 Catalog 테이블이나 신규 revision을 만들지 않았다.

- 정상 데이터, 성분 Identity 누락과 성분 Alias, 숫자 함량만 존재, 복수 role, 교차 Snapshot
  Identity 재사용의 5개 시나리오를 실제 기존 테이블에 적재해 정확한 집계를 확인했다.
- 점검은 READ ONLY transaction에서 실행했다. 같은 결과를 다시 읽고 합성 데이터 정리 후
  기준 집계가 복원되는지 확인했다. 모든 출력 값은 비음수 정수다.
- 기존 Source·Catalog migration 테스트와 함께 **29 passed** (신규 5개 포함).
- 관련 Ruff·Mypy 및 diff 공백 검사 통과.

```bash
PYTHONPATH=backend:. uv run pytest tests/migration/test_rag_source_catalog_migration.py -q
```

이 검증은 기존 스키마의 조회·제약 회귀이며, 새 Catalog adapter의 저장·commit/rollback 통합
검증이 아니다. D-02는 미확정으로 유지한다. 인계 후 실제 이행·저장 구현을 이어간다.
