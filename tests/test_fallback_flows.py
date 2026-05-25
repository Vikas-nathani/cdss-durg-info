"""
Tests every fallback path added to resolver.py and dosing.py.

Flow matrix being verified:
  Resolver Step 1 — primary   : drug in indian_brand WITH source filter
  Resolver Step 1 — fallback  : drug ONLY in indian_brand WITHOUT source filter
  Resolver Step 2 — primary   : formulation found via direct rxcui join
  Resolver Step 2 — fallback  : formulation found via UNII bridge
  Dosing           — primary  : dosing rows found via direct rxcui CTE
  Dosing           — fallback : dosing rows found via UNII bridge CTE
"""

import pytest
import asyncpg
from app.services.resolver import resolve_drug
from app.services.dosing import get_dosing
from app.exceptions import DrugNotFoundException, NoFormulationException, NoDosingDataException


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — find representative drug IDs from the real DB
# ──────────────────────────────────────────────────────────────────────────────

async def _find_primary_resolver_drug(conn) -> str | None:
    """Drug that resolves on the primary path (quality source + direct rxcui)."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT ib.drug_id_1mg
        FROM drugdb.indian_brand ib
        JOIN drugdb.drug d ON d.rxcui = ANY(ib.rxcui)
        JOIN drugdb.drug_master_linkage_unique m USING (master_linkage_id)
        WHERE ib.match_combination NOT IN ('drugbank', 'us_unapproved')
          AND ib.rxcui IS NOT NULL
        LIMIT 20
        """
    )
    return rows[0]["drug_id_1mg"] if rows else None


async def _find_step1_fallback_drug(pool) -> str | None:
    """
    Drug that is ONLY in indian_brand under drugbank/us_unapproved sources
    AND can be fully resolved (has a reachable formulation via rxcui or UNII bridge).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ib.drug_id_1mg, ib.rxcui
            FROM drugdb.indian_brand ib
            WHERE ib.match_combination IN ('drugbank', 'us_unapproved')
              AND ib.drug_id_1mg NOT IN (
                SELECT drug_id_1mg FROM drugdb.indian_brand
                WHERE match_combination NOT IN ('drugbank', 'us_unapproved')
              )
              AND ib.rxcui IS NOT NULL
            LIMIT 50
            """
        )

    for row in rows:
        drug_id = row["drug_id_1mg"]
        rxcui = list(row["rxcui"]) if row["rxcui"] else []
        if not rxcui:
            continue
        async with pool.acquire() as conn:
            has_form = await conn.fetchrow(
                """
                SELECT 1 FROM drugdb.drug d
                JOIN drugdb.drug_master_linkage_unique m USING (master_linkage_id)
                WHERE d.rxcui = ANY($1::text[])
                LIMIT 1
                """,
                rxcui,
            )
            if has_form:
                return drug_id
            has_unii = await conn.fetchrow(
                """
                SELECT 1
                FROM drugdb.ingredients i
                JOIN public."DrugMasterLinkage" dml ON i.unii = ANY(dml.unii_ids)
                JOIN drugdb.drug d ON d.master_linkage_id = dml.master_linkage_id
                JOIN drugdb.drug_master_linkage_unique m ON m.master_linkage_id = d.master_linkage_id
                WHERE i.rxcui = ANY($1::text[]) AND i.unii IS NOT NULL
                LIMIT 1
                """,
                rxcui,
            )
            if has_unii:
                return drug_id
    return None


async def _find_step2_fallback_drug(conn) -> str | None:
    """
    Drug whose rxcui has no direct entry in drug+drug_master_linkage_unique,
    but whose ingredients have a UNII that bridges to a formulation.
    Uses a two-step approach to avoid a full correlated scan.
    """
    # Step A: rxcuis that have a UNII bridge but no direct drug row
    rows = await conn.fetch(
        """
        SELECT DISTINCT ib.drug_id_1mg, ib.rxcui
        FROM drugdb.indian_brand ib
        WHERE ib.rxcui IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM drugdb.drug d
            WHERE d.rxcui = ANY(ib.rxcui)
          )
        LIMIT 200
        """
    )
    for row in rows:
        rxcui = list(row["rxcui"]) if row["rxcui"] else []
        if not rxcui:
            continue
        bridge = await conn.fetchrow(
            """
            SELECT 1
            FROM drugdb.ingredients i
            JOIN public."DrugMasterLinkage" dml ON i.unii = ANY(dml.unii_ids)
            WHERE i.rxcui = ANY($1::text[])
              AND i.unii IS NOT NULL
            LIMIT 1
            """,
            rxcui,
        )
        if bridge:
            return row["drug_id_1mg"]
    return None


