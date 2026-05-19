import pytest
from tests.conftest import API_KEY


@pytest.mark.asyncio
async def test_dosing_valid_adult(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/dosing-regimen?age=35",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)


@pytest.mark.asyncio
async def test_dosing_invalid_age(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/dosing-regimen?age=-1",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dosing_missing_age(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/dosing-regimen",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dosing_invalid_drug(client, invalid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{invalid_drug_id}/dosing-regimen?age=35",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404
