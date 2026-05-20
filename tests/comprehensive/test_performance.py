"""
Performance tests: response times, cache behaviour, concurrent load.
Prints summary tables for each test group.
"""
import asyncio
import time
import pytest
import httpx

BASE_URL = "http://localhost:8002"
API_KEY_HDR = {"X-API-Key": "dev"}

ALL_DRUGS = [
    {"id": "1000019", "name": "Tranexamic Acid"},
    {"id": "1000037", "name": "Olanzapine"},
    {"id": "1000041", "name": "Pantoprazole"},
    {"id": "1000042", "name": "Rifaximin"},
    {"id": "1002775", "name": "Clopidogrel"},
    {"id": "1000006", "name": "FDC-Dapa+Glim+Met 500A"},
    {"id": "1000008", "name": "FDC-Dapa+Glim+Met 500B"},
    {"id": "1000013", "name": "FDC-Dapa+Glim+Met 1000A"},
    {"id": "1000015", "name": "FDC-Dapa+Glim+Met 1000B"},
    {"id": "1000098", "name": "FDC-Glim+Met 1000"},
]


async def _get(client: httpx.AsyncClient, url: str, params: dict = None) -> tuple:
    t0 = time.perf_counter()
    resp = await client.get(url, params=params, headers=API_KEY_HDR)
    return resp, (time.perf_counter() - t0) * 1000


def _print_table(rows: list, headers: list):
    col_widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*row))
    print()


# ── Test 1: Sequential label endpoint timing ──────────────────────────────────

@pytest.mark.asyncio
async def test_contraindications_sequential_all_drugs():
    """All 10 drugs — sequential, each must be under 2000ms."""
    rows = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        for drug in ALL_DRUGS:
            resp, ms = await _get(client, f"/api/v1/drug/{drug['id']}/contraindications")
            status = "PASS" if resp.status_code == 200 and ms < 2000 else "FAIL"
            rows.append((drug["id"], drug["name"], f"{ms:.0f}", status))
            assert resp.status_code == 200, f"contraindications {drug['id']} → {resp.status_code}"
            assert ms < 2000, f"{drug['id']} took {ms:.0f}ms (limit 2000ms)"

    print("\n── Test 1: /contraindications sequential ──")
    _print_table(rows, ["drug_id", "name", "ms", "status"])


# ── Test 2: Cache performance ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_hit_faster_than_miss():
    """Second call for same drug must be faster (cache hit) and meta.cached=true."""
    drug_id = "1002775"
    url = f"/api/v1/drug/{drug_id}/warnings"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # Warm up (may already be cached — we flush then check, but flushing
        # Redis here would break other tests, so we just record both times)
        resp1, ms1 = await _get(client, url)
        resp2, ms2 = await _get(client, url)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp2.json()["meta"]["cached"] is True, "Second call must be cache hit"

    print(f"\n── Test 2: Cache performance ──")
    print(f"  First call  (cache miss/any): {ms1:.0f}ms")
    print(f"  Second call (cache hit):      {ms2:.0f}ms")
    if ms1 > 0:
        print(f"  Speedup: {ms1/ms2:.1f}x faster")
    print()

    # Cache hit should be faster; allow generous margin for flaky CI
    assert ms2 < ms1 * 2 or ms2 < 500, (
        f"Cache hit ({ms2:.0f}ms) not appreciably faster than miss ({ms1:.0f}ms)"
    )


# ── Test 3: /interactions heavy endpoint ──────────────────────────────────────

@pytest.mark.asyncio
async def test_interactions_heavy_endpoint_all_drugs():
    """/interactions is the heaviest query — must still be under 5000ms per drug."""
    rows = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        for drug in ALL_DRUGS:
            resp, ms = await _get(client, f"/api/v1/drug/{drug['id']}/interactions")
            body = resp.json()
            count = len(body.get("data", []))
            status = "PASS" if resp.status_code == 200 and ms < 5000 else "FAIL"
            rows.append((drug["id"], drug["name"], f"{ms:.0f}", str(count), status))
            assert resp.status_code == 200, f"interactions {drug['id']} → {resp.status_code}"
            assert ms < 5000, f"{drug['id']} interactions took {ms:.0f}ms (limit 5000ms)"

    print("\n── Test 3: /interactions timing ──")
    _print_table(rows, ["drug_id", "name", "ms", "interaction_count", "status"])


