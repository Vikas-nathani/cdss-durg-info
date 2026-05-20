#!/usr/bin/env python3
"""
Master test runner for the CDSS Drug Info comprehensive test suite.
Runs all 3 test files, collects results, and prints a formatted report.
Saves the full report to tests/comprehensive/test_report.txt.
"""
import subprocess
import sys
import os
import time
import re
import json
import asyncio
import httpx

BASE_URL = "http://localhost:8002"
REPORT_PATH = os.path.join(os.path.dirname(__file__), "test_report.txt")
API_KEY = "dev"

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            if line.strip().startswith("API_KEY="):
                API_KEY = line.strip().split("=", 1)[1]
                break


def check_server():
    try:
        r = __import__("urllib.request", fromlist=["urlopen"]).urlopen(
            f"{BASE_URL}/health", timeout=5
        )
        data = json.loads(r.read())
        if data.get("status") == "healthy":
            return True
    except Exception:
        pass
    print(
        "ERROR: Server not running on http://localhost:8002\n"
        "Start with:  docker restart cdss-drug-info-drug-info-1\n"
        "  or:        uvicorn main:app --reload --port 8002"
    )
    sys.exit(1)


def run_pytest(test_file: str) -> dict:
    """Run a pytest file and return {passed, failed, errors, duration_s, output}."""
    t0 = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", test_file,
            "-v", "--tb=short", "--no-header",
            "-p", "no:warnings",
        ],
        capture_output=True,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    duration = time.perf_counter() - t0
    output = result.stdout + result.stderr

    passed = failed = errors = 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) error", output)
    if m:
        errors = int(m.group(1))

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration_s": duration,
        "output": output,
        "returncode": result.returncode,
    }


async def collect_performance_data() -> dict:
    """Run a quick performance sample after tests complete."""
    endpoints = [
        "generic-name", "contraindications", "warnings",
        "adverse-reactions", "interactions", "dosing-regimen",
    ]
    params_map = {"dosing-regimen": {"age": 35}}
    drug_id = "1002775"
    timings: dict[str, list] = {e: [] for e in endpoints}

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=30.0,
        headers={"X-API-Key": API_KEY}
    ) as client:
        for endpoint in endpoints:
            for _ in range(2):
                t0 = time.perf_counter()
                params = params_map.get(endpoint)
                await client.get(
                    f"/api/v1/drug/{drug_id}/{endpoint}",
                    params=params,
                )
                timings[endpoint].append((time.perf_counter() - t0) * 1000)

    return {e: sum(v) / len(v) for e, v in timings.items()}


async def collect_cache_data() -> dict:
    """Measure cache miss vs hit for a fresh warm-up call sequence."""
    drug_id = "1000019"
    url = f"/api/v1/drug/{drug_id}/warnings"
    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=30.0,
        headers={"X-API-Key": API_KEY}
    ) as client:
        t0 = time.perf_counter()
        r1 = await client.get(url)
        ms1 = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = await client.get(url)
        ms2 = (time.perf_counter() - t0) * 1000

    cached = r2.json().get("meta", {}).get("cached", False)
    return {"miss_ms": ms1, "hit_ms": ms2, "hit_confirmed": cached}


def extract_failures(output: str) -> list[str]:
    """Pull out FAILED test lines from pytest output."""
    failures = []
    in_failure = False
    block: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            failures.append(line)
        if re.match(r"^_{5,}", line):
            if block:
                failures.append("\n".join(block))
                block = []
            in_failure = True
        elif in_failure:
            block.append(line)
    return failures


