import structlog
from typing import Dict, Any
from collections import Counter

logger = structlog.get_logger(__name__)

_SEVERITY_RANK = {"major": 1, "moderate": 2, "minor": 3}


async def check_drug_interaction(formulation_id_1: str, formulation_id_2: str, pool) -> Dict[str, Any]:
    """
    Find interactions specifically between the ingredients of two drugs.
    Checks both directions (A→B and B→A) and deduplicates.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT
                    i_a.name  AS drug1_ingredient,
                    i_b.name  AS drug2_ingredient,
                    ii.severity,
                    ii.mechanism
                FROM drugdb.drug_ingredient_mapping dim_a
                JOIN drugdb.ingredients i_a        ON i_a.id = dim_a.ingredient_id
                JOIN drugdb.ingredient_interactions ii ON ii.id = dim_a.ingredient_id
                JOIN drugdb.ingredients i_b        ON i_b.id = ii.reacting_id
                JOIN drugdb.drug_ingredient_mapping dim_b ON dim_b.ingredient_id = ii.reacting_id
                WHERE dim_a.formulation_id = $1
                  AND dim_b.formulation_id = $2

                UNION

                SELECT DISTINCT
                    i_a.name  AS drug1_ingredient,
                    i_b.name  AS drug2_ingredient,
                    ii.severity,
                    ii.mechanism
                FROM drugdb.drug_ingredient_mapping dim_b
                JOIN drugdb.ingredients i_b        ON i_b.id = dim_b.ingredient_id
                JOIN drugdb.ingredient_interactions ii ON ii.id = dim_b.ingredient_id
                JOIN drugdb.ingredients i_a        ON i_a.id = ii.reacting_id
                JOIN drugdb.drug_ingredient_mapping dim_a ON dim_a.ingredient_id = ii.reacting_id
                WHERE dim_b.formulation_id = $2
                  AND dim_a.formulation_id = $1
            ) t
            ORDER BY
                CASE severity
                    WHEN 'major'    THEN 1
                    WHEN 'moderate' THEN 2
                    WHEN 'minor'    THEN 3
                    ELSE 4
                END,
                drug2_ingredient
            """,
            formulation_id_1,
            formulation_id_2,
        )

    interactions = [dict(r) for r in rows]
    severities = Counter(r["severity"] for r in interactions if r.get("severity"))
    severity_counts = {
        "major":    severities.get("major", 0),
        "moderate": severities.get("moderate", 0),
        "minor":    severities.get("minor", 0),
    }

    found = [s for s in ("major", "moderate", "minor") if severities.get(s)]
    highest_severity = found[0] if found else None

    logger.info(
        "drug_interaction_check",
        formulation_id_1=formulation_id_1,
        formulation_id_2=formulation_id_2,
        count=len(interactions),
        highest=highest_severity,
    )
    return {
        "interactions": interactions,
        "severity_counts": severity_counts,
        "has_interaction": bool(interactions),
        "highest_severity": highest_severity,
    }


async def get_interactions(formulation_id: str, pool) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT our_ingredient, interacting_ingredient, severity, mechanism
            FROM (
                SELECT DISTINCT
                    i_ours.name   AS our_ingredient,
                    i_theirs.name AS interacting_ingredient,
                    ii.severity,
                    ii.mechanism
                FROM drugdb.drug_ingredient_mapping di_ours
                JOIN drugdb.ingredients i_ours
                    ON i_ours.id = di_ours.ingredient_id
                JOIN drugdb.ingredient_interactions ii
                    ON ii.id = di_ours.ingredient_id
                JOIN drugdb.ingredients i_theirs
                    ON i_theirs.id = ii.reacting_id
                WHERE di_ours.formulation_id = $1
            ) t
            ORDER BY
                CASE severity
                    WHEN 'major' THEN 1
                    WHEN 'moderate' THEN 2
                    WHEN 'minor' THEN 3
                    ELSE 4
                END,
                interacting_ingredient
            LIMIT 100
            """,
            formulation_id,
        )

    interactions = [dict(r) for r in rows]
    severities = Counter(r["severity"] for r in interactions if r.get("severity"))
    severity_counts = {
        "major": severities.get("major", 0),
        "moderate": severities.get("moderate", 0),
        "minor": severities.get("minor", 0),
    }

    logger.info("interactions_fetched", formulation_id=formulation_id, count=len(interactions))
    return {"interactions": interactions, "severity_counts": severity_counts}
