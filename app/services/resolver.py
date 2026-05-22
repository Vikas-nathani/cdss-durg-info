import json
import structlog
from dataclasses import dataclass
from typing import Optional, List
from app.exceptions import DrugNotFoundException, NoFormulationException, NoLabelDataException

logger = structlog.get_logger(__name__)


@dataclass
class ResolvedDrug:
    drug_id_1mg: str
    brand_name: Optional[str]
    salt_composition: Optional[str]
    rxcui: Optional[List[str]]
    formulation_id: Optional[str]
    master_linkage_id: Optional[str]
    generic_name: Optional[str]
    combined_clean_jsonb: Optional[dict]


async def resolve_drug(drug_id_1mg: str, pool) -> ResolvedDrug:
    async with pool.acquire() as conn:
        # Step 1
        row1 = await conn.fetchrow(
            """
            SELECT ib.rxcui, ib.salt_composition, ib.brand_name
            FROM drugdb.indian_brand ib
            WHERE ib.drug_id_1mg = $1
              AND ib.match_combination NOT IN ('drugbank', 'us_unapproved')
            LIMIT 1
            """,
            drug_id_1mg,
        )
        if not row1:
            logger.warning("drug_not_found", drug_id_1mg=drug_id_1mg)
            raise DrugNotFoundException(drug_id_1mg)

        raw_rxcui = row1["rxcui"]
        # Handle both list and single string from asyncpg
        if raw_rxcui is None:
            rxcui = []
        elif isinstance(raw_rxcui, list):
            rxcui = raw_rxcui
        elif isinstance(raw_rxcui, str):
            rxcui = [raw_rxcui]
        else:
            rxcui = list(raw_rxcui)

        brand_name = row1["brand_name"]
        salt_composition = row1["salt_composition"]

        logger.info("resolver_step1_ok", drug_id_1mg=drug_id_1mg, rxcui=rxcui)

        # Step 2 — only pick a formulation that has label data in drug_master_linkage_unique
        row2 = await conn.fetchrow(
            """
            SELECT d.formulation_id, d.master_linkage_id, d.generic_name,
                   m.combined_clean_jsonb, m.generic_name AS ml_generic_name
            FROM drugdb.drug d
            JOIN drugdb.drug_master_linkage_unique m USING (master_linkage_id)
            WHERE d.rxcui = ANY($1::text[])
            LIMIT 1
            """,
            rxcui,
        )
        if not row2:
            logger.warning("no_formulation", drug_id_1mg=drug_id_1mg, rxcui=rxcui)
            raise NoFormulationException(drug_id_1mg)

        formulation_id = row2["formulation_id"]
        master_linkage_id = row2["master_linkage_id"]
        generic_name = row2["generic_name"]

        logger.info(
            "resolver_step2_ok",
            formulation_id=formulation_id,
            master_linkage_id=master_linkage_id,
        )

        row3 = row2

        raw_jsonb = row3["combined_clean_jsonb"]
        if raw_jsonb is None:
            combined_clean_jsonb = {}
        elif isinstance(raw_jsonb, str):
            try:
                combined_clean_jsonb = json.loads(raw_jsonb)
            except (json.JSONDecodeError, ValueError):
                combined_clean_jsonb = {}
        elif isinstance(raw_jsonb, dict):
            combined_clean_jsonb = raw_jsonb
        else:
            combined_clean_jsonb = {}

        final_generic_name = row3["ml_generic_name"] or generic_name

        logger.info("resolver_step3_ok", master_linkage_id=master_linkage_id)

        return ResolvedDrug(
            drug_id_1mg=drug_id_1mg,
            brand_name=brand_name,
            salt_composition=salt_composition,
            rxcui=rxcui,
            formulation_id=formulation_id,
            master_linkage_id=master_linkage_id,
            generic_name=final_generic_name,
            combined_clean_jsonb=combined_clean_jsonb,
        )
