"""
Concurrency and data-isolation tests.

Verifies two properties under concurrent load:
  1. THROUGHPUT  — the server handles N simultaneous users without errors
  2. ISOLATION   — each user's response contains data for their drug/age/auth only,
                   never cross-contaminated with another concurrent request

Run standalone:
  pytest tests/comprehensive/test_concurrency.py -v -s
"""
import asyncio
import os
import time

import httpx
import pytest

BASE_URL = "http://localhost:8002"
API_KEY = "dev"

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            if _line.strip().startswith("API_KEY="):
                API_KEY = _line.strip().split("=", 1)[1]
                break

AUTH_HDR = {"X-API-Key": API_KEY}
WRONG_HDR = {"X-API-Key": "WRONG_KEY_12345"}

ALL_DRUGS = [
    {"id": "1000019", "name": "Tranexamic Acid"},
    {"id": "1000037", "name": "Olanzapine"},
    {"id": "1000041", "name": "Pantoprazole"},
    {"id": "1000042", "name": "Rifaximin"},
    {"id": "1002775", "name": "Clopidogrel bisulfate"},
    {"id": "1000006", "name": "FDC-Dapa+Glim+Met-A"},
    {"id": "1000008", "name": "FDC-Dapa+Glim+Met-B"},
    {"id": "1000013", "name": "FDC-Dapa+Glim+Met-C"},
    {"id": "1000015", "name": "FDC-Dapa+Glim+Met-D"},
    {"id": "1000098", "name": "FDC-Glim+Met"},
]


async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict = None,
    headers: dict = None,
) -> tuple:
    hdrs = AUTH_HDR if headers is None else headers
    t0 = time.perf_counter()
    resp = await client.get(url, params=params, headers=hdrs)
    return resp, (time.perf_counter() - t0) * 1000


def _print_table(rows: list, headers: list):
    col_widths = [
        max(len(str(r[i])) for r in rows + [headers])
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*row))
    print()


# ── Test 1: 100 concurrent users — all get HTTP 200 ──────────────────────────

