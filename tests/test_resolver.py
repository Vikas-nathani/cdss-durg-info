import pytest
import asyncpg
from app.services.resolver import resolve_drug
from app.exceptions import DrugNotFoundException, NoFormulationException


@pytest.mark.asyncio
async def test_resolve_valid_drug(db_pool, valid_drug_id):
    result = await resolve_drug(valid_drug_id, db_pool)
    assert result.drug_id_1mg == valid_drug_id
    assert result.formulation_id is not None
    assert result.master_linkage_id is not None


@pytest.mark.asyncio
async def test_resolve_invalid_drug(db_pool, invalid_drug_id):
    with pytest.raises(DrugNotFoundException):
        await resolve_drug(invalid_drug_id, db_pool)


@pytest.mark.asyncio
async def test_resolve_returns_combined_jsonb(db_pool, valid_drug_id):
    result = await resolve_drug(valid_drug_id, db_pool)
    assert result.combined_clean_jsonb is not None
    assert isinstance(result.combined_clean_jsonb, dict)
