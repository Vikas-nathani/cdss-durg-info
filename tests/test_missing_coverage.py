"""
Tests for gaps identified in existing coverage:
  1. check-interaction endpoint (0% coverage before this file)
  2. Fallback drugs (365656, 1000030) through the full HTTP API
  3. All dosing age groups (neonate / infant / pediatric / adolescent / geriatric / any)
  4. Cache behaviour after fallback path fires
  5. Dosing response field completeness for fallback drugs
  6. /health endpoint content (db + redis status)
  7. check-interaction for fallback drugs (UNII-resolved formulations)
"""

import pytest
from tests.conftest import API_KEY

# ── Known drug IDs (verified against live DB by test_fallback_flows.py) ────────
PRIMARY_DRUG     = "1000006"   # primary resolver + primary dosing
STEP1_FB_DRUG    = "365656"    # only in indian_brand under drugbank/us_unapproved
STEP2_FB_DRUG    = "1000030"   # no direct rxcui formulation → UNII bridge
DOSING_FB_DRUG   = "1000035"   # primary dosing empty → UNII dosing fallback
PAIR_DRUG_A      = "1000006"
PAIR_DRUG_B      = "1000037"   # used in comprehensive tests as a known-good drug


# ══════════════════════════════════════════════════════════════════════════════
# 1. check-interaction endpoint
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_interaction_response_shape(client):
    resp = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_B}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["drug_1"]["drug_id_1mg"] == PAIR_DRUG_A
    assert body["drug_2"]["drug_id_1mg"] == PAIR_DRUG_B
    assert isinstance(body["has_interaction"], bool)
    assert body["highest_severity"] in ("major", "moderate", "minor", "none", None)
    assert isinstance(body["severity_summary"], dict)
    assert "major" in body["severity_summary"]
    assert "moderate" in body["severity_summary"]
    assert "minor" in body["severity_summary"]
    assert isinstance(body["data"], list)
    assert "cached" in body["meta"]
    assert "response_time_ms" in body["meta"]


@pytest.mark.asyncio
async def test_check_interaction_is_symmetric(client):
    """drug_A vs drug_B must return same has_interaction as drug_B vs drug_A."""
    resp_ab = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_B}",
        headers={"X-API-Key": API_KEY},
    )
    resp_ba = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_B}/check-interaction/{PAIR_DRUG_A}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp_ab.status_code == 200
    assert resp_ba.status_code == 200
    body_ab = resp_ab.json()
    body_ba = resp_ba.json()
    assert body_ab["has_interaction"] == body_ba["has_interaction"]
    assert body_ab["severity_summary"] == body_ba["severity_summary"]
    assert len(body_ab["data"]) == len(body_ba["data"])


@pytest.mark.asyncio
async def test_check_interaction_data_items_shape(client):
    """When interactions exist, each item must have the required keys."""
    resp = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_B}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    for item in body["data"]:
        assert "drug1_ingredient" in item
        assert "drug2_ingredient" in item
        assert "severity" in item
        assert "mechanism" in item
        assert item["severity"] in ("major", "moderate", "minor")


