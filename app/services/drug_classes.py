import structlog
from typing import Dict, Any, List

logger = structlog.get_logger(__name__)


async def get_drug_classes(formulation_id: str, pool) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                pharmacologic_class,
                therapeutic_class,
                mechanism_class
            FROM drugdb.drug
            WHERE formulation_id = $1
            """,
            formulation_id,
        )

    if not row:
        logger.warning("no_drug_classes", formulation_id=formulation_id)
        return {
            "pharmacologic_class": [],
            "therapeutic_class": [],
            "mechanism_class": [],
        }

    def to_list(val) -> List:
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return [val]

    result = {
        "pharmacologic_class": to_list(row["pharmacologic_class"]),
        "therapeutic_class": to_list(row["therapeutic_class"]),
        "mechanism_class": to_list(row["mechanism_class"]),
    }
    logger.info("drug_classes_fetched", formulation_id=formulation_id)
    return result