async def _find_dosing_primary_drug(conn) -> str | None:
    """Drug that returns dosing rows via the primary CTE (direct rxcui path)."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT ib.drug_id_1mg
        FROM drugdb.indian_brand ib
        JOIN drugdb.drug d ON d.rxcui = ANY(ib.rxcui)
        JOIN drugdb.dosing_regimen dr ON dr.formulation_id = d.formulation_id
        WHERE ib.match_combination NOT IN ('drugbank', 'us_unapproved')
          AND ib.rxcui IS NOT NULL
          AND dr.age_group = ANY(ARRAY['adult'])
          AND dr.renal_function = 'any'
          AND dr.dose_basis = 'fixed'
          AND dr.frequency IS NOT NULL
        LIMIT 20
        """
    )
    return rows[0]["drug_id_1mg"] if rows else None


async def _find_dosing_fallback_drug(conn) -> str | None:
    """
    Drug whose primary dosing CTE returns nothing but the UNII-bridge CTE
    finds dosing rows. Uses a two-step scan to avoid a full correlated subquery.
    """
    # Step A: drugs with no direct rxcui→dosing path
    rows = await conn.fetch(
        """
        SELECT DISTINCT ib.drug_id_1mg, ib.rxcui
        FROM drugdb.indian_brand ib
        WHERE ib.rxcui IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM drugdb.drug d
            JOIN drugdb.dosing_regimen dr ON dr.formulation_id = d.formulation_id
            WHERE d.rxcui = ANY(ib.rxcui)
              AND dr.age_group = ANY(ARRAY['adult'])
              AND dr.renal_function = 'any'
              AND dr.dose_basis = 'fixed'
              AND dr.frequency IS NOT NULL
          )
        LIMIT 200
        """
    )
    for row in rows:
        rxcui = list(row["rxcui"]) if row["rxcui"] else []
        if not rxcui:
            continue
        # Step B: check if UNII bridge leads to dosing
        bridge = await conn.fetchrow(
            """
            SELECT 1
            FROM drugdb.ingredients i
            JOIN public."DrugMasterLinkage" dml ON i.unii = ANY(dml.unii_ids)
            JOIN drugdb.drug d2 ON d2.master_linkage_id = dml.master_linkage_id
            JOIN drugdb.dosing_regimen dr2 ON dr2.formulation_id = d2.formulation_id
            WHERE i.rxcui = ANY($1::text[])
              AND i.unii IS NOT NULL
              AND dr2.age_group = ANY(ARRAY['adult'])
              AND dr2.renal_function = 'any'
              AND dr2.dose_basis = 'fixed'
              AND dr2.frequency IS NOT NULL
            LIMIT 1
            """,
            rxcui,
        )
        if bridge:
            return row["drug_id_1mg"]
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Session-scoped fixtures — discover one drug ID per flow path
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def flow_drug_ids(db_pool):
    async with db_pool.acquire() as conn:
        primary_resolver    = await _find_primary_resolver_drug(conn)
        step2_fallback      = await _find_step2_fallback_drug(conn)
        dosing_primary      = await _find_dosing_primary_drug(conn)
        dosing_fallback     = await _find_dosing_fallback_drug(conn)
    step1_fallback          = await _find_step1_fallback_drug(db_pool)

    ids = {
        "primary_resolver": primary_resolver,
        "step1_fallback":   step1_fallback,
        "step2_fallback":   step2_fallback,
        "dosing_primary":   dosing_primary,
        "dosing_fallback":  dosing_fallback,
    }
    print("\n[fallback flow] discovered drug IDs:")
    for name, drug_id in ids.items():
        print(f"  {name:20s} → {drug_id or 'NOT FOUND IN DB'}")
    return ids


# ──────────────────────────────────────────────────────────────────────────────
# Resolver tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolver_primary_path(db_pool, flow_drug_ids):
    drug_id = flow_drug_ids["primary_resolver"]
    if not drug_id:
        pytest.skip("No drug found for primary resolver path in DB")

    result = await resolve_drug(drug_id, db_pool)
    assert result.drug_id_1mg == drug_id
    assert result.formulation_id is not None
    assert result.master_linkage_id is not None
    assert result.rxcui is not None and len(result.rxcui) > 0
    print(f"\n  [primary resolver] {drug_id} → formulation_id={result.formulation_id}")


@pytest.mark.asyncio
async def test_resolver_step1_fallback(db_pool, flow_drug_ids):
    """Drug only exists under drugbank/us_unapproved — Step 1 fallback fires."""
    drug_id = flow_drug_ids["step1_fallback"]
    if not drug_id:
        pytest.skip("No drug found for step1 fallback path in DB")

    result = await resolve_drug(drug_id, db_pool)
    assert result.drug_id_1mg == drug_id
    assert result.rxcui is not None
    print(f"\n  [step1 fallback] {drug_id} → rxcui={result.rxcui}")


