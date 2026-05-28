# CDSS Drug Info — All Endpoint Queries Reference

Every API request flows through **two phases**:

1. **Resolution phase** — `resolve_drug()` converts a `drug_id_1mg` → `formulation_id` + `master_linkage_id` + label JSONB. All endpoints share this phase.
2. **Endpoint-specific phase** — the resolved IDs are used to fetch or extract the actual data (JSONB traversal, SQL query, or both).

---

## Table of Contents

1. [Core Resolver — shared by ALL endpoints](#1-core-resolver--shared-by-all-endpoints)
2. [Label Endpoints (15 endpoints — JSONB extraction)](#2-label-endpoints-15-endpoints)
3. [Dosing Regimen](#3-dosing-regimen-endpoint)
4. [Drug Interactions](#4-drug-interactions-endpoint)
5. [Check Drug-Drug Interaction](#5-check-drug-drug-interaction-endpoint)
6. [Drug Classes](#6-drug-classes-endpoint)
7. [Database Tables Reference](#7-database-tables-reference)
8. [Caching Keys Reference](#8-caching-keys-reference)

---

## 1. Core Resolver — shared by ALL endpoints

**File:** `app/services/resolver.py`

Every endpoint calls `resolve_drug(drug_id_1mg, pool)` before doing anything else.
It runs up to **4 SQL queries** across 3 steps.

```
drug_id_1mg (string from URL)
       │
       ▼
  ┌─────────────────────────┐
  │  STEP 1 — Brand lookup  │
  └─────────────────────────┘
       │  Primary query → indian_brand WHERE match_combination NOT IN (...)
       │  Fallback query → indian_brand (no filter)
       │
       ▼  rxcui[], brand_name, salt_composition
  ┌──────────────────────────────────┐
  │  STEP 2 — Formulation resolution │
  └──────────────────────────────────┘
       │  Primary query → drug JOIN drug_master_linkage_unique WHERE rxcui = ANY(...)
       │  Fallback query → UNII bridge (4-CTE query, see below)
       │
       ▼  formulation_id, master_linkage_id, combined_clean_jsonb
  ┌──────────────────────────────────┐
  │  STEP 3 — Return ResolvedDrug    │
  └──────────────────────────────────┘
       │  No SQL — just parses the JSONB from step 2
       ▼
  ResolvedDrug object passed to each endpoint handler
```

---

### Step 1 — Primary query (resolver.py:25–34)

**Purpose:** Look up the brand's RxNorm IDs, preferring high-quality source matches.

```sql
SELECT ib.rxcui, ib.salt_composition, ib.brand_name
FROM drugdb.indian_brand ib
WHERE ib.drug_id_1mg = $1
  AND ib.match_combination NOT IN ('drugbank', 'us_unapproved')
LIMIT 1
```

**Parameters:** `$1` = `drug_id_1mg`
**Returns:** `rxcui[]`, `salt_composition`, `brand_name`

---

### Step 1 — Fallback query (resolver.py:37–45)

**Trigger:** Primary returns no row — drug was only matched via drugbank or us_unapproved sources.

```sql
SELECT ib.rxcui, ib.salt_composition, ib.brand_name
FROM drugdb.indian_brand ib
WHERE ib.drug_id_1mg = $1
LIMIT 1
```

**Parameters:** `$1` = `drug_id_1mg`
**On failure:** Raises `DrugNotFoundException` → HTTP 404

---

### Step 2 — Primary query (resolver.py:67–77)

**Purpose:** Find the formulation and its merged label JSONB using RxCUI.

```sql
SELECT d.formulation_id, d.master_linkage_id, d.generic_name,
       m.combined_clean_jsonb, m.generic_name AS ml_generic_name
FROM drugdb.drug d
JOIN drugdb.drug_master_linkage_unique m USING (master_linkage_id)
WHERE d.rxcui = ANY($1::text[])
LIMIT 1
```

**Parameters:** `$1` = `rxcui[]` array from Step 1
**Returns:** `formulation_id`, `master_linkage_id`, `combined_clean_jsonb`, `generic_name`

---

### Step 2 — Fallback query (UNII bridge) (resolver.py:80–107)

**Trigger:** Primary returns no row — rxcui exists in `indian_brand` but has no matching row in `drug`.
This uses the UNII identifier to bridge across to `DrugMasterLinkage`.

**Flow:**
```
rxcui[] from Step 1
    │
    ▼
resolvable_rxcuis CTE
    Joins ingredients → DrugMasterLinkage via unii
    Only keeps rxcuis that have exactly one master_linkage entry
    │
    ▼
all_pass_check CTE
    Ensures ALL rxcuis in the array were resolved (full match required)
    │
    ▼
linkage CTE
    Extracts the master_linkage_id(s) — only if all_pass_check passed
    │
    ▼
Final SELECT
    Joins drug + drug_master_linkage_unique via master_linkage_id
```

```sql
WITH resolvable_rxcuis AS (
  SELECT DISTINCT i.rxcui, dml.master_linkage_id
  FROM drugdb.ingredients i
  JOIN public."DrugMasterLinkage" dml ON dml.unii_ids @> ARRAY[i.unii::text]
  WHERE i.rxcui = ANY($1::text[])
    AND i.unii IS NOT NULL
    AND array_length(dml.rxcui_ids, 1) = 1
),
all_pass_check AS (
  SELECT 1
  WHERE (SELECT COUNT(DISTINCT rxcui) FROM resolvable_rxcuis) = array_length($1::text[], 1)
),
linkage AS (
  SELECT DISTINCT master_linkage_id
  FROM resolvable_rxcuis
  WHERE EXISTS (SELECT 1 FROM all_pass_check)
)
SELECT d.formulation_id, d.master_linkage_id, d.generic_name,
       m.combined_clean_jsonb, m.generic_name AS ml_generic_name
FROM drugdb.drug d
JOIN drugdb.drug_master_linkage_unique m USING (master_linkage_id)
JOIN linkage l ON d.master_linkage_id = l.master_linkage_id
LIMIT 1
```

**Parameters:** `$1` = `rxcui[]` array
**On failure:** Raises `NoFormulationException` → HTTP 404

---

## 2. Label Endpoints (15 endpoints)

**File:** `app/routers/label.py`, `app/services/label.py`

All 15 label endpoints share the same flow after `resolve_drug()`:

```
ResolvedDrug.combined_clean_jsonb  (in-memory dict, already fetched by resolver)
       │
       ▼
  ┌──────────────────────────────────────────────┐
  │  Check Redis cache                           │
  │  key = "label:{endpoint_name}:{master_id}"  │
  └──────────────────────────────────────────────┘
       │  HIT  ──────────────────────────────────► return cached JSON
       │  MISS
       ▼
  ┌──────────────────────────────────────────────┐
  │  Extract from JSONB (no SQL — in-memory)     │
  │  1. Try openFDA path in combined_clean_jsonb │
  │  2. Fallback: try DailyMed path              │
  └──────────────────────────────────────────────┘
       │
       ▼
  Store in Redis (TTL = 24h)
       │
       ▼
  Return response
```

**No additional SQL is run for label endpoints.** The data was loaded in Step 2 of the resolver as `combined_clean_jsonb`.

---

### JSONB structure inside `combined_clean_jsonb`

```
combined_clean_jsonb
├── openfda
│   ├── safety
│   │   ├── contraindications        { text, table, subsections }
│   │   ├── warnings_and_cautions    { text, table, subsections }
│   │   ├── warnings                 { text, table, subsections }
│   │   └── boxed_warning            { text, table, subsections }
│   ├── clinical
│   │   ├── mechanism_of_action      { text, table, subsections }
│   │   └── microbiology             { text, table, subsections }
│   ├── drug_info
│   │   └── generic_name             string
│   ├── patient_info
│   │   └── information_for_patients { text, table, subsections }
│   ├── adverse_events
│   │   └── adverse_reactions        { text, table, subsections }
│   ├── labeling_content
│   │   ├── drug_description         string
│   │   └── indications_and_usage    { text, table, subsections }
│   └── population_specific
│       ├── pediatric_use            { text, table, subsections }
│       ├── geriatric_use            { text, table, subsections }
│       ├── use_in_pregnancy         { text, table, subsections }
│       └── use_in_specific_populations { text, table, subsections }
├── dailymed
│   ├── safety
│   │   ├── contraindications        { content, subsections }
│   │   ├── warnings_and_precautions { content, subsections }
│   │   ├── warnings                 { content, subsections }
│   │   └── boxed_warning            { content, subsections }
│   ├── drug_info
│   │   └── products[]               [ { generic_name, dosage_form, active_ingredients[], inactive_ingredients[], ... } ]
│   ├── patient_info
│   │   └── information_for_patients { content, subsections }
│   ├── adverse_events
│   │   └── adverse_reactions        { content, subsections }
│   ├── labeling_content
│   │   └── indications_and_usage    { content, subsections }
│   └── population_specific
│       ├── pediatric_use            { content, subsections }
│       ├── geriatric_use            { content, subsections }
│       └── teratogenic_effects      { content, subsections }
└── drugbank[]
    └── [each entry]
        └── drug_interactions
            └── food_interactions    string | list
```

---

### Per-endpoint JSONB extraction paths

| Endpoint | Route | Primary path (openFDA) | Fallback path (DailyMed) |
|---|---|---|---|
| Contraindications | `GET /drug/{id}/contraindications` | `openfda.safety.contraindications` | `dailymed.safety.contraindications` |
| Warnings | `GET /drug/{id}/warnings` | `openfda.safety.warnings_and_cautions` → `warnings` → `boxed_warning` | `dailymed.safety.warnings_and_precautions` → `warnings` → `boxed_warning` |
| Mechanism of Action | `GET /drug/{id}/mechanism-of-action` | `openfda.clinical.mechanism_of_action` | *(openFDA only, no fallback)* |
| Microbiology | `GET /drug/{id}/microbiology` | `openfda.clinical.microbiology` | *(openFDA only, no fallback)* |
| Generic Name | `GET /drug/{id}/generic-name` | `openfda.drug_info.generic_name` | `dailymed.drug_info.products[0].generic_name` |
| Patient Info | `GET /drug/{id}/patient-info` | `openfda.patient_info.information_for_patients` | `dailymed.patient_info.information_for_patients` |
| Adverse Reactions | `GET /drug/{id}/adverse-reactions` | `openfda.adverse_events.adverse_reactions` | `dailymed.adverse_events.adverse_reactions` |
| Drug Description | `GET /drug/{id}/drug-description` | `openfda.labeling_content.drug_description` | *(openFDA only, no fallback)* |
| Indications | `GET /drug/{id}/indications` | `openfda.labeling_content.indications_and_usage` | `dailymed.labeling_content.indications_and_usage` |
| Population Info | `GET /drug/{id}/population-info?age=N` | age<18 → `openfda.population_specific.pediatric_use`; age≥65 → `openfda.population_specific.geriatric_use` | DailyMed equivalent path |
| Pregnancy Use | `GET /drug/{id}/pregnancy-use` | `openfda.population_specific.use_in_pregnancy` | `dailymed.population_specific.teratogenic_effects` |
| Specific Populations | `GET /drug/{id}/specific-populations` | `openfda.population_specific.use_in_specific_populations` | *(openFDA only, no fallback)* |
| Products | `GET /drug/{id}/products` | *(DailyMed only)* | `dailymed.drug_info.products[]` |
| Food Interactions | `GET /drug/{id}/food-interactions` | *(DrugBank only)* | `drugbank[].drug_interactions.food_interactions` |
| Ingredients | `GET /drug/{id}/ingredients` | *(DailyMed only)* | `dailymed.drug_info.products[].active_ingredients` + `inactive_ingredients` |

**Population Info age routing:**

```
age parameter
    │
    ├── age < 18  ──► category = "pediatric"
    │                  primary: openfda.population_specific.pediatric_use
    │                  fallback: dailymed.population_specific.pediatric_use
    │
    ├── age ≥ 65  ──► category = "geriatric"
    │                  primary: openfda.population_specific.geriatric_use
    │                  fallback: dailymed.population_specific.geriatric_use
    │
    └── 18 ≤ age < 65 ──► category = "adult"
                           returns empty (no adult-specific section in labels)
```

---

## 3. Dosing Regimen Endpoint

**Route:** `GET /drug/{drug_id_1mg}/dosing-regimen?age={float}`
**File:** `app/services/dosing.py`, `app/routers/dosing.py`

### Age → Age Group mapping (router level)

```
age (float, years)
    │
    ├── age < 0.083 (< 1 month)   ──► "neonate"
    ├── 0.083 ≤ age < 2            ──► "infant"
    ├── 2 ≤ age < 12               ──► "pediatric"
    ├── 12 ≤ age < 18              ──► "adolescent"
    ├── 18 ≤ age < 65              ──► "adult"
    └── age ≥ 65                   ──► "geriatric"
```

Each `age_group` expands to a list of labels searched in the database:

| age_group | Searches for |
|---|---|
| `neonate` | `["neonate"]` |
| `infant` | `["infant"]` |
| `pediatric` | `["pediatric", "children"]` |
| `adolescent` | `["adolescent"]` |
| `adult` | `["adult"]` |
| `geriatric` | `["geriatric"]` |

---

### Primary query — 5-CTE query (dosing.py:22–130)

**Flow:**

```
drug_id_1mg + age_group_list
       │
       ▼
salt_ingredients CTE
    Look up rxcui from indian_brand (quality sources only)
       │
       ▼
candidate_formulations CTE
    Join drug table on rxcui, count how many dosing rows each formulation has
       │
       ▼
best_formulation CTE
    Pick ONE formulation per rxcui, ranked by data source quality:
    dailymed (1) > openfda (2) > drugbank (3) > rxnorm (4)
    Tie-break: most dosing rows first
       │
       ▼
ranked CTE
    Fetch dosing_regimen rows for best_formulation + age_group
    Filter: renal='any', hepatic='any', pregnancy='any', dose_basis='fixed'
    Deduplicate: same (frequency, route, dose_value, dose_unit, indication)
    Keep row with most complete data (indication+notes > indication > notes > neither)
       │
       ▼
Final SELECT
    Join back to indian_brand for brand_name, salt_composition
    Join ingredients for generic_name (aggregated as "Drug A / Drug B")
    Return only rn=1 rows, ordered by frequency + dose_value
```

```sql
WITH salt_ingredients AS (
  SELECT ib.salt_composition, ib.rxcui
  FROM drugdb.indian_brand ib
  WHERE ib.drug_id_1mg = $1
    AND ib.match_combination NOT IN ('drugbank', 'us_unapproved')
  LIMIT 1
),
candidate_formulations AS (
  SELECT
    d.formulation_id,
    d.rxcui,
    d.generic_name,
    d.has_dailymed,
    d.has_openfda,
    d.has_drugbank,
    d.has_rxnorm,
    COUNT(dr.id) AS dosing_row_count
  FROM drugdb.drug d
  JOIN salt_ingredients si ON d.rxcui = ANY(si.rxcui)
  LEFT JOIN drugdb.dosing_regimen dr ON dr.formulation_id = d.formulation_id
  GROUP BY
    d.formulation_id, d.rxcui, d.generic_name,
    d.has_dailymed, d.has_openfda, d.has_drugbank, d.has_rxnorm
),
best_formulation AS (
  SELECT DISTINCT ON (rxcui)
    formulation_id,
    rxcui
  FROM candidate_formulations
  ORDER BY
    rxcui,
    CASE
      WHEN has_dailymed = true THEN 1
      WHEN has_openfda  = true THEN 2
      WHEN has_drugbank = true THEN 3
      WHEN has_rxnorm   = true THEN 4
      ELSE 5
    END ASC,
    dosing_row_count DESC,
    formulation_id ASC
),
ranked AS (
  SELECT
    dr.frequency,
    dr.route,
    dr.dose_amount,
    dr.dose_value,
    dr.dose_unit,
    dr.duration,
    dr.indication,
    dr.administration_notes,
    ROW_NUMBER() OVER (
      PARTITION BY
        dr.frequency,
        dr.route,
        dr.dose_value,
        dr.dose_unit,
        LOWER(COALESCE(dr.indication, ''))
      ORDER BY
        CASE
          WHEN dr.indication IS NOT NULL
           AND dr.administration_notes IS NOT NULL THEN 1
          WHEN dr.indication IS NOT NULL            THEN 2
          WHEN dr.administration_notes IS NOT NULL  THEN 3
          ELSE 4
        END ASC,
        dr.id ASC
    ) AS rn
  FROM best_formulation bf
  JOIN drugdb.dosing_regimen dr ON dr.formulation_id = bf.formulation_id
  WHERE dr.age_group        = ANY($2::text[])
    AND dr.renal_function   = 'any'
    AND dr.hepatic_function = 'any'
    AND dr.pregnancy_status = 'any'
    AND dr.dose_basis       = 'fixed'
    AND dr.frequency        IS NOT NULL
    AND UPPER(COALESCE(dr.dose_amount, '')) != 'CONTRAINDICATED'
    AND (dr.administration_notes NOT ILIKE '%pediatric%'
         OR dr.administration_notes IS NULL)
)
SELECT
  ib.brand_name,
  ib.salt_composition,
  (
    SELECT STRING_AGG(i.name, ' / ' ORDER BY i.name)
    FROM drugdb.drug_ingredient_mapping dim
    JOIN drugdb.ingredients i ON i.id = dim.ingredient_id
    WHERE dim.formulation_id = bf.formulation_id
  ) AS generic_name,
  r.frequency,
  r.route,
  r.dose_amount,
  r.dose_unit,
  r.duration,
  LOWER(r.indication) AS indication,
  r.administration_notes AS instructions
FROM ranked r
CROSS JOIN best_formulation bf
JOIN drugdb.indian_brand ib
  ON ib.drug_id_1mg = $1
  AND ib.match_combination NOT IN ('drugbank', 'us_unapproved')
WHERE r.rn = 1
ORDER BY r.frequency, r.dose_value
```

**Parameters:** `$1` = `drug_id_1mg`, `$2` = `age_group_list[]`

---

### Fallback query — UNII bridge dosing (dosing.py:135–264)

**Trigger:** Primary query returns no rows. The `match_combination` filter in `salt_ingredients` excluded this drug's indian_brand row.

**Key difference from primary:**
- `salt_ingredients` removes the `match_combination` filter
- `candidate_formulations` finds formulations via `master_linkage_id` through UNII bridge instead of directly via `rxcui`
- `indian_brand` join at the end also removes the `match_combination` filter

**Flow:**
```
drug_id_1mg + age_group_list
       │
       ▼
salt_ingredients CTE
    Look up rxcui from indian_brand (NO quality filter — any source)
       │
       ▼
resolvable_rxcuis CTE
    unnest rxcui array → join ingredients on rxcui → join DrugMasterLinkage on unii
    Only include rxcuis where the master linkage has exactly 1 rxcui (unambiguous)
       │
       ▼
all_pass_check CTE
    All rxcuis must resolve — partial matches are rejected
       │
       ▼
linkage CTE
    master_linkage_id(s) only if all_pass_check passed
       │
       ▼
candidate_formulations CTE
    Join drug on master_linkage_id (not rxcui directly)
    Count dosing rows as before
       │
       ▼
best_formulation → ranked → final SELECT
    Same logic as primary query
```

```sql
WITH salt_ingredients AS (
  SELECT ib.salt_composition, ib.rxcui
  FROM drugdb.indian_brand ib
  WHERE ib.drug_id_1mg = $1
  LIMIT 1
),
resolvable_rxcuis AS (
  SELECT DISTINCT r.rxcui, dml.master_linkage_id
  FROM salt_ingredients si
  CROSS JOIN LATERAL unnest(si.rxcui) AS r(rxcui)
  JOIN drugdb.ingredients i ON i.rxcui = r.rxcui
  JOIN public."DrugMasterLinkage" dml ON dml.unii_ids @> ARRAY[i.unii::text]
  WHERE i.unii IS NOT NULL
    AND array_length(dml.rxcui_ids, 1) = 1
),
all_pass_check AS (
  SELECT 1
  FROM salt_ingredients si
  WHERE (SELECT COUNT(DISTINCT rxcui) FROM resolvable_rxcuis) = array_length(si.rxcui, 1)
),
linkage AS (
  SELECT DISTINCT master_linkage_id
  FROM resolvable_rxcuis
  WHERE EXISTS (SELECT 1 FROM all_pass_check)
),
candidate_formulations AS (
  SELECT
    d.formulation_id, d.rxcui, d.generic_name,
    d.has_dailymed, d.has_openfda, d.has_drugbank, d.has_rxnorm,
    COUNT(dr.id) AS dosing_row_count
  FROM drugdb.drug d
  JOIN linkage l ON d.master_linkage_id = l.master_linkage_id
  LEFT JOIN drugdb.dosing_regimen dr ON dr.formulation_id = d.formulation_id
  GROUP BY
    d.formulation_id, d.rxcui, d.generic_name,
    d.has_dailymed, d.has_openfda, d.has_drugbank, d.has_rxnorm
),
best_formulation AS (
  SELECT DISTINCT ON (rxcui) formulation_id, rxcui
  FROM candidate_formulations
  ORDER BY rxcui,
    CASE WHEN has_dailymed=true THEN 1 WHEN has_openfda=true THEN 2
         WHEN has_drugbank=true THEN 3 WHEN has_rxnorm=true THEN 4 ELSE 5 END ASC,
    dosing_row_count DESC, formulation_id ASC
),
ranked AS (
  /* identical WHERE and WINDOW clause as primary query */
  SELECT dr.frequency, dr.route, dr.dose_amount, dr.dose_value, dr.dose_unit,
         dr.duration, dr.indication, dr.administration_notes,
    ROW_NUMBER() OVER (
      PARTITION BY dr.frequency, dr.route, dr.dose_value, dr.dose_unit,
                   LOWER(COALESCE(dr.indication,''))
      ORDER BY
        CASE WHEN dr.indication IS NOT NULL AND dr.administration_notes IS NOT NULL THEN 1
             WHEN dr.indication IS NOT NULL THEN 2
             WHEN dr.administration_notes IS NOT NULL THEN 3
             ELSE 4 END ASC, dr.id ASC
    ) AS rn
  FROM best_formulation bf
  JOIN drugdb.dosing_regimen dr ON dr.formulation_id = bf.formulation_id
  WHERE dr.age_group = ANY($2::text[])
    AND dr.renal_function='any' AND dr.hepatic_function='any'
    AND dr.pregnancy_status='any' AND dr.dose_basis='fixed'
    AND dr.frequency IS NOT NULL
    AND UPPER(COALESCE(dr.dose_amount,'')) != 'CONTRAINDICATED'
    AND (dr.administration_notes NOT ILIKE '%pediatric%' OR dr.administration_notes IS NULL)
)
SELECT
  ib.brand_name,
  ib.salt_composition,
  (SELECT STRING_AGG(i.name, ' / ' ORDER BY i.name)
   FROM drugdb.drug_ingredient_mapping dim
   JOIN drugdb.ingredients i ON i.id = dim.ingredient_id
   WHERE dim.formulation_id = bf.formulation_id) AS generic_name,
  r.frequency, r.route, r.dose_amount, r.dose_unit, r.duration,
  LOWER(r.indication) AS indication,
  r.administration_notes AS instructions
FROM ranked r
CROSS JOIN best_formulation bf
JOIN LATERAL (
  SELECT brand_name, salt_composition
  FROM drugdb.indian_brand
  WHERE drug_id_1mg = $1
  LIMIT 1
) ib ON true
WHERE r.rn = 1
ORDER BY r.frequency, r.dose_value
```

**Parameters:** `$1` = `drug_id_1mg`, `$2` = `age_group_list[]`
**On failure:** Raises `NoDosingDataException` → HTTP 404

---

## 4. Drug Interactions Endpoint

**Route:** `GET /drug/{drug_id_1mg}/interactions`
**File:** `app/services/interactions.py` (lines 86–128)

### Flow

```
drug_id_1mg
       │
       ▼
resolve_drug()  ──►  formulation_id
       │
       ▼
Check Redis cache
key = "interactions:{formulation_id}"
       │  MISS
       ▼
SQL: 4-table join to find all known interactions for this drug's ingredients
       │
       ▼
Return list of { our_ingredient, interacting_ingredient, severity, mechanism }
sorted by severity (major > moderate > minor)
```

### Query (interactions.py:88–117)

**Purpose:** Find all drugs that interact with any ingredient of the given drug.
Uses the `ingredient_interactions` table which stores ingredient-to-ingredient interaction data.

```sql
SELECT our_ingredient, interacting_ingredient, severity, mechanism
FROM (
    SELECT DISTINCT
        i_ours.name   AS our_ingredient,
        i_theirs.name AS interacting_ingredient,
        ii.severity,
        ii.mechanism
    FROM drugdb.drug_ingredient_mapping di_ours
    JOIN drugdb.ingredients i_ours
        ON i_ours.id = di_ours.ingredient_id
    JOIN drugdb.ingredient_interactions ii
        ON ii.id = di_ours.ingredient_id
    JOIN drugdb.ingredients i_theirs
        ON i_theirs.id = ii.reacting_id
    WHERE di_ours.formulation_id = $1
) t
ORDER BY
    CASE severity
        WHEN 'major' THEN 1
        WHEN 'moderate' THEN 2
        WHEN 'minor' THEN 3
        ELSE 4
    END,
    interacting_ingredient
LIMIT 100
```

**Parameters:** `$1` = `formulation_id`
**No fallback query** — returns empty list if no interactions found.

---

## 5. Check Drug-Drug Interaction Endpoint

**Route:** `GET /drug/{drug_id_1mg}/check-interaction/{other_drug_id}`
**File:** `app/services/interactions.py` (lines 10–83)

### Flow

```
drug_id_1mg + other_drug_id
       │
       ▼
resolve_drug() called TWICE (in parallel) → formulation_id_1, formulation_id_2
       │
       ▼
SQL: bidirectional join — checks A→B interactions AND B→A interactions
       │
       ▼
Deduplicate via UNION
Sort by severity
       │
       ▼
Return { interactions[], severity_counts{major,moderate,minor}, has_interaction, highest_severity }
```

### Query (interactions.py:16–58)

**Purpose:** Find interactions SPECIFICALLY between two named drugs (not all possible interactions).
Checks both directions because `ingredient_interactions` may store only one direction.

```sql
SELECT * FROM (
    -- Direction 1: ingredients of drug1 → ingredients of drug2
    SELECT DISTINCT
        i_a.name  AS drug1_ingredient,
        i_b.name  AS drug2_ingredient,
        ii.severity,
        ii.mechanism
    FROM drugdb.drug_ingredient_mapping dim_a
    JOIN drugdb.ingredients i_a        ON i_a.id = dim_a.ingredient_id
    JOIN drugdb.ingredient_interactions ii ON ii.id = dim_a.ingredient_id
    JOIN drugdb.ingredients i_b        ON i_b.id = ii.reacting_id
    JOIN drugdb.drug_ingredient_mapping dim_b ON dim_b.ingredient_id = ii.reacting_id
    WHERE dim_a.formulation_id = $1
      AND dim_b.formulation_id = $2

    UNION

    -- Direction 2: ingredients of drug2 → ingredients of drug1
    SELECT DISTINCT
        i_a.name  AS drug1_ingredient,
        i_b.name  AS drug2_ingredient,
        ii.severity,
        ii.mechanism
    FROM drugdb.drug_ingredient_mapping dim_b
    JOIN drugdb.ingredients i_b        ON i_b.id = dim_b.ingredient_id
    JOIN drugdb.ingredient_interactions ii ON ii.id = dim_b.ingredient_id
    JOIN drugdb.ingredients i_a        ON i_a.id = ii.reacting_id
    JOIN drugdb.drug_ingredient_mapping dim_a ON dim_a.ingredient_id = ii.reacting_id
    WHERE dim_b.formulation_id = $2
      AND dim_a.formulation_id = $1
) t
ORDER BY
    CASE severity
        WHEN 'major'    THEN 1
        WHEN 'moderate' THEN 2
        WHEN 'minor'    THEN 3
        ELSE 4
    END,
    drug2_ingredient
```

**Parameters:** `$1` = `formulation_id_1`, `$2` = `formulation_id_2`
**No fallback query** — returns `has_interaction: false` if no rows found.

---

## 6. Drug Classes Endpoint

**Route:** `GET /drug/{drug_id_1mg}/drug-classes`
**File:** `app/services/drug_classes.py`

### Flow

```
drug_id_1mg
       │
       ▼
resolve_drug()  ──►  formulation_id
       │
       ▼
Check Redis cache
key = "drug_classes:{formulation_id}"
       │  MISS
       ▼
SQL: single SELECT on drug table
       │
       ▼
Return { pharmacologic_class[], therapeutic_class[], mechanism_class[] }
```

### Query (drug_classes.py:9–19)

The simplest query in the entire system — a single lookup on `drugdb.drug`.

```sql
SELECT
    pharmacologic_class,
    therapeutic_class,
    mechanism_class
FROM drugdb.drug
WHERE formulation_id = $1
```

**Parameters:** `$1` = `formulation_id`
**No fallback query** — returns empty arrays if no row found (not a 404).

---

## 7. Database Tables Reference

| Table | Schema | Purpose | Key Columns |
|---|---|---|---|
| `indian_brand` | `drugdb` | Maps 1mg drug IDs to RxNorm | `drug_id_1mg`, `rxcui[]`, `brand_name`, `salt_composition`, `match_combination` |
| `drug` | `drugdb` | One row per RxNorm formulation | `formulation_id`, `rxcui`, `master_linkage_id`, `generic_name`, `has_dailymed`, `has_openfda`, `has_drugbank`, `has_rxnorm`, `pharmacologic_class[]`, `therapeutic_class[]`, `mechanism_class[]` |
| `drug_master_linkage_unique` | `drugdb` | Deduplicated merged label JSONB | `master_linkage_id`, `generic_name`, `combined_clean_jsonb` |
| `DrugMasterLinkage` | `public` | Raw source records (all sources) | `master_linkage_id`, `rxcui_ids[]`, `unii_ids[]` |
| `dosing_regimen` | `drugdb` | Dosing rows by formulation + age | `id`, `formulation_id`, `age_group`, `frequency`, `route`, `dose_amount`, `dose_value`, `dose_unit`, `duration`, `indication`, `administration_notes`, `renal_function`, `hepatic_function`, `pregnancy_status`, `dose_basis` |
| `ingredients` | `drugdb` | Active/inactive ingredient master | `id`, `name`, `rxcui`, `unii`, `drugbank_id` |
| `drug_ingredient_mapping` | `drugdb` | Formulation ↔ ingredient bridge | `formulation_id`, `ingredient_id` |
| `ingredient_interactions` | `drugdb` | Ingredient-level interaction data | `id` (ingredient), `reacting_id` (ingredient), `severity`, `mechanism` |

---

## 8. Caching Keys Reference

All caching uses Redis with a 24-hour TTL (86,400 seconds). On Redis failure the system silently falls through to the database.

| Endpoint | Cache Key Pattern | Cached After |
|---|---|---|
| All label endpoints | `label:{endpoint_name}:{master_linkage_id}` | Resolver + JSONB extraction |
| Population Info | `label:population_info:{master_linkage_id}:{category}` | `category` = pediatric/geriatric/adult |
| Interactions | `interactions:{formulation_id}` | SQL query result |
| Check Interaction | *(not cached)* | Runs SQL every time |
| Drug Classes | `drug_classes:{formulation_id}` | SQL query result |
| Dosing | `dosing:{drug_id_1mg}:{age_group}` | SQL query result |
| Resolver | `resolver:{drug_id_1mg}` | Full ResolvedDrug object |

---

## End-to-End Request Flow Summary

```
Client request: GET /api/v1/drug/{drug_id_1mg}/{endpoint}
       │
       ▼
FastAPI router
       │
       ▼
resolve_drug(drug_id_1mg)
    Step 1 SQL: indian_brand (primary, then fallback)
    Step 2 SQL: drug + drug_master_linkage_unique (primary, then UNII bridge fallback)
    Step 3: parse JSONB in memory
       │
       ▼
Endpoint-specific logic:
    ├── Label endpoints (15):  extract JSONB path (no SQL) → Redis cache
    ├── Dosing:                5-CTE SQL (primary) → UNII-bridge SQL (fallback) → Redis cache
    ├── Interactions:          4-table join SQL → Redis cache
    ├── Check Interaction:     bidirectional UNION SQL (no cache)
    └── Drug Classes:          single SELECT SQL → Redis cache
       │
       ▼
JSON response with { success, drug_id_1mg, generic_name, data, meta: { source, cached, response_time_ms } }
```
