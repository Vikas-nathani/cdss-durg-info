"""
Comprehensive test suite configuration.
Connects to the live server at localhost:8002 using real HTTP via httpx.
"""
import os
import time
import pytest
import httpx

BASE_URL = "http://localhost:8002"

# Read API key from .env file in project root
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
API_KEY = "dev"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("API_KEY="):
                API_KEY = line.split("=", 1)[1].strip()
                break


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api_key():
    return API_KEY


@pytest.fixture(scope="session")
def auth_headers():
    return {"X-API-Key": API_KEY}


# ── Drug fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def single_ingredient_drugs():
    return [
        {"id": "1000019", "name": "Tranexamic Acid"},
        {"id": "1000037", "name": "Olanzapine"},
        {"id": "1000041", "name": "Pantoprazole"},
        {"id": "1000042", "name": "Rifaximin"},
        {"id": "1002775", "name": "Clopidogrel bisulfate"},
    ]


@pytest.fixture(scope="session")
def fdc_drugs():
    return [
        {"id": "1000006", "name": "Dapagliflozin+Glimepiride+Metformin 500mg (A)"},
        {"id": "1000008", "name": "Dapagliflozin+Glimepiride+Metformin 500mg (B)"},
        {"id": "1000013", "name": "Dapagliflozin+Glimepiride+Metformin 1000mg (A)"},
        {"id": "1000015", "name": "Dapagliflozin+Glimepiride+Metformin 1000mg (B)"},
        {"id": "1000098", "name": "Glimepiride+Metformin 1000mg"},
    ]


@pytest.fixture(scope="session")
def all_test_drugs(single_ingredient_drugs, fdc_drugs):
    return single_ingredient_drugs + fdc_drugs


@pytest.fixture(scope="session")
def no_dosing_drugs():
    return [
        {"id": "411423",  "name": "Minovayl 100mg Injection"},
        {"id": "779258",  "name": "Diavaine 100mg Tablet"},
        {"id": "18840",   "name": "Minoz OD 100 Capsule"},
    ]


@pytest.fixture(scope="session")
def invalid_drug_id():
    return "999999999"


# ── HTTP helper ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def http_client():
    """Synchronous-style fixture that exposes a thin async GET helper."""
    # We return a helper dict so individual tests can use httpx directly
    return {"base_url": BASE_URL, "headers": {"X-API-Key": API_KEY}}


async def timed_get(url: str, params: dict = None) -> tuple:
    """
    Make a GET request with the API key header.
    Returns (response, elapsed_ms).
    """
    headers = {"X-API-Key": API_KEY}
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return resp, elapsed_ms


async def timed_get_no_auth(url: str, params: dict = None) -> tuple:
    """Same as timed_get but without API key header."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        resp = await client.get(url, params=params)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return resp, elapsed_ms


async def timed_get_wrong_auth(url: str, params: dict = None) -> tuple:
    """Same as timed_get but with wrong API key."""
    headers = {"X-API-Key": "WRONG_KEY_12345"}
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return resp, elapsed_ms
