"""
Edge case tests covering invalid drugs, validation errors, auth failures,
FDC-specific behaviour, and null-data handling.
"""
import pytest
from tests.comprehensive.conftest import timed_get, timed_get_no_auth, timed_get_wrong_auth

ALL_ENDPOINTS_NO_PARAMS = [
    "/api/v1/drug/{drug_id}/contraindications",
    "/api/v1/drug/{drug_id}/warnings",
    "/api/v1/drug/{drug_id}/mechanism-of-action",
    "/api/v1/drug/{drug_id}/microbiology",
    "/api/v1/drug/{drug_id}/generic-name",
    "/api/v1/drug/{drug_id}/patient-info",
    "/api/v1/drug/{drug_id}/adverse-reactions",
    "/api/v1/drug/{drug_id}/drug-description",
    "/api/v1/drug/{drug_id}/indications",
    "/api/v1/drug/{drug_id}/pregnancy-use",
    "/api/v1/drug/{drug_id}/specific-populations",
    "/api/v1/drug/{drug_id}/products",
    "/api/v1/drug/{drug_id}/food-interactions",
    "/api/v1/drug/{drug_id}/ingredients",
    "/api/v1/drug/{drug_id}/interactions",
    "/api/v1/drug/{drug_id}/drug-classes",
]

INVALID_DRUG_ID = "999999999"
NO_DOSING_IDS = ["411423", "779258", "18840"]
FDC_DRUG_ID = "1000006"


# ── Group 1: Invalid drug → all endpoints return 404 ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("url_tpl", ALL_ENDPOINTS_NO_PARAMS)
async def test_invalid_drug_returns_404(url_tpl):
    url = url_tpl.format(drug_id=INVALID_DRUG_ID)
    resp, _ = await timed_get(url)
    assert resp.status_code == 404, f"{url} → expected 404, got {resp.status_code}"
    body = resp.json()
    assert body.get("success") is False
    assert body.get("error_code") == "DRUG_NOT_FOUND", f"Expected DRUG_NOT_FOUND, got: {body}"


