#!/usr/bin/env python3
"""
Check how many of the top 500 drugs return real data from the CDSS APIs.
Tests each FOUND drug against key endpoints and reports coverage per endpoint.
"""
import asyncio
import csv
import os
import sys
from pathlib import Path
from collections import defaultdict

import httpx

ROOT = Path(__file__).resolve().parent.parent

BASE_URL = "http://localhost:8002"
API_KEY  = "dev"
HEADERS  = {"X-API-Key": API_KEY}

CSV_PATH = ROOT / "top500_coverage_result.csv"

ENDPOINTS = [
    ("indications",        "/api/v1/drug/{id}/indications"),
    ("contraindications",  "/api/v1/drug/{id}/contraindications"),
    ("warnings",           "/api/v1/drug/{id}/warnings"),
    ("mechanism_of_action","/api/v1/drug/{id}/mechanism-of-action"),
    ("adverse_reactions",  "/api/v1/drug/{id}/adverse-reactions"),
    ("interactions",       "/api/v1/drug/{id}/interactions"),
    ("dosing_adult",       "/api/v1/drug/{id}/dosing-regimen?age=30"),
    ("dosing_pediatric",   "/api/v1/drug/{id}/dosing-regimen?age=8"),
]

CONCURRENCY = 20


async def check_drug(client: httpx.AsyncClient, drug_id: str, brand: str, sem: asyncio.Semaphore):
    results = {}
    for ep_name, ep_path in ENDPOINTS:
        url = BASE_URL + ep_path.replace("{id}", drug_id)
        async with sem:
            try:
                r = await client.get(url, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    body = r.json()
                    data = body.get("data")
                    # Count as "has_data" only if data is non-empty
                    if data is None or data == {} or data == [] or data == "":
                        results[ep_name] = "no_data"
                    else:
                        results[ep_name] = "ok"
                elif r.status_code == 404:
                    results[ep_name] = "404"
                else:
                    results[ep_name] = f"err_{r.status_code}"
            except Exception as e:
                results[ep_name] = "timeout"
    return drug_id, brand, results


async def main():
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found", file=sys.stderr)
        sys.exit(1)

    drugs = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] == "FOUND" and row["drug_id_1mg"]:
                drugs.append((row["drug_id_1mg"], row["brand_name_csv"]))

    # Deduplicate by drug_id (same ID may appear under multiple brand names)
    seen = set()
    unique_drugs = []
    for did, brand in drugs:
        if did not in seen:
            seen.add(did)
            unique_drugs.append((did, brand))

    total = len(unique_drugs)
    print(f"Testing {total} unique drug IDs across {len(ENDPOINTS)} endpoints...\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    ep_counts: dict[str, dict[str, int]] = {ep: defaultdict(int) for ep, _ in ENDPOINTS}

    async with httpx.AsyncClient() as client:
        tasks = [check_drug(client, did, brand, sem) for did, brand in unique_drugs]
        done = 0
        for coro in asyncio.as_completed(tasks):
            drug_id, brand, results = await coro
            done += 1
            for ep_name, status in results.items():
                ep_counts[ep_name][status] += 1
            if done % 50 == 0 or done == total:
                print(f"  [{done:>3}/{total}] done", flush=True)

    print(f"\n{'='*65}")
    print(f"{'ENDPOINT':<25}  {'OK':>5}  {'NO_DATA':>7}  {'404':>5}  {'ERR':>5}  {'COVERAGE':>8}")
    print(f"{'-'*65}")
    for ep_name, _ in ENDPOINTS:
        c = ep_counts[ep_name]
        ok      = c.get("ok", 0)
        no_data = c.get("no_data", 0)
        not_fnd = c.get("404", 0)
        err     = sum(v for k, v in c.items() if k not in ("ok", "no_data", "404"))
        pct     = ok / total * 100 if total else 0
        print(f"  {ep_name:<23}  {ok:>5}  {no_data:>7}  {not_fnd:>5}  {err:>5}  {pct:>7.1f}%")
    print(f"{'='*65}")
    print(f"\nTotal unique drug IDs tested: {total}")


if __name__ == "__main__":
    asyncio.run(main())