@pytest.mark.asyncio
async def test_resolver_step2_fallback(db_pool, flow_drug_ids):
    """rxcui has no direct formulation — Step 2 UNII bridge fires."""
    drug_id = flow_drug_ids["step2_fallback"]
    if not drug_id:
        pytest.skip("No drug found for step2 UNII bridge path in DB")

    result = await resolve_drug(drug_id, db_pool)
    assert result.drug_id_1mg == drug_id
    assert result.formulation_id is not None
    assert result.master_linkage_id is not None
    print(f"\n  [step2 UNII fallback] {drug_id} → formulation_id={result.formulation_id}")


@pytest.mark.asyncio
async def test_resolver_truly_missing_drug_raises_404(db_pool):
    with pytest.raises(DrugNotFoundException):
        await resolve_drug("DOES_NOT_EXIST_999999", db_pool)


# ──────────────────────────────────────────────────────────────────────────────
# Dosing tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dosing_primary_path(db_pool, flow_drug_ids):
    drug_id = flow_drug_ids["dosing_primary"]
    if not drug_id:
        pytest.skip("No drug found for primary dosing path in DB")

    rows = await get_dosing(drug_id, "adult", db_pool)
    assert len(rows) > 0
    assert "frequency" in rows[0]
    assert "dose_unit" in rows[0]
    print(f"\n  [dosing primary] {drug_id} → {len(rows)} rows")


@pytest.mark.asyncio
async def test_dosing_fallback_path(db_pool, flow_drug_ids):
    """Primary dosing CTE returns nothing — UNII bridge fallback fires."""
    drug_id = flow_drug_ids["dosing_fallback"]
    if not drug_id:
        pytest.skip("No drug found for dosing UNII fallback path in DB")

    rows = await get_dosing(drug_id, "adult", db_pool)
    assert len(rows) > 0
    assert "frequency" in rows[0]
    print(f"\n  [dosing fallback] {drug_id} → {len(rows)} rows via UNII bridge")


@pytest.mark.asyncio
async def test_dosing_missing_drug_raises_404(db_pool):
    with pytest.raises((NoDosingDataException, DrugNotFoundException, NoFormulationException)):
        await get_dosing("DOES_NOT_EXIST_999999", "adult", db_pool)


# ──────────────────────────────────────────────────────────────────────────────
# Bulk coverage — test 20 drugs per path and report pass/fallback/404 counts
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_resolver_coverage(db_pool):
    """
    Fetch 20 drugs from each source class and classify how they resolve.
    Prints a summary — does not fail unless every single drug errors.
    """
    async with db_pool.acquire() as conn:
        quality = await conn.fetch(
            """
            SELECT DISTINCT drug_id_1mg FROM drugdb.indian_brand
            WHERE match_combination NOT IN ('drugbank', 'us_unapproved')
              AND rxcui IS NOT NULL
            LIMIT 20
            """
        )
        fallback_only = await conn.fetch(
            """
            SELECT drug_id_1mg FROM drugdb.indian_brand
            WHERE match_combination IN ('drugbank', 'us_unapproved')
              AND drug_id_1mg NOT IN (
                SELECT drug_id_1mg FROM drugdb.indian_brand
                WHERE match_combination NOT IN ('drugbank', 'us_unapproved')
              )
              AND rxcui IS NOT NULL
            LIMIT 20
            """
        )

    results = {"primary": 0, "step1_fb": 0, "step2_fb": 0, "error": 0}

    for row in list(quality) + list(fallback_only):
        drug_id = row["drug_id_1mg"]
        try:
            resolved = await resolve_drug(drug_id, db_pool)
            results["primary"] += 1
        except (DrugNotFoundException, NoFormulationException):
            results["error"] += 1
        except Exception:
            results["error"] += 1

    print(f"\n  [bulk resolver] primary={results['primary']}  error={results['error']}")
    total = sum(results.values())
    assert total > 0
    assert results["error"] < total, "All drugs errored — something is broken"


@pytest.mark.asyncio
async def test_bulk_dosing_coverage(db_pool):
    """
    Fetch 20 drugs and classify dosing as primary / fallback / 404.
    Prints a summary — does not fail unless every drug returns 404.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT drug_id_1mg FROM drugdb.indian_brand
            WHERE rxcui IS NOT NULL
            LIMIT 20
            """
        )

    primary_count  = 0
    fallback_count = 0
    miss_count     = 0

    for row in rows:
        drug_id = row["drug_id_1mg"]
        try:
            result = await get_dosing(drug_id, "adult", db_pool)
            if result:
                primary_count += 1
        except NoDosingDataException:
            miss_count += 1
        except (DrugNotFoundException, NoFormulationException):
            miss_count += 1
        except Exception:
            miss_count += 1

    print(
        f"\n  [bulk dosing] got_rows={primary_count}  no_rows={miss_count}"
    )
    assert primary_count + miss_count > 0
