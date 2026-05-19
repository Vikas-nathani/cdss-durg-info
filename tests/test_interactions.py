import pytest
from tests.conftest import API_KEY


@pytest.mark.asyncio
async def test_interactions_valid(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/interactions",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert "severity_counts" in body["meta"]
    sc = body["meta"]["severity_counts"]
    assert "major" in sc and "moderate" in sc and "minor" in sc


@pytest.mark.asyncio
async def test_interactions_invalid_drug(client, invalid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{invalid_drug_id}/interactions",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_interactions_cache(client, valid_drug_id, redis_client):
    if redis_client is None:
        pytest.skip("Redis not available in this environment")
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{valid_drug_id}/interactions"
    await client.get(url, headers=headers)
    resp2 = await client.get(url, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["meta"]["cached"] is True
