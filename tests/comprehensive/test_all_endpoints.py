"""
Comprehensive endpoint coverage tests.
Tests all 18 endpoints with 10 drugs (5 single-ingredient + 5 FDC).
"""
import pytest
from tests.comprehensive.conftest import timed_get

# ── Endpoint definitions ─────────────────────────────────────────────────────

LABEL_ENDPOINTS = [
    "contraindications",
    "warnings",
    "mechanism-of-action",
    "microbiology",
    "generic-name",
    "patient-info",
    "adverse-reactions",
    "drug-description",
    "indications",
    "specific-populations",
    "products",
    "food-interactions",
    "ingredients",
    "pregnancy-use",
]

ALL_DRUGS = [
    "1000019", "1000037", "1000041", "1000042", "1002775",
    "1000006", "1000008", "1000013", "1000015", "1000098",
]

PERFORMANCE_LIMIT_MS = 3000


# ── Shared assertions ─────────────────────────────────────────────────────────

def assert_base_envelope(body: dict, drug_id: str):
    assert body.get("success") is True, f"Expected success=true, got: {body}"
    assert body.get("drug_id_1mg") == drug_id
    assert "generic_name" in body
    assert "data" in body
    assert "meta" in body
    meta = body["meta"]
    assert "cached" in meta
    assert "response_time_ms" in meta


def assert_rich_label_structure(data):
    """Standard label endpoints return {text, table, subsections}."""
    assert isinstance(data, dict), f"data must be dict, got {type(data)}"
    assert "text" in data, f"data missing 'text': {data}"
    assert "table" in data, f"data missing 'table': {data}"
    assert "subsections" in data, f"data missing 'subsections': {data}"
    # Values may be null but keys must exist
    assert data["text"] is None or isinstance(data["text"], str)
    assert data["table"] is None or isinstance(data["table"], list)
    assert data["subsections"] is None or isinstance(data["subsections"], list)


# ── Label endpoint tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
@pytest.mark.parametrize("endpoint", [e for e in LABEL_ENDPOINTS if e not in ("products", "ingredients")])
async def test_label_endpoint_structure(drug_id, endpoint):
    url = f"/api/v1/drug/{drug_id}/{endpoint}"
    resp, elapsed_ms = await timed_get(url)
    assert resp.status_code == 200, f"{endpoint} for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < PERFORMANCE_LIMIT_MS, f"{endpoint} took {elapsed_ms:.0f}ms (limit {PERFORMANCE_LIMIT_MS}ms)"
    assert_rich_label_structure(body["data"])


# ── /products ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
async def test_products_structure(drug_id):
    resp, elapsed_ms = await timed_get(f"/api/v1/drug/{drug_id}/products")
    assert resp.status_code == 200, f"products for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < PERFORMANCE_LIMIT_MS

    data = body["data"]
    assert isinstance(data, list), f"products data must be list, got {type(data)}"

    meta = body["meta"]
    assert "product_count" in meta, "meta must have product_count"
    assert meta["product_count"] == len(data)

    for item in data:
        # Flat tabular format — no ndc_code or brand_name
        assert "ndc_code" not in item, f"product must not have ndc_code: {item}"
        assert "brand_name" not in item, f"product must not have brand_name: {item}"
        assert "generic_name" in item
        assert "dosage_form" in item
        assert "route_of_administration" in item
        assert "color" in item
        assert "shape" in item
        assert "imprint" in item
        assert "size_mm" in item


# ── /ingredients ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
async def test_ingredients_structure(drug_id):
    resp, elapsed_ms = await timed_get(f"/api/v1/drug/{drug_id}/ingredients")
    assert resp.status_code == 200, f"ingredients for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < PERFORMANCE_LIMIT_MS

    data = body["data"]
    assert isinstance(data, dict), f"ingredients data must be dict"
    assert "active" in data
    assert "inactive" in data
    assert isinstance(data["active"], list)
    assert isinstance(data["inactive"], list)

    for product_entry in data["active"]:
        assert "product" in product_entry
        assert "ingredients" in product_entry
        assert isinstance(product_entry["ingredients"], list)
        for ingredient in product_entry["ingredients"]:
            assert isinstance(ingredient, dict)
            assert "name" in ingredient
            assert "strength" in ingredient

    for product_entry in data["inactive"]:
        assert "product" in product_entry
        assert "ingredients" in product_entry
        assert isinstance(product_entry["ingredients"], list)


# ── /interactions ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
async def test_interactions_structure(drug_id):
    resp, elapsed_ms = await timed_get(f"/api/v1/drug/{drug_id}/interactions")
    assert resp.status_code == 200, f"interactions for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < 5000, f"interactions took {elapsed_ms:.0f}ms (limit 5000ms)"

    data = body["data"]
    assert isinstance(data, list), f"interactions data must be list"

    meta = body["meta"]
    assert "severity_counts" in meta, "meta must have severity_counts"
    sc = meta["severity_counts"]
    assert "major" in sc
    assert "moderate" in sc
    assert "minor" in sc

    for item in data:
        assert "our_ingredient" in item
        assert "interacting_ingredient" in item
        assert "severity" in item
        assert "mechanism" in item
        assert item["severity"] in ("major", "moderate", "minor")


# ── /drug-classes ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
async def test_drug_classes_structure(drug_id):
    resp, elapsed_ms = await timed_get(f"/api/v1/drug/{drug_id}/drug-classes")
    assert resp.status_code == 200, f"drug-classes for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < PERFORMANCE_LIMIT_MS

    data = body["data"]
    assert isinstance(data, dict)
    assert "pharmacologic_class" in data
    assert "therapeutic_class" in data
    assert "mechanism_class" in data


# ── /population-info ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
@pytest.mark.parametrize("age,expected_category", [
    (5, "pediatric"),
    (35, "adult"),
    (70, "geriatric"),
])
async def test_population_info(drug_id, age, expected_category):
    resp, elapsed_ms = await timed_get(
        f"/api/v1/drug/{drug_id}/population-info", params={"age": age}
    )
    assert resp.status_code == 200, f"population-info age={age} for {drug_id} → {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert_base_envelope(body, drug_id)
    assert elapsed_ms < PERFORMANCE_LIMIT_MS

    data = body["data"]
    assert isinstance(data, dict)
    assert "population_category" in data
    assert "age" in data
    assert "text" in data
    assert "table" in data
    assert "subsections" in data
    assert data["population_category"] == expected_category, (
        f"age={age} → expected '{expected_category}', got '{data['population_category']}'"
    )
    assert data["age"] == age


# ── /dosing-regimen ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", ALL_DRUGS)
async def test_dosing_regimen_adult(drug_id):
    resp, elapsed_ms = await timed_get(
        f"/api/v1/drug/{drug_id}/dosing-regimen", params={"age": 35}
    )
    # Dosing may return 404 if no dosing rows — that's valid for some FDC drugs
    assert resp.status_code in (200, 404), (
        f"dosing for {drug_id} → unexpected {resp.status_code}: {resp.text[:200]}"
    )
    assert elapsed_ms < 5000, f"dosing took {elapsed_ms:.0f}ms (limit 5000ms)"

    body = resp.json()
    if resp.status_code == 200:
        assert body.get("success") is True
        assert body.get("drug_id_1mg") == drug_id
        assert isinstance(body.get("data"), list)
        for row in body["data"]:
            for key in ("brand_name", "salt_composition", "generic_name",
                        "frequency", "route", "dose_amount", "dose_unit",
                        "duration", "indication", "instructions"):
                assert key in row, f"dosing row missing '{key}': {row}"
    else:
        assert body.get("error_code") == "NO_DOSING_DATA"
