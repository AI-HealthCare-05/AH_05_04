-- #166: 기존 Catalog 이행 전 조사. 한 SELECT의 동일 MVCC snapshot에서 건수만 반환한다.
-- public schema의 실제 테이블을 조회한다. 결과 0건은 D-02 인계·승인·migration 허가가 아니다.
-- 읽기 전용 계정/transaction에서 실행하며, 원문·공식 코드·UUID를 출력하지 않는다.
SELECT jsonb_build_object(
    'product_rows', (SELECT count(*) FROM public.rag_medication_product),
    'ingredient_rows', (SELECT count(*) FROM public.rag_medication_ingredient),
    'alias_rows', (SELECT count(*) FROM public.rag_medication_alias),
    'component_rows', (SELECT count(*) FROM public.rag_medication_product_component),
    'ingredient_missing_identity_rows', (
        SELECT count(*) FROM public.rag_medication_ingredient
        WHERE ingredient_code_system IS NULL OR ingredient_code IS NULL
            OR btrim(ingredient_code_system) = '' OR btrim(ingredient_code) = ''
    ),
    'ingredient_same_snapshot_identity_groups', (
        SELECT count(*) FROM (
            SELECT source_snapshot_id, ingredient_code_system, ingredient_code
            FROM public.rag_medication_ingredient
            WHERE ingredient_code_system IS NOT NULL AND ingredient_code IS NOT NULL
            GROUP BY source_snapshot_id, ingredient_code_system, ingredient_code
            HAVING count(*) > 1
        ) AS duplicate_groups
    ),
    'product_identity_across_snapshots_groups', (
        SELECT count(*) FROM (
            SELECT code_system, canonical_code FROM public.rag_medication_product
            GROUP BY code_system, canonical_code
            HAVING count(DISTINCT source_snapshot_id) > 1
        ) AS shared_identities
    ),
    'alias_approved_boolean_rows', (
        SELECT count(*) FROM public.rag_medication_alias WHERE is_approved IS TRUE
    ),
    'alias_missing_target_identity_rows', (
        SELECT count(*) FROM public.rag_medication_alias a
        LEFT JOIN public.rag_medication_product p
            ON p.id = a.product_id AND p.source_snapshot_id = a.source_snapshot_id
        LEFT JOIN public.rag_medication_ingredient i
            ON i.id = a.ingredient_id AND i.source_snapshot_id = a.source_snapshot_id
        WHERE (a.target_type = 'PRODUCT' AND (
            p.id IS NULL OR p.code_system IS NULL OR p.canonical_code IS NULL
            OR btrim(p.code_system) = '' OR btrim(p.canonical_code) = ''
        )) OR (a.target_type = 'INGREDIENT' AND (
            i.id IS NULL OR i.ingredient_code_system IS NULL OR i.ingredient_code IS NULL
            OR btrim(i.ingredient_code_system) = '' OR btrim(i.ingredient_code) = ''
        ))
    ),
    'component_numeric_without_text_rows', (
        SELECT count(*) FROM public.rag_medication_product_component
        WHERE amount_value IS NOT NULL AND (amount_text IS NULL OR btrim(amount_text) = '')
    ),
    'component_multiple_role_pairs', (
        SELECT count(*) FROM (
            SELECT product_id, ingredient_id FROM public.rag_medication_product_component
            GROUP BY product_id, ingredient_id HAVING count(DISTINCT component_role) > 1
        ) AS multiple_roles
    )
) AS catalog_migration_inventory;
