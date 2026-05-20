#!/usr/bin/env python3
"""
Max-concurrency capacity finder for CDSS Drug Info API.

Sends increasing waves of simultaneous users and reports exactly where
the server starts failing — giving you the safe operating ceiling.

Usage:
  python3 tests/comprehensive/find_max_concurrency.py

Stop conditions (either triggers a "DEGRADED" label and halts the ramp):
  - >5% of requests return 5xx
  - >5% of requests time out (30s each)
  - >10% of requests fail for any reason
"""
import asyncio
import os
import sys
import time

import httpx

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

ALL_DRUGS = [
    "1000019", "1000037", "1000041", "1000042", "1002775",
    "1000006", "1000008", "1000013", "1000015", "1000098",
]

# Ramp schedule: add more levels if you want to probe higher
CONCURRENCY_LEVELS = [50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000]
REQUEST_TIMEOUT_S = 30.0
FAILURE_THRESHOLD = 0.05   # stop if >5% requests fail or time out


def check_server() -> bool:
    try:
        import json
        import urllib.request
        r = urllib.request.urlopen(f"{BASE_URL}/health", timeout=5)
        return json.loads(r.read()).get("status") == "healthy"
    except Exception:
        return False


async def run_level(n: int) -> dict:
    """Fire n concurrent requests and collect per-request latencies."""
    drugs = [ALL_DRUGS[i % len(ALL_DRUGS)] for i in range(n)]

    # Allow enough connections so httpx itself is not the bottleneck
    limits = httpx.Limits(
        max_connections=n + 50,
        max_keepalive_connections=n + 50,
    )

    success = 0
    fail_5xx = 0
    fail_other = 0
    timeouts = 0
    net_errors = 0
    latencies: list[float] = []

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=REQUEST_TIMEOUT_S,
        limits=limits,
    ) as client:

        async def one(drug_id: str):
            nonlocal success, fail_5xx, fail_other, timeouts, net_errors
            try:
                t0 = time.perf_counter()
                resp = await client.get(
                    f"/api/v1/drug/{drug_id}/generic-name",
                    headers=AUTH_HDR,
                )
                latencies.append((time.perf_counter() - t0) * 1000)
                if resp.status_code == 200:
                    success += 1
                elif resp.status_code >= 500:
                    fail_5xx += 1
                else:
                    fail_other += 1
            except httpx.TimeoutException:
                timeouts += 1
            except Exception:
                net_errors += 1

        wall_t0 = time.perf_counter()
        await asyncio.gather(*[one(d) for d in drugs])
        wall_ms = (time.perf_counter() - wall_t0) * 1000

    avg_ms = sum(latencies) / len(latencies) if latencies else 0
    sorted_lat = sorted(latencies)
    p95_ms = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0
    p99_ms = sorted_lat[int(len(sorted_lat) * 0.99)] if sorted_lat else 0

    total_failures = fail_5xx + timeouts + net_errors
    failure_rate = total_failures / n

    if fail_5xx > n * FAILURE_THRESHOLD:
        status = "5XX ERRORS"
    elif timeouts > n * FAILURE_THRESHOLD:
        status = "TIMEOUTS"
    elif failure_rate > FAILURE_THRESHOLD:
        status = "DEGRADED"
    else:
        status = "OK"

    return {
        "n": n,
        "success": success,
        "fail_5xx": fail_5xx,
        "fail_other": fail_other,
        "timeouts": timeouts,
        "net_errors": net_errors,
        "wall_ms": wall_ms,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "p99_ms": p99_ms,
        "success_rate": success / n,
        "failure_rate": failure_rate,
        "status": status,
    }


async def main():
    if not check_server():
        print(f"ERROR: Server not reachable at {BASE_URL}/health")
        sys.exit(1)

    print(f"Server: {BASE_URL}")
    print(f"Endpoint tested: GET /api/v1/drug/{{id}}/generic-name  (lightest label endpoint)")
    print(f"Timeout per request: {REQUEST_TIMEOUT_S}s")
    print(f"Failure threshold: >{FAILURE_THRESHOLD:.0%} of requests")
    print()
    print(
        f"{'Users':>6}  "
        f"{'OK':>6}  "
        f"{'5xx':>5}  "
        f"{'TO':>5}  "
        f"{'Err':>5}  "
        f"{'Wall(s)':>7}  "
        f"{'Avg(ms)':>7}  "
        f"{'P95(ms)':>7}  "
        f"{'P99(ms)':>7}  "
        f"{'Rate':>6}  "
        f"Status"
    )
    print("─" * 88)

    max_safe = 0
    results_log = []

    for n in CONCURRENCY_LEVELS:
        sys.stdout.write(f"  → firing {n} concurrent requests …\r")
        sys.stdout.flush()

        r = await run_level(n)
        results_log.append(r)

        print(
            f"{r['n']:>6}  "
            f"{r['success']:>6}  "
            f"{r['fail_5xx']:>5}  "
            f"{r['timeouts']:>5}  "
            f"{r['net_errors']:>5}  "
            f"{r['wall_ms']/1000:>7.2f}  "
            f"{r['avg_ms']:>7.0f}  "
            f"{r['p95_ms']:>7.0f}  "
            f"{r['p99_ms']:>7.0f}  "
            f"{r['success_rate']:>5.1%}  "
            f"{r['status']}"
        )

        if r["status"] == "OK":
            max_safe = n
        else:
            print(f"\n  ↳ Degradation detected at {n} concurrent users — stopping ramp.")
            break

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("═" * 55)
    print("  CAPACITY SUMMARY")
    print("═" * 55)

    if max_safe == 0:
        print("  Server failed even at the lowest concurrency level.")
    elif max_safe == CONCURRENCY_LEVELS[-1] and all(r["status"] == "OK" for r in results_log):
        print(f"  All {CONCURRENCY_LEVELS[-1]} concurrent users handled successfully.")
        print(f"  The server handled every tested level without degradation.")
        print(f"  True maximum is higher — add larger values to CONCURRENCY_LEVELS")
        print(f"  in this script to probe further.")
    else:
        degraded = next((r for r in results_log if r["status"] != "OK"), None)
        print(f"  Max safe concurrent users : {max_safe}")
        if degraded:
            print(f"  Breaking point            : {degraded['n']} users")
            print(f"    - 5xx errors   : {degraded['fail_5xx']}")
            print(f"    - Timeouts     : {degraded['timeouts']}")
            print(f"    - Net errors   : {degraded['net_errors']}")
            print(f"    - Success rate : {degraded['success_rate']:.1%}")
            print(f"    - Wall time    : {degraded['wall_ms']/1000:.2f}s")

    print()
    # Best and worst latency at safe peak
    ok_results = [r for r in results_log if r["status"] == "OK"]
    if ok_results:
        peak = ok_results[-1]
        print(f"  At peak safe load ({peak['n']} users):")
        print(f"    Avg response : {peak['avg_ms']:.0f}ms")
        print(f"    P95 response : {peak['p95_ms']:.0f}ms")
        print(f"    P99 response : {peak['p99_ms']:.0f}ms")
        print(f"    Wall time    : {peak['wall_ms']/1000:.2f}s for all {peak['n']} requests")

    print("═" * 55)


if __name__ == "__main__":
    asyncio.run(main())
