import structlog
from typing import List, Dict, Any
from collections import Counter

logger = structlog.get_logger(__name__)


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