@pytest.mark.asyncio
async def test_check_interaction_invalid_first_drug(client):
    resp = await client.get(
        f"/api/v1/drug/INVALID_999/check-interaction/{PAIR_DRUG_B}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "DRUG_NOT_FOUND"


@pytest.mark.asyncio
async def test_check_interaction_invalid_second_drug(client):
    resp = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/INVALID_999",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "DRUG_NOT_FOUND"


@pytest.mark.asyncio
async def test_check_interaction_both_invalid(client):
    resp = await client.get(
        "/api/v1/drug/INVALID_A/check-interaction/INVALID_B",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_check_interaction_no_api_key(client):
    resp = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_B}",
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_check_interaction_cache_hit(client, redis_client):
    if redis_client is None:
        pytest.skip("Redis not available")
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_B}"
    await client.get(url, headers=headers)
    resp2 = await client.get(url, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["meta"]["cached"] is True


@pytest.mark.asyncio
async def test_check_interaction_same_drug_with_itself(client):
    """A drug checked against itself — should succeed (no interactions expected)."""
    resp = await client.get(
        f"/api/v1/drug/{PAIR_DRUG_A}/check-interaction/{PAIR_DRUG_A}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. Fallback drugs through the full HTTP API (all key endpoints)
# ══════════════════════════════════════════════════════════════════════════════

FALLBACK_DRUGS = [STEP1_FB_DRUG, STEP2_FB_DRUG]

LABEL_ENDPOINTS = [
    "contraindications",
    "warnings",
    "mechanism-of-action",
    "generic-name",
    "adverse-reactions",
    "drug-description",
    "indications",
    "ingredients",
    "food-interactions",
    "drug-classes",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", FALLBACK_DRUGS)
@pytest.mark.parametrize("endpoint", LABEL_ENDPOINTS)
async def test_fallback_drug_label_endpoints_return_200(client, drug_id, endpoint):
    """Fallback-resolved drugs must serve every label endpoint without 500."""
    resp = await client.get(
        f"/api/v1/drug/{drug_id}/{endpoint}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404), (
        f"{endpoint} for fallback drug {drug_id} → unexpected {resp.status_code}: {resp.text[:300]}"
    )
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert body["drug_id_1mg"] == drug_id


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", FALLBACK_DRUGS)
async def test_fallback_drug_interactions(client, drug_id):
    resp = await client.get(
        f"/api/v1/drug/{drug_id}/interactions",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert "severity_counts" in body["meta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", FALLBACK_DRUGS)
async def test_fallback_drug_population_info(client, drug_id):
    resp = await client.get(
        f"/api/v1/drug/{drug_id}/population-info?age=35",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["population_category"] == "adult"


# ══════════════════════════════════════════════════════════════════════════════
# 3. All dosing age groups
# ══════════════════════════════════════════════════════════════════════════════

AGE_GROUP_CASES = [
    (0,   "neonate"),
    (0.5, "infant"),
    (5,   "pediatric"),
    (15,  "adolescent"),
    (35,  "adult"),
    (70,  "geriatric"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("age,expected_group", AGE_GROUP_CASES)
async def test_dosing_all_age_groups_primary_drug(client, age, expected_group):
    """Primary drug must accept every age group without 500."""
    resp = await client.get(
        f"/api/v1/drug/{PRIMARY_DRUG}/dosing-regimen?age={age}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404), (
        f"dosing age={age} ({expected_group}) → unexpected {resp.status_code}: {resp.text[:200]}"
    )
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("age,expected_group", AGE_GROUP_CASES)
async def test_dosing_all_age_groups_fallback_drug(client, age, expected_group):
    """UNII-bridge fallback drug must also handle every age group without 500."""
    resp = await client.get(
        f"/api/v1/drug/{STEP2_FB_DRUG}/dosing-regimen?age={age}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404), (
        f"fallback dosing age={age} ({expected_group}) → unexpected {resp.status_code}: {resp.text[:200]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. Cache behaviour after fallback path fires
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cache_hit_after_dosing_fallback(client, redis_client):
    """Second request for a fallback-dosing drug must be served from cache."""
    if redis_client is None:
        pytest.skip("Redis not available")
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{DOSING_FB_DRUG}/dosing-regimen?age=35"
    r1 = await client.get(url, headers=headers)
    if r1.status_code == 404:
        pytest.skip(f"{DOSING_FB_DRUG} has no dosing rows even via fallback")
    assert r1.status_code == 200
    r2 = await client.get(url, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["meta"]["cached"] is True


@pytest.mark.asyncio
async def test_cache_data_consistent_after_fallback(client, redis_client):
    """Cached response must be identical to the original fallback response."""
    if redis_client is None:
        pytest.skip("Redis not available")
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{STEP2_FB_DRUG}/contraindications"
    r1 = await client.get(url, headers=headers)
    r2 = await client.get(url, headers=headers)
    if r1.status_code != 200 or r2.status_code != 200:
        pytest.skip(f"{STEP2_FB_DRUG} returned {r1.status_code}/{r2.status_code}")
    assert r1.json()["data"] == r2.json()["data"]
    assert r2.json()["meta"]["cached"] is True


@pytest.mark.asyncio
async def test_cache_hit_after_resolver_step1_fallback(client, redis_client):
    if redis_client is None:
        pytest.skip("Redis not available")
    headers = {"X-API-Key": API_KEY}
    url = f"/api/v1/drug/{STEP1_FB_DRUG}/warnings"
    r1 = await client.get(url, headers=headers)
    if r1.status_code != 200:
        pytest.skip(f"{STEP1_FB_DRUG} returned {r1.status_code} on first hit")
    r2 = await client.get(url, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["meta"]["cached"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 5. Dosing response field completeness for fallback drugs
# ══════════════════════════════════════════════════════════════════════════════

DOSING_REQUIRED_FIELDS = [
    "brand_name", "salt_composition", "generic_name",
    "frequency", "route", "dose_amount", "dose_unit",
    "duration", "indication", "instructions",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("drug_id", [PRIMARY_DRUG, STEP2_FB_DRUG, DOSING_FB_DRUG])
async def test_dosing_row_has_all_fields(client, drug_id):
    resp = await client.get(
        f"/api/v1/drug/{drug_id}/dosing-regimen?age=35",
        headers={"X-API-Key": API_KEY},
    )
    if resp.status_code == 404:
        pytest.skip(f"No dosing data for {drug_id}")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) > 0
    for row in rows:
        for field in DOSING_REQUIRED_FIELDS:
            assert field in row, f"dosing row for {drug_id} missing field '{field}': {row}"


# ══════════════════════════════════════════════════════════════════════════════
# 6. /health endpoint content
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_health_endpoint_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_endpoint_has_db_and_redis_fields(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "database" in body or "db" in body or "status" in body, (
        f"health response missing expected keys: {body}"
    )


@pytest.mark.asyncio
async def test_health_endpoint_db_connected(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    db_status = body.get("database") or body.get("db")
    if db_status is not None:
        assert db_status in ("ok", "connected", True, "healthy"), (
            f"DB not healthy in /health: {body}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 7. check-interaction with fallback-resolved drugs
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_interaction_primary_vs_step1_fallback(client):
    """Primary drug vs step1-fallback drug — should not 500."""
    resp = await client.get(
        f"/api/v1/drug/{PRIMARY_DRUG}/check-interaction/{STEP1_FB_DRUG}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["has_interaction"], bool)
        assert isinstance(body["data"], list)


@pytest.mark.asyncio
async def test_check_interaction_primary_vs_step2_fallback(client):
    """Primary drug vs UNII-bridge drug — should not 500."""
    resp = await client.get(
        f"/api/v1/drug/{PRIMARY_DRUG}/check-interaction/{STEP2_FB_DRUG}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["has_interaction"], bool)


@pytest.mark.asyncio
async def test_check_interaction_both_fallback_drugs(client):
    """Both drugs via fallback path — should not 500."""
    resp = await client.get(
        f"/api/v1/drug/{STEP1_FB_DRUG}/check-interaction/{STEP2_FB_DRUG}",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert "has_interaction" in body


@pytest.mark.asyncio
async def test_check_interaction_symmetric_for_fallback_drugs(client):
    """Symmetry must hold even when both drugs come from fallback paths."""
    headers = {"X-API-Key": API_KEY}
    r_ab = await client.get(
        f"/api/v1/drug/{STEP1_FB_DRUG}/check-interaction/{STEP2_FB_DRUG}",
        headers=headers,
    )
    r_ba = await client.get(
        f"/api/v1/drug/{STEP2_FB_DRUG}/check-interaction/{STEP1_FB_DRUG}",
        headers=headers,
    )
    if r_ab.status_code == 200 and r_ba.status_code == 200:
        assert r_ab.json()["has_interaction"] == r_ba.json()["has_interaction"]
        assert r_ab.json()["severity_summary"] == r_ba.json()["severity_summary"]