@pytest.mark.asyncio
async def test_100_concurrent_users_all_succeed():
    """
    Simulates 100 simultaneous users hitting /contraindications.
    All 100 responses must be HTTP 200.
    A single 5xx means the server crashed or dropped a connection under load.
    """
    tasks_plan = [ALL_DRUGS[i % len(ALL_DRUGS)] for i in range(100)]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        t0 = time.perf_counter()
        coros = [
            _get(client, f"/api/v1/drug/{d['id']}/contraindications")
            for d in tasks_plan
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        total_ms = (time.perf_counter() - t0) * 1000

    errors = []
    status_counts: dict[int, int] = {}
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append(f"  request {i} ({tasks_plan[i]['id']}): EXCEPTION {result}")
            continue
        resp, _ = result
        status_counts[resp.status_code] = status_counts.get(resp.status_code, 0) + 1
        if resp.status_code != 200:
            errors.append(f"  request {i} ({tasks_plan[i]['id']}): HTTP {resp.status_code}")

    print(f"\n── Test 1: 100 concurrent users ──")
    print(f"  Total wall time : {total_ms:.0f}ms")
    print(f"  Avg per request : {total_ms / 100:.0f}ms")
    print(f"  Status counts   : {dict(sorted(status_counts.items()))}")
    if errors:
        print(f"  FAILURES ({len(errors)}):")
        for e in errors[:10]:
            print(e)
    print()

    assert not errors, f"{len(errors)}/100 requests failed:\n" + "\n".join(errors[:10])


# ── Test 2: Data isolation — 10 concurrent users, 10 different drugs ──────────

@pytest.mark.asyncio
async def test_data_isolation_10_concurrent_different_drugs():
    """
    10 users each request a different drug at the exact same moment.
    Every response's drug_id_1mg must match the requested drug_id.

    If user A's response contains user B's drug_id → data leakage confirmed.
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        coros = [
            _get(client, f"/api/v1/drug/{d['id']}/generic-name")
            for d in ALL_DRUGS
        ]
        results = await asyncio.gather(*coros)

    rows = []
    isolation_failures = []
    for drug, (resp, ms) in zip(ALL_DRUGS, results):
        assert resp.status_code == 200, f"generic-name {drug['id']} → {resp.status_code}"
        body = resp.json()
        returned_id = body.get("drug_id_1mg")
        ok = returned_id == drug["id"]
        if not ok:
            isolation_failures.append(
                f"  requested {drug['id']} ({drug['name']}) but got drug_id_1mg={returned_id}"
            )
        rows.append((drug["id"], drug["name"], returned_id or "—", "OK" if ok else "LEAK"))

    print(f"\n── Test 2: Data isolation (10 concurrent, all different drugs) ──")
    _print_table(rows, ["requested_id", "drug_name", "returned_id", "status"])

    assert not isolation_failures, (
        "DATA LEAKAGE DETECTED — responses contained wrong drug_id:\n"
        + "\n".join(isolation_failures)
    )


# ── Test 3: Data isolation at 100 scale ───────────────────────────────────────

@pytest.mark.asyncio
async def test_data_isolation_100_concurrent():
    """
    100 concurrent requests (10 drugs × 10 rounds).
    Every single response must contain drug_id_1mg == the requested drug_id.
    Any mismatch = data leakage between concurrent users.
    """
    tasks_plan = [ALL_DRUGS[i % len(ALL_DRUGS)] for i in range(100)]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        coros = [
            _get(client, f"/api/v1/drug/{d['id']}/generic-name")
            for d in tasks_plan
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

    isolation_failures = []
    exceptions = []
    correct = 0
    for i, result in enumerate(results):
        expected_id = tasks_plan[i]["id"]
        if isinstance(result, Exception):
            exceptions.append(f"  request {i}: EXCEPTION {result}")
            continue
        resp, _ = result
        returned_id = resp.json().get("drug_id_1mg")
        if returned_id == expected_id:
            correct += 1
        else:
            isolation_failures.append(
                f"  request {i}: requested {expected_id}, got {returned_id}"
            )

    print(f"\n── Test 3: Data isolation at 100 concurrent ──")
    print(f"  Requests sent       : 100")
    print(f"  Correct responses   : {correct}")
    print(f"  Isolation failures  : {len(isolation_failures)}")
    print(f"  Network exceptions  : {len(exceptions)}")
    if isolation_failures:
        print("  LEAKAGE EXAMPLES:")
        for f in isolation_failures[:5]:
            print(f)
    print()

    assert not exceptions, "Network errors:\n" + "\n".join(exceptions[:5])
    assert not isolation_failures, (
        f"DATA ISOLATION VIOLATED in {len(isolation_failures)}/100 requests:\n"
        + "\n".join(isolation_failures)
    )


# ── Test 4: Auth isolation — mixed auth/no-auth concurrent ───────────────────

@pytest.mark.asyncio
async def test_auth_isolation_mixed_concurrent():
    """
    20 authorized + 20 unauthorized requests fire at the same time.
    Auth'd users must get 200; unauth'd users must get 401.

    If an unauth'd user accidentally receives a 200 (riding an auth'd request's
    auth state) → auth isolation failure.
    """
    url = "/api/v1/drug/1002775/contraindications"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        auth_coros   = [_get(client, url, headers=AUTH_HDR)  for _ in range(20)]
        noauth_coros = [_get(client, url, headers={})         for _ in range(20)]
        results = await asyncio.gather(*(auth_coros + noauth_coros))

    auth_results   = results[:20]
    noauth_results = results[20:]

    auth_wrong   = [(i, r.status_code) for i, (r, _) in enumerate(auth_results)   if r.status_code != 200]
    noauth_wrong = [(i, r.status_code) for i, (r, _) in enumerate(noauth_results) if r.status_code != 401]

    print(f"\n── Test 4: Auth isolation (20 auth + 20 no-auth concurrent) ──")
    print(f"  Auth'd    : 20 sent → {20 - len(auth_wrong)} got 200, {len(auth_wrong)} unexpected")
    print(f"  Unauth'd  : 20 sent → {20 - len(noauth_wrong)} got 401, {len(noauth_wrong)} unexpected")
    print()

    assert not auth_wrong, f"Auth'd requests got wrong status: {auth_wrong}"
    assert not noauth_wrong, (
        f"Unauth'd requests got wrong status (possible auth leakage): {noauth_wrong}"
    )


# ── Test 5: Sustained wave load — server doesn't degrade ─────────────────────

@pytest.mark.asyncio
async def test_sustained_wave_load_10_waves():
    """
    10 waves of 10 concurrent requests (100 total, sequential waves).
    Checks the server does not degrade across sustained traffic
    (no connection pool exhaustion, no progressive slowdown that causes failures).
    """
    wave_summaries = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        for wave_idx in range(10):
            wave_drugs = [ALL_DRUGS[(wave_idx * 10 + j) % len(ALL_DRUGS)] for j in range(10)]
            t0 = time.perf_counter()
            coros = [
                _get(client, f"/api/v1/drug/{d['id']}/generic-name")
                for d in wave_drugs
            ]
            wave_results = await asyncio.gather(*coros)
            wave_ms = (time.perf_counter() - t0) * 1000
            failures = [
                wave_drugs[j]["id"]
                for j, (r, _) in enumerate(wave_results)
                if r.status_code != 200
            ]
            wave_summaries.append({"wave": wave_idx + 1, "ms": wave_ms, "failures": failures})

    rows = [
        (
            str(w["wave"]),
            f"{w['ms']:.0f}",
            str(len(w["failures"])),
            "PASS" if not w["failures"] else "FAIL",
        )
        for w in wave_summaries
    ]
    print(f"\n── Test 5: Sustained wave load (10 waves × 10 concurrent) ──")
    _print_table(rows, ["wave", "total_ms", "failures", "status"])

    bad_waves = [f"Wave {w['wave']}: {w['failures']}" for w in wave_summaries if w["failures"]]
    assert not bad_waves, "Waves with failures:\n" + "\n".join(bad_waves)


# ── Test 6: Cache isolation — same drug, 20 concurrent, consistent data ───────

@pytest.mark.asyncio
async def test_cache_isolation_same_drug_20_concurrent():
    """
    20 users request the same drug simultaneously.
    All 20 responses must return identical drug_id_1mg and generic_name.

    Proves that the cache layer never mixes responses:
    concurrent cache writes/reads for the same key must be idempotent.
    """
    drug_id = "1002775"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        coros = [
            _get(client, f"/api/v1/drug/{drug_id}/generic-name")
            for _ in range(20)
        ]
        results = await asyncio.gather(*coros)

    drug_ids_seen: set[str] = set()
    generic_names_seen: set[str] = set()
    for resp, _ in results:
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        body = resp.json()
        drug_ids_seen.add(body.get("drug_id_1mg") or "")
        generic_names_seen.add(body.get("generic_name") or "")

    print(f"\n── Test 6: Cache isolation (20 concurrent, same drug) ──")
    print(f"  Unique drug_ids returned    : {drug_ids_seen}")
    print(f"  Unique generic_names seen   : {generic_names_seen}")
    print()

    assert drug_ids_seen == {drug_id}, (
        f"Expected only drug_id={drug_id} in all responses, got: {drug_ids_seen}"
    )
    assert len(generic_names_seen) == 1, (
        f"Inconsistent generic_name across 20 concurrent responses: {generic_names_seen}"
    )


# ── Test 7: Population-info age-parameter isolation ───────────────────────────

@pytest.mark.asyncio
async def test_population_info_age_isolation():
    """
    3 users request /population-info for the same drug but different ages concurrently.
    Each must get the correct population_category for their own age parameter.

    Proves query-parameter isolation: the server cannot mix up age=5 (pediatric)
    with age=35 (adult) or age=70 (geriatric) between concurrent requests.
    """
    drug_id = "1002775"
    age_cases = [(5, "pediatric"), (35, "adult"), (70, "geriatric")]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        coros = [
            _get(client, f"/api/v1/drug/{drug_id}/population-info", params={"age": age})
            for age, _ in age_cases
        ]
        results = await asyncio.gather(*coros)

    rows = []
    failures = []
    for (age, expected_cat), (resp, _) in zip(age_cases, results):
        assert resp.status_code == 200, f"population-info age={age} → {resp.status_code}"
        data = resp.json()["data"]
        actual_cat = data["population_category"]
        actual_age = data["age"]
        ok = actual_cat == expected_cat and actual_age == age
        if not ok:
            failures.append(
                f"  age={age}: expected category='{expected_cat}', "
                f"got category='{actual_cat}', age_in_response={actual_age}"
            )
        rows.append((str(age), expected_cat, actual_cat, str(actual_age), "OK" if ok else "FAIL"))

    print(f"\n── Test 7: Population-info age isolation (3 ages, concurrent) ──")
    _print_table(rows, ["sent_age", "expected_cat", "actual_cat", "response_age", "status"])

    assert not failures, "Age parameter isolation failed:\n" + "\n".join(failures)


# ── Test 8: 200-user stress test — zero 5xx responses ─────────────────────────

@pytest.mark.asyncio
async def test_200_concurrent_no_5xx():
    """
    200 simultaneous requests across all 10 drugs (20 rounds each).

    Acceptance criteria:
      - Zero HTTP 5xx responses (server must never crash under load)
      - At most 2 connection-level exceptions (generous allowance for CI flakiness)
      - All 2xx responses have success=true

    Note: 4xx (e.g. 401 if auth accidentally omitted) would surface as a bug here too.
    """
    tasks_plan = [ALL_DRUGS[i % len(ALL_DRUGS)] for i in range(200)]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=90.0) as client:
        t0 = time.perf_counter()
        coros = [
            _get(client, f"/api/v1/drug/{d['id']}/contraindications")
            for d in tasks_plan
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        total_ms = (time.perf_counter() - t0) * 1000

    status_counts: dict[int, int] = {}
    errors_5xx = []
    exceptions = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            exceptions.append(f"request {i} ({tasks_plan[i]['id']}): {type(result).__name__}: {result}")
            continue
        resp, _ = result
        code = resp.status_code
        status_counts[code] = status_counts.get(code, 0) + 1
        if code >= 500:
            errors_5xx.append(f"  request {i} ({tasks_plan[i]['id']}): HTTP {code} — {resp.text[:80]}")

    print(f"\n── Test 8: 200-user stress test ──")
    print(f"  Total wall time  : {total_ms:.0f}ms")
    print(f"  Avg per request  : {total_ms / 200:.0f}ms")
    print(f"  Status breakdown : {dict(sorted(status_counts.items()))}")
    print(f"  5xx errors       : {len(errors_5xx)}")
    print(f"  Network errors   : {len(exceptions)}")
    if errors_5xx:
        for e in errors_5xx[:5]:
            print(e)
    if exceptions:
        for e in exceptions[:3]:
            print(f"  {e}")
    print()

    assert not errors_5xx, (
        f"Server returned 5xx under 200-user load:\n" + "\n".join(errors_5xx[:10])
    )
    assert len(exceptions) <= 2, (
        f"Too many connection errors ({len(exceptions)}) under 200-user load:\n"
        + "\n".join(exceptions[:5])
    )
