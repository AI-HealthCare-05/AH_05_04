"""add candidate search displayed count deferred constraint

Revision ID: 171c0f751206
Revises: 171798152fa3
Create Date: 2026-09-04

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "171c0f751206"
down_revision: str | Sequence[str] | None = "171798152fa3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 계약 문서(medication-identification-v1.md:117): "Commit 시 Deferred Constraint는 실제
# 표시 Result 수, displayed_candidate_count, 상태별 허용 Result 수와
# selection_eligible => is_displayed를 검증한다. 불일치하면 전체를 rollback한다."
#
# 일반 CHECK 제약은 다른 테이블을 참조할 수 없어(medication_candidate_search 자기 자신의
# displayed_candidate_count 숫자만 봄) medication_candidate_search_result의 실제
# is_displayed 개수와 이 숫자가 정말 일치하는지는 검증하지 못한다. Finalizer가
# add_results()와 finalize_search()를 한 Transaction에서 실행하는 동안 중간 상태는
# 일치하지 않을 수 있으므로, 즉시 실행되는 일반 트리거가 아니라 DEFERRABLE INITIALLY
# DEFERRED CONSTRAINT TRIGGER로 커밋 직전에만 검증한다.
_FUNCTION_NAME = "check_medication_candidate_search_displayed_count"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION_NAME}()
        RETURNS trigger AS $$
        DECLARE
            v_search_id CHAR(36);
            v_expected_count INTEGER;
            v_actual_count INTEGER;
        BEGIN
            IF TG_TABLE_NAME = 'medication_candidate_search_result' THEN
                IF TG_OP = 'DELETE' THEN
                    v_search_id := OLD.search_id;
                ELSE
                    v_search_id := NEW.search_id;
                END IF;
            ELSE
                v_search_id := COALESCE(NEW.id, OLD.id);
            END IF;

            SELECT displayed_candidate_count INTO v_expected_count
            FROM medication_candidate_search
            WHERE id = v_search_id;

            -- Search가 이미 삭제됐거나(현재 구현엔 없지만 향후 대비) 찾을 수 없으면
            -- 이 트리거가 검증할 대상이 아니다.
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            SELECT count(*) INTO v_actual_count
            FROM medication_candidate_search_result
            WHERE search_id = v_search_id AND is_displayed = true;

            IF v_actual_count <> v_expected_count THEN
                RAISE EXCEPTION
                    'medication_candidate_search.displayed_candidate_count (%) does not match '
                    'actual displayed medication_candidate_search_result count (%) for search_id %',
                    v_expected_count, v_actual_count, v_search_id;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_candidate_search_result_displayed_count
        AFTER INSERT OR UPDATE OR DELETE ON medication_candidate_search_result
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION check_medication_candidate_search_displayed_count();
        """
    )

    # Result 변경 없이 Search.displayed_candidate_count만 바뀌는 경로(정상 흐름에는 없지만
    # 직접 SQL로 값을 고치는 실수를 막기 위한 방어선)도 같은 함수로 재검증한다.
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_candidate_search_displayed_count
        AFTER UPDATE OF displayed_candidate_count ON medication_candidate_search
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION check_medication_candidate_search_displayed_count();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_candidate_search_displayed_count ON medication_candidate_search")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_candidate_search_result_displayed_count ON medication_candidate_search_result"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()")
