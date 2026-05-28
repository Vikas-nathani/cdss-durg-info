#!/usr/bin/env python3
"""
Check how many of the top 500 Indian drugs can be found in drugdb.indian_brand
and retrieve their drug_id_1mg.

Strategy: pull all indian_brand rows once, match in-memory (avoids slow ILIKE scans
on 358k rows). Match priority per drug:
  1. Exact (case-insensitive)
  2. Prefix  (db brand starts with input)
  3. Contains (db brand contains input)
  4. NOT FOUND
"""
import asyncio
import csv
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

CSV_PATH = ROOT / "top_500_india_drugs.csv"
OUT_PATH  = ROOT / "top500_coverage_result.csv"

SOURCE_RANK = {"dailymed": 1, "openfda": 2, "rxnorm": 3, "partial_drugbank": 4}


def best_row(rows):
    """Pick the best row by source priority."""
    return min(rows, key=lambda r: SOURCE_RANK.get(r["match_combination"], 5))


async def main():
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    drugs = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            drugs.append({
                "no":       row["#"],
                "brand":    row["Brand Name (India)"].strip(),
                "salt":     row["Salt Composition"].strip(),
                "category": row["Therapeutic Category"].strip(),
            })

    print(f"Loaded {len(drugs)} drugs from CSV")

    print("Fetching all indian_brand rows into memory...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        all_rows = await conn.fetch(
            """
            SELECT drug_id_1mg, brand_name, rxcui, match_combination
            FROM drugdb.indian_brand
            """
        )
    finally:
        await conn.close()

    print(f"Loaded {len(all_rows)} rows from indian_brand (filtered)")

    # Build lookup structures: lower(brand_name) → [rows]
    from collections import defaultdict
    exact_map: dict[str, list] = defaultdict(list)
    for r in all_rows:
        exact_map[r["brand_name"].lower()].append(r)

    # For prefix/contains we need the full list; keep as list of (lower_name, row)
    lower_pairs = [(r["brand_name"].lower(), r) for r in all_rows]

    def lookup(brand: str):
        key = brand.lower()

        # 1. Exact
        if key in exact_map:
            r = best_row(exact_map[key])
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "exact"

        # 2. Prefix
        prefix_hits = [r for (lb, r) in lower_pairs if lb.startswith(key)]
        if prefix_hits:
            # Prefer shortest match (closest to input), then source rank
            prefix_hits.sort(key=lambda r: (len(r["brand_name"]),
                                             SOURCE_RANK.get(r["match_combination"], 5)))
            r = prefix_hits[0]
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "prefix"

        # 3. Contains
        contain_hits = [r for (lb, r) in lower_pairs if key in lb]
        if contain_hits:
            contain_hits.sort(key=lambda r: (len(r["brand_name"]),
                                              SOURCE_RANK.get(r["match_combination"], 5)))
            r = contain_hits[0]
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "contains"

        return None, None, None, "not_found"

    print("Matching drugs...\n")
    results = []
    found = 0
    not_found = 0

    for i, drug in enumerate(drugs, 1):
        drug_id, matched_name, rxcui, match_type = lookup(drug["brand"])
        status = "FOUND" if match_type != "not_found" else "MISSING"
        if match_type != "not_found":
            found += 1
        else:
            not_found += 1

        results.append({
            "no":             drug["no"],
            "brand_name_csv": drug["brand"],
            "salt_csv":       drug["salt"],
            "category":       drug["category"],
            "status":         status,
            "match_type":     match_type,
            "drug_id_1mg":    drug_id or "",
            "matched_brand":  matched_name or "",
            "rxcui":          str(rxcui) if rxcui else "",
        })

        print(f"  [{i:>3}/{len(drugs)}] {drug['brand']:<40} → {status} ({match_type})"
              + (f"  id={drug_id}" if drug_id else ""))

    # Write results
    fieldnames = ["no", "brand_name_csv", "salt_csv", "category",
                  "status", "match_type", "drug_id_1mg", "matched_brand", "rxcui"]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    total = len(drugs)
    print(f"\n{'='*60}")
    print(f"TOTAL  : {total}")
    print(f"FOUND  : {found}  ({found/total*100:.1f}%)")
    print(f"MISSING: {not_found}  ({not_found/total*100:.1f}%)")

    by_match: dict[str, int] = {}
    for r in results:
        by_match[r["match_type"]] = by_match.get(r["match_type"], 0) + 1
    print("\nBreakdown by match type:")
    for mt, cnt in sorted(by_match.items(), key=lambda x: -x[1]):
        print(f"  {mt:<15} : {cnt}")

    print(f"\nFull results written to: {OUT_PATH}")

    missing = [r for r in results if r["status"] == "MISSING"]
    if missing:
        print(f"\nMissing brands ({len(missing)}):")
        for r in missing:
            print(f"  #{r['no']:>3}  {r['brand_name_csv']}")


if __name__ == "__main__":
    asyncio.run(main())