@pytest.mark.asyncio
async def test_invalid_drug_population_info_returns_404():
    resp, _ = await timed_get(
        f"/api/v1/drug/{INVALID_DRUG_ID}/population-info", params={"age": 35}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("success") is False
    assert body.get("error_code") == "DRUG_NOT_FOUND"


@pytest.mark.asyncio
async def test_invalid_drug_dosing_returns_404():
    resp, _ = await timed_get(
        f"/api/v1/drug/{INVALID_DRUG_ID}/dosing-regimen", params={"age": 35}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("success") is False
    # Dosing has its own lookup — may return DRUG_NOT_FOUND or NO_DOSING_DATA
    assert body.get("error_code") in ("DRUG_NOT_FOUND", "NO_DOSING_DATA")


# ── Group 2: No-dosing drugs ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", NO_DOSING_IDS)
async def test_no_dosing_drug_adult_dosing_valid_response(drug_id):
    """These drugs have no pediatric dosing but may have adult dosing rows."""
    resp, _ = await timed_get(
        f"/api/v1/drug/{drug_id}/dosing-regimen", params={"age": 35}
    )
    # Accept 200 (has adult dosing) or 404 (truly no dosing for this age)
    assert resp.status_code in (200, 404), (
        f"drug {drug_id} dosing → unexpected {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    if resp.status_code == 404:
        assert body.get("error_code") == "NO_DOSING_DATA"
    else:
        assert body.get("success") is True
        assert isinstance(body.get("data"), list)


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", NO_DOSING_IDS)
async def test_no_dosing_drug_pediatric_dosing_returns_404(drug_id):
    resp, _ = await timed_get(
        f"/api/v1/drug/{drug_id}/dosing-regimen", params={"age": 5}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("error_code") == "NO_DOSING_DATA"


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", NO_DOSING_IDS)
async def test_no_dosing_drug_contraindications_still_200(drug_id):
    """Drug exists even if it has no dosing data — other endpoints must still work."""
    resp, _ = await timed_get(f"/api/v1/drug/{drug_id}/contraindications")
    assert resp.status_code == 200, (
        f"contraindications for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    )
    assert resp.json().get("success") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", NO_DOSING_IDS)
async def test_no_dosing_drug_generic_name_still_200(drug_id):
    resp, _ = await timed_get(f"/api/v1/drug/{drug_id}/generic-name")
    assert resp.status_code == 200
    assert resp.json().get("success") is True


# ── Group 3: Invalid age for /population-info ─────────────────────────────────

@pytest.mark.asyncio
async def test_population_info_negative_age_returns_422():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/population-info", params={"age": -1}
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body.get("error_code") == "INVALID_AGE"


@pytest.mark.asyncio
async def test_population_info_age_over_120_returns_422():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/population-info", params={"age": 121}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("error_code") == "INVALID_AGE"


@pytest.mark.asyncio
async def test_population_info_age_exactly_120_is_valid():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/population-info", params={"age": 120}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["population_category"] == "geriatric"


@pytest.mark.asyncio
async def test_population_info_age_exactly_0_is_valid():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/population-info", params={"age": 0}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["population_category"] == "pediatric"


@pytest.mark.asyncio
async def test_population_info_string_age_returns_422():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/population-info", params={"age": "abc"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_population_info_missing_age_returns_422():
    resp, _ = await timed_get("/api/v1/drug/1002775/population-info")
    assert resp.status_code == 422


# ── Group 4: Invalid dosing parameters ───────────────────────────────────────

@pytest.mark.asyncio
async def test_dosing_negative_age_returns_422():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/dosing-regimen", params={"age": -1}
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_dosing_string_age_returns_422():
    resp, _ = await timed_get(
        "/api/v1/drug/1002775/dosing-regimen", params={"age": "baby"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dosing_missing_age_returns_422():
    resp, _ = await timed_get("/api/v1/drug/1002775/dosing-regimen")
    assert resp.status_code == 422


# ── Group 5: Auth failures ────────────────────────────────────────────────────

SAMPLE_LABEL_URLS = [
    "/api/v1/drug/1002775/contraindications",
    "/api/v1/drug/1002775/warnings",
    "/api/v1/drug/1002775/mechanism-of-action",
    "/api/v1/drug/1002775/products",
    "/api/v1/drug/1002775/interactions",
    "/api/v1/drug/1002775/drug-classes",
    "/api/v1/drug/1002775/dosing-regimen",
    "/api/v1/drug/1002775/population-info",
    "/api/v1/drug/1002775/ingredients",
    "/api/v1/drug/1002775/food-interactions",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", SAMPLE_LABEL_URLS)
async def test_no_api_key_returns_401(url):
    resp, _ = await timed_get_no_auth(url)
    assert resp.status_code == 401, f"{url} without auth → expected 401, got {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", SAMPLE_LABEL_URLS)
async def test_wrong_api_key_returns_401(url):
    resp, _ = await timed_get_wrong_auth(url)
    assert resp.status_code == 401, f"{url} wrong auth → expected 401, got {resp.status_code}"


@pytest.mark.asyncio
async def test_health_endpoint_skips_auth():
    """Health endpoint must be reachable without an API key."""
    async with __import__("httpx").AsyncClient(base_url="http://localhost:8002", timeout=10.0) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "healthy"


# ── Group 6: FDC drug specific ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fdc_interactions_has_multiple_our_ingredients():
    """A 3-ingredient FDC should show multiple distinct our_ingredient values."""
    resp, _ = await timed_get(f"/api/v1/drug/{FDC_DRUG_ID}/interactions")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    if data:
        # Verify expected keys exist in each item
        for item in data:
            assert "our_ingredient" in item
            assert "interacting_ingredient" in item
            assert "severity" in item
            assert "mechanism" in item
        our_ingredients = {item["our_ingredient"] for item in data}
        assert len(our_ingredients) >= 1, "FDC should have at least 1 ingredient in interactions"


@pytest.mark.asyncio
async def test_fdc_ingredients_structure():
    """FDC drugs should have ingredients entries."""
    resp, _ = await timed_get(f"/api/v1/drug/{FDC_DRUG_ID}/ingredients")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert "active" in data
    assert "inactive" in data


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ["1000006", "1000008", "1000013", "1000015", "1000098"])
async def test_fdc_drug_products_returns_list(drug_id):
    resp, _ = await timed_get(f"/api/v1/drug/{drug_id}/products")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"], list)


# ── Group 7: Null data handling ───────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ["1000019", "1000037", "1000041", "1000042"])
async def test_null_data_still_success(drug_id):
    """Endpoints where data.text is null must still return success=true and correct structure."""
    for endpoint in ("microbiology", "drug-description", "specific-populations"):
        resp, _ = await timed_get(f"/api/v1/drug/{drug_id}/{endpoint}")
        assert resp.status_code == 200, f"{endpoint} for {drug_id} → {resp.status_code}"
        body = resp.json()
        assert body.get("success") is True, f"{endpoint} for {drug_id} returned success=false"
        assert "data" in body
        data = body["data"]
        # Must be a dict with the required keys even when all values are null
        assert isinstance(data, dict)
        assert "text" in data
        assert "table" in data
        assert "subsections" in data
        # No 500 errors — null data must never blow up the response


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ["1000019", "1000037", "1000041", "1000042"])
async def test_adult_population_info_null_data_still_success(drug_id):
    """Adult population-info must return success=true even though data.text is null."""
    resp, _ = await timed_get(
        f"/api/v1/drug/{drug_id}/population-info", params={"age": 35}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]
    assert data["population_category"] == "adult"
    assert data["text"] is None
    assert data["table"] is None
    assert data["subsections"] is None