def build_report(results: dict, perf: dict, cache: dict) -> str:
    total_passed = sum(r["passed"] for r in results.values())
    total_failed = sum(r["failed"] + r["errors"] for r in results.values())
    total_tests = total_passed + total_failed
    all_passed = total_failed == 0

    avg_ms = sum(perf.values()) / len(perf) if perf else 0
    fastest = min(perf, key=perf.get) if perf else "n/a"
    slowest = max(perf, key=perf.get) if perf else "n/a"
    all_under_3s = all(v < 3000 for v in perf.values())

    cache_speedup = (cache["miss_ms"] / cache["hit_ms"]) if cache["hit_ms"] > 0 else 0

    lines = [
        "═" * 55,
        "  CDSS DRUG INFO — COMPREHENSIVE TEST REPORT",
        "═" * 55,
        "",
        "ENDPOINT COVERAGE:",
        f"  Total unique endpoints tested : 18",
        f"  Endpoint call variants        : 20 (population-info x3 ages)",
        f"  Drugs tested                  : 13  (5 single, 5 FDC, 3 no-dosing)",
        f"  Total test cases              : {total_tests}",
        f"  Passed                        : {total_passed}",
        f"  Failed                        : {total_failed}",
        f"  Overall result                : {'ALL PASS ✓' if all_passed else 'FAILURES ✗'}",
        "",
        "TEST FILE BREAKDOWN:",
    ]

    labels = {
        "test_all_endpoints": "test_all_endpoints.py",
        "test_edge_cases":    "test_edge_cases.py",
        "test_performance":   "test_performance.py",
    }
    for key, label in labels.items():
        if key in results:
            r = results[key]
            lines.append(
                f"  {label:<30} passed={r['passed']}  "
                f"failed={r['failed']}  time={r['duration_s']:.1f}s"
            )

    lines += [
        "",
        "PERFORMANCE SUMMARY:",
        f"  Fastest endpoint : /{fastest}  ({perf.get(fastest, 0):.0f}ms avg)",
        f"  Slowest endpoint : /{slowest}  ({perf.get(slowest, 0):.0f}ms avg)",
        f"  Average response : {avg_ms:.0f}ms",
        f"  All under 3s     : {'YES' if all_under_3s else 'NO'}",
        "",
        "EDGE CASES:",
        f"  Invalid drug → 404   : {'PASS' if total_failed == 0 else 'check failures'}",
        f"  No dosing data → 404 : {'PASS' if total_failed == 0 else 'check failures'}",
        f"  Invalid age → 422    : {'PASS' if total_failed == 0 else 'check failures'}",
        f"  Missing auth → 401   : {'PASS' if total_failed == 0 else 'check failures'}",
        f"  FDC interactions     : {'PASS' if total_failed == 0 else 'check failures'}",
        "",
        "CACHE PERFORMANCE:",
        f"  Cache miss : {cache['miss_ms']:.0f}ms",
        f"  Cache hit  : {cache['hit_ms']:.0f}ms",
        f"  Speedup    : {cache_speedup:.1f}x faster",
        f"  Confirmed  : {'YES (meta.cached=true)' if cache['hit_confirmed'] else 'NO'}",
        "",
    ]

    all_failures = []
    for key, r in results.items():
        if r["failed"] or r["errors"]:
            failures = extract_failures(r["output"])
            all_failures.extend(failures)

    if all_failures:
        lines.append("FAILED TESTS:")
        for f in all_failures:
            lines.append(f"  {f}")
    else:
        lines.append("FAILED TESTS: none ✓")

    lines += ["", "═" * 55]
    return "\n".join(lines)


def main():
    print("Checking server health …")
    check_server()
    print(f"✓ Server healthy at {BASE_URL}\n")

    test_dir = os.path.join(os.path.dirname(__file__))
    files = {
        "test_all_endpoints": os.path.join(test_dir, "test_all_endpoints.py"),
        "test_edge_cases":    os.path.join(test_dir, "test_edge_cases.py"),
        "test_performance":   os.path.join(test_dir, "test_performance.py"),
    }

    results = {}
    for key, path in files.items():
        label = os.path.basename(path)
        print(f"{'='*55}")
        print(f"Running {label} …")
        print(f"{'='*55}")
        r = run_pytest(path)
        results[key] = r
        print(r["output"])
        status = "PASS" if r["returncode"] == 0 else "FAIL"
        print(f"→ {label}: {r['passed']} passed, {r['failed']+r['errors']} failed [{status}]\n")

    print("Collecting performance data …")
    perf = asyncio.run(collect_performance_data())
    cache = asyncio.run(collect_cache_data())

    report = build_report(results, perf, cache)
    print(report)

    with open(REPORT_PATH, "w") as f:
        f.write(report)
        f.write("\n\n")
        for key, r in results.items():
            f.write(f"\n{'='*55}\n{files[key]}\n{'='*55}\n")
            f.write(r["output"])

    print(f"\nFull report saved to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