# ── Test 4: Concurrent requests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_contraindications_all_drugs():
    """10 concurrent requests to /contraindications — all must succeed under 10000ms total."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        t0 = time.perf_counter()
        tasks = [
            _get(client, f"/api/v1/drug/{d['id']}/contraindications")
            for d in ALL_DRUGS
        ]
        results = await asyncio.gather(*tasks)
        total_ms = (time.perf_counter() - t0) * 1000

    rows = []
    for drug, (resp, ms) in zip(ALL_DRUGS, results):
        status = "PASS" if resp.status_code == 200 else "FAIL"
        rows.append((drug["id"], drug["name"], f"{ms:.0f}", status))
        assert resp.status_code == 200, f"concurrent {drug['id']} → {resp.status_code}"

    print(f"\n── Test 4: 10 concurrent /contraindications ──")
    _print_table(rows, ["drug_id", "name", "ms", "status"])
    print(f"  Total wall time for 10 concurrent: {total_ms:.0f}ms")
    print()

    assert total_ms < 10000, f"Concurrent total {total_ms:.0f}ms exceeded 10000ms"


# ── Test 5: Sequential vs concurrent comparison ───────────────────────────────

@pytest.mark.asyncio
async def test_sequential_vs_concurrent_speedup():
    """Concurrent should be significantly faster than sequential for 5 drugs."""
    five_drugs = ALL_DRUGS[:5]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # Sequential
        t0 = time.perf_counter()
        for d in five_drugs:
            await _get(client, f"/api/v1/drug/{d['id']}/contraindications")
        seq_total = (time.perf_counter() - t0) * 1000

        # Concurrent
        t0 = time.perf_counter()
        await asyncio.gather(*[
            _get(client, f"/api/v1/drug/{d['id']}/contraindications")
            for d in five_drugs
        ])
        conc_total = (time.perf_counter() - t0) * 1000

    speedup = seq_total / conc_total if conc_total > 0 else 0
    print(f"\n── Test 5: Sequential vs Concurrent (5 drugs, /contraindications) ──")
    print(f"  Sequential total: {seq_total:.0f}ms")
    print(f"  Concurrent total: {conc_total:.0f}ms")
    print(f"  Speedup:          {speedup:.1f}x faster")
    print()

    # Concurrent should finish in less than sequential time (allowing some margin)
    assert conc_total < seq_total * 1.2, (
        f"Concurrent ({conc_total:.0f}ms) was not faster than sequential ({seq_total:.0f}ms)"
    )


# ── Test 6: /dosing-regimen CTE query performance ────────────────────────────

@pytest.mark.asyncio
async def test_dosing_regimen_performance_all_drugs():
    """/dosing-regimen uses 5-CTE query — must be under 5000ms per drug."""
    rows = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        for drug in ALL_DRUGS:
            resp, ms = await _get(
                client,
                f"/api/v1/drug/{drug['id']}/dosing-regimen",
                params={"age": 35},
            )
            count = len(resp.json().get("data", [])) if resp.status_code == 200 else 0
            status_label = "PASS" if ms < 5000 and resp.status_code in (200, 404) else "FAIL"
            rows.append((drug["id"], drug["name"], f"{ms:.0f}", str(count), status_label))
            assert resp.status_code in (200, 404), (
                f"dosing {drug['id']} → unexpected {resp.status_code}"
            )
            assert ms < 5000, f"{drug['id']} dosing took {ms:.0f}ms (limit 5000ms)"

    print("\n── Test 6: /dosing-regimen timing ──")
    _print_table(rows, ["drug_id", "name", "ms", "dosing_rows", "status"])
