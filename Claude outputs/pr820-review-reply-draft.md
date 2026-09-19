## #820 리뷰 반영 — 세 건 모두 처리했습니다

리뷰 감사합니다. 지적 세 가지 다 반영했고, 커밋은 두 개로 나눴습니다.

### 1. test-rag fixture 누락 (MUST FIX)

`_add_checkin_lock_fixture_columns` 패턴대로 `_add_track_c_followup_fixture_columns`를 추가하고, `RUNTIME_TRACK_C_APPEND_TABLES`와 `RUNTIME_TRACK_C_FOLLOWUP_TABLES`를 합성 fixture 테이블 생성 집합에도 넣었습니다.

GRANT 대상 컬럼이 나중에 바뀌었을 때 또 같은 실수가 나지 않도록, 헬퍼에서 상수와 fixture 컬럼 목록이 어긋나면 SQL 에러 대신 메시지가 뜨게 했습니다.

```python
_TRACK_C_FOLLOWUP_FIXTURE_COLUMN_TYPES = {
    "response": "text",
    "revision": "integer",
    "updated_at": "timestamptz",
}

async def _add_track_c_followup_fixture_columns(connection):
    assert set(RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS) == set(_TRACK_C_FOLLOWUP_FIXTURE_COLUMN_TYPES), (
        "GRANT 대상 컬럼이 바뀌면 fixture 컬럼도 함께 갱신해야 한다"
    )
```

### 2. 회귀 테스트가 버그를 못 잡는 문제

말씀하신 대로 `test_database_role_provisioning.py`로 옮겼습니다. 소스 문자열 검사는 전부 걷어냈습니다.

- `_assert_runtime_table_privileges`에 Track C 5개 테이블을 추가해 `has_table_privilege`로 확인합니다. INSERT 부여 루프를 비우면 여기서 바로 깨집니다.
- `_exercise_track_c_runtime_writes`가 Runtime 자격증명으로 실제 INSERT와 follow-up 세 컬럼 UPDATE를 냅니다.
- `_assert_track_c_runtime_column_privileges`가 `has_column_privilege`로 세 컬럼만 열려 있는지 확인합니다.
- 거부 목록에 Track C 5개 테이블의 DELETE / TRUNCATE / 전체 컬럼 UPDATE, producer INSERT를 추가했습니다.

지적하신 대로 `GRANT DELETE ON TABLE public.{table}` 형태의 부정 단언은 코드가 `quoted_identifier`를 쓰는 한 절대 걸리지 않아서 전부 제거했습니다. 계약 테스트는 상수 집합 고정 용도로만 남기고, 실제 검증 위치를 docstring에 적어 뒀습니다.

`#820`으로 INSERT가 허용되므로 `_exercise_checkin_correction_runtime_permissions`의 `INSERT INTO {table} DEFAULT VALUES` 거부 단언은 제거했습니다. 원래 GRANT 실패로 테스트가 앞에서 끊겨 드러나지 않던 부분입니다.

### 3. audit 테이블 SELECT

확인하고 회수했습니다. `RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES`를 두고 audit에는 INSERT만 부여합니다.

- `ActionPlanFollowupAudit`에는 server default 컬럼이 없고 PK는 `uuid4` 애플리케이션 기본값이라 flush가 RETURNING을 내지 않습니다.
- 런타임에서 audit을 읽는 코드는 없습니다. `account_deletion_request_repository.py`의 DELETE는 #748 cleanup 역할 소관이고 SELECT는 그쪽에 남아 있습니다.
- 말씀하신 대로 "SELECT를 뺀 상태로 follow-up 정정 경로가 도는지"를 테스트로 박았습니다. `_exercise_track_c_followup_correction`이 Runtime 자격증명으로 `get_plan_followup_for_update` → `save_plan_followup`을 최초 응답·정정 두 번 태웁니다. 정정 쪽이 audit INSERT와 세 컬럼 UPDATE를 함께 내므로, SELECT 회수가 잘못됐다면 여기서 42501로 걸립니다.
- 거부 목록에도 `SELECT * FROM action_plan_followup_audit`을 넣었습니다.

### 검증

로컬에서 확인한 것:

```
pytest tests/contract/test_database_role_deployment.py tests/contract/test_public_track_c_gate.py  # 20 passed
ruff check / ruff format --check  # PASS (변경 3파일)
scripts/ci/check_python_test_inventory.py, check_database_logic.py, check_protected_table_writes.py  # PASS
```

`ISSUE398_TEST_POSTGRES_CONTAINER` lane은 지금 환경에 docker가 없어 로컬 재현을 못 했습니다. test-rag CI 결과로 확인 부탁드립니다. 만약 audit SELECT 회수 쪽에서 문제가 생기면 3번만 되돌리는 것이 가장 작은 롤백입니다.
