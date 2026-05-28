-- Resolver coverage report: how many indian_brand drugs produce a result
-- through the full resolver flow (indian_brand → drug table or UNII bridge),
-- broken down by match_combination source.
--
-- PRIMARY HIT  : match_combination NOT IN ('drugbank','us_unapproved')
--                AND rxcui directly joins drugdb.drug + drug_master_linkage_unique
-- FALLBACK HIT : primary path fails, but either
--                  (a) excluded source whose rxcui joins drug + drug_master_linkage_unique, OR
--                  (b) UNII bridge: rxcui → ingredients.unii → DrugMasterLinkage → drug + drug_master_linkage_unique
--
-- NOTE: drug_master_linkage_unique join is required in both paths because the
--       resolver SELECT includes that table in both step-2 queries.
--
-- Run:
--   psql $DATABASE_URL -f queries/resolver_coverage_by_match_combination.sql

WITH

-- Unnest rxcui arrays so scalar joins can use btree/gin indexes
ib_rxcui AS (
    SELECT drug_id_1mg, match_combination, unnest(rxcui) AS rxcui_val
    FROM   drugdb.indian_brand
    WHERE  rxcui IS NOT NULL
),

-- Step 2 direct: rxcui → drug → drug_master_linkage_unique (mirrors resolver step-2 primary)
direct_match AS (
    SELECT DISTINCT ir.drug_id_1mg
    FROM   ib_rxcui ir
    JOIN   drugdb.drug d                      ON d.rxcui             = ir.rxcui_val
    JOIN   drugdb.drug_master_linkage_unique m ON m.master_linkage_id = d.master_linkage_id
),

-- Step 2 UNII bridge: rxcui → ingredients → DrugMasterLinkage → drug → drug_master_linkage_unique
-- (mirrors resolver step-2 fallback)
unii_match AS (
    SELECT DISTINCT ir.drug_id_1mg
    FROM   ib_rxcui ir
    JOIN   drugdb.ingredients        ing ON ing.rxcui = ir.rxcui_val
                                        AND ing.unii  IS NOT NULL
    JOIN   public."DrugMasterLinkage" dml ON dml.unii_ids @> ARRAY[ing.unii::text]
    JOIN   drugdb.drug                d   ON d.master_linkage_id  = dml.master_linkage_id
    JOIN   drugdb.drug_master_linkage_unique m ON m.master_linkage_id = d.master_linkage_id
),

classified AS (
    SELECT
        ib.match_combination,

        -- primary: quality source + direct rxcui hit (with linkage)
        CASE WHEN ib.match_combination NOT IN ('drugbank', 'us_unapproved')
              AND dm.drug_id_1mg IS NOT NULL
             THEN 1 ELSE 0 END AS is_primary,

        -- fallback: primary failed, but direct hit or UNII bridge works
        CASE WHEN NOT (    ib.match_combination NOT IN ('drugbank', 'us_unapproved')
                       AND dm.drug_id_1mg IS NOT NULL)
              AND (dm.drug_id_1mg IS NOT NULL OR um.drug_id_1mg IS NOT NULL)
             THEN 1 ELSE 0 END AS is_fallback

    FROM  drugdb.indian_brand ib
    LEFT JOIN direct_match dm ON dm.drug_id_1mg = ib.drug_id_1mg
    LEFT JOIN unii_match   um ON um.drug_id_1mg = ib.drug_id_1mg
)

SELECT
    match_combination,
    TO_CHAR(COUNT(*),                                                                                     'FM999,999') AS total_drugs,
    TO_CHAR(SUM(is_primary),                                                                              'FM999,999') AS primary_hits,
    TO_CHAR(SUM(CASE WHEN is_primary = 0 THEN is_fallback ELSE 0 END),                                   'FM999,999') AS fallback_hits,
    TO_CHAR(SUM(CASE WHEN is_primary = 1 OR  is_fallback = 1 THEN 1 ELSE 0 END),                         'FM999,999') AS either_path,
    TO_CHAR(SUM(CASE WHEN is_primary = 0 AND is_fallback = 0 THEN 1 ELSE 0 END),                         'FM999,999') AS no_results,
    ROUND(100.0 * SUM(CASE WHEN is_primary = 1 OR is_fallback = 1 THEN 1 ELSE 0 END) / COUNT(*), 1)
        || '%'                                                                                            AS "coverage %"
FROM  classified
GROUP BY match_combination
ORDER BY COUNT(*) DESC;
