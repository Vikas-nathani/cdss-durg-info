import pytest
from tests.conftest import API_KEY

ENDPOINTS = [
    "contraindications",
    "warnings",
    "mechanism-of-action",
    "microbiology",
    "generic-name",
    "patient-info",
    "adverse-reactions",
    "drug-description",
    "indications",
    "geriatric-use",
    "pediatric-use",
    "pregnancy-use",
    "specific-populations",
    "products",
    "food-interactions",
    "ingredients",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_label_valid_drug(client, valid_drug_id, endpoint):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/{endpoint}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["drug_id_1mg"] == valid_drug_id
    assert "data" in body
    assert "meta" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_label_invalid_drug(client, invalid_drug_id, endpoint):
    resp = await client.get(
        f"/api/v1/drug/{invalid_drug_id}/{endpoint}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error_code"] == "DRUG_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_label_no_api_key(client, valid_drug_id, endpoint):
    resp = await client.get(f"/api/v1/drug/{valid_drug_id}/{endpoint}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_label_cache_hit(client, valid_drug_id, redis_client):
    if redis_client is None:
        pytest.skip("Redis not available in this environment")
    endpoint = "contraindications"
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{valid_drug_id}/{endpoint}"
    await client.get(url, headers=headers)
    resp2 = await client.get(url, headers=headers)
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["meta"]["cached"] is True


@pytest.mark.asyncio
async def test_ingredients_valid_drug(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/ingredients",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["drug_id_1mg"] == valid_drug_id
    data = body["data"]
    assert "active" in data
    assert "inactive" in data
    assert isinstance(data["active"], list)
    assert isinstance(data["inactive"], list)


@pytest.mark.asyncio
async def test_ingredients_invalid_drug(client, invalid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{invalid_drug_id}/ingredients",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error_code"] == "DRUG_NOT_FOUND"


@pytest.mark.asyncio
async def test_products_flat_structure(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/products",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, list)
    assert "product_count" in body["meta"]
    if data:
        product = data[0]
        assert "ndc_code" not in product
        assert "brand_name" not in product
        assert "generic_name" in product
        assert "dosage_form" in product
        assert "route_of_administration" in product
        assert "color" in product
        assert "shape" in product
        assert "imprint" in product
        assert "size_mm" in product


@pytest.mark.asyncio
async def test_label_rich_structure(client, valid_drug_id):
    resp = await client.get(
        f"/api/v1/drug/{valid_drug_id}/contraindications",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert "text" in data
    assert "table" in data
    assert "subsections" in data
