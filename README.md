# cdss-drug-info

Production-grade FastAPI backend serving drug information for Indian medical practitioners via 19 REST API endpoints. Built as the data layer for a Clinical Decision Support System (CDSS).

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [High Level Architecture](#2-high-level-architecture)
3. [Low Level Architecture](#3-low-level-architecture)
4. [Project Structure](#4-project-structure)
5. [Database Statistics](#5-database-statistics)
6. [All 19 Endpoints](#6-all-19-endpoints)
7. [SQL Queries Reference](#7-sql-queries-reference)
8. [Cache Keys Reference](#8-cache-keys-reference)
9. [Error Codes Reference](#9-error-codes-reference)
10. [How to Run](#10-how-to-run)
11. [Testing](#11-testing)
12. [What Frontend Needs to Send](#12-what-frontend-needs-to-send)
13. [Known Issues and Notes](#13-known-issues-and-notes)
14. [Implementation Notes](#14-implementation-notes)

---

## 1. Project Overview

**cdss-drug-info** is a production-grade FastAPI backend that exposes drug information for Indian medical practitioners through 18 REST API endpoints.

### Why Indian brand drugs?

Indian doctors prescribe drugs by **brand name**, not by generic name. A doctor prescribes "Plavix" or "Deplatt", not "clopidogrel bisulfate". This system bridges that gap: given a brand drug ID from 1mg.com (India's largest pharmacy platform), it resolves to the underlying generic and returns full clinical data.

### What does it serve?

- Contraindications, warnings, mechanism of action, adverse reactions
- Dosing regimens filtered by age group (neonate → geriatric)
- Drug-drug interactions with severity levels
- Drug classifications (pharmacologic, therapeutic, mechanism class)
- Patient information, product listings, food interactions
- All data returned as structured JSON for CDSS frontend consumption

### Data Sources

| Source    | Coverage                                              |
|-----------|-------------------------------------------------------|
| openFDA   | 46,176 records — primary source for label data        |
| DailyMed  | 3,935 records — fallback for label data, products     |
| DrugBank  | Food interactions, ingredient-level drug interactions |
| RxNorm    | Drug concept normalization, rxcui resolution          |

---

## 2. High Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            FRONTEND (CDSS)                          │
│                   Sends: drug_id_1mg + X-API-Key                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          API LAYER (FastAPI)                        │
│   ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────┐  │
│   │  auth.py │  │ logging.py │  │  timing.py │  │  18 Routers   │  │
│   │ X-API-Key│  │structlog   │  │X-Resp-Time │  │ label/dosing/ │  │
│   │ middleware│  │ middleware │  │ middleware  │  │ interactions  │  │
│   └──────────┘  └────────────┘  └────────────┘  └───────────────┘  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CACHE LAYER (Redis)                         │
│   TTL: 24 hours │ Keys: resolver:{id}, label:{type}:{id}, etc.     │
│   Cache miss → DB query → cache set → return response              │
│   Cache hit  → return directly (no DB query)                       │
│   Redis down → silent fallback to DB (non-fatal)                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ cache miss
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SERVICE LAYER                               │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐  │
│   │ resolver.py  │  │   label.py   │  │dosing.py │  │interact. │  │
│   │ 3-step chain │  │ JSONB extract│  │ 5-CTE SQL│  │ 5-table  │  │
│   │ drug→rxcui   │  │ openFDA/DM   │  │ query    │  │ join SQL │  │
│   └──────────────┘  └──────────────┘  └──────────┘  └──────────┘  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      DATABASE LAYER (PostgreSQL)                    │
│                                                                     │
│   schema: public                    schema: drugdb                  │
│   ┌──────────────────────┐   ┌────────────────────────────────┐    │
│   │  DrugMasterLinkage   │   │  masterlinkage_unique (6,295)  │    │
│   │  50,111 raw records  │──▶│  deduplicated by generic_name  │    │
│   │  (source of all JSONB│   │  combined_clean_jsonb          │    │
│   │   drug data)         │   └────────────────────────────────┘    │
│   └──────────────────────┘             ▲                           │
│                                        │ master_linkage_id          │
│                              ┌─────────┴──────────┐                │
│                              │     drug (table)    │                │
│                              │  formulation_id     │                │
│                              │  master_linkage_id  │                │
│                              │  rxcui              │                │
│                              └─────────▲───────────┘                │
│                                        │ rxcui                      │
│                              ┌─────────┴──────────┐                │
│                              │   indian_brand      │                │
│                              │   drug_id_1mg       │                │
│                              │   rxcui             │                │
│                              │   brand_name        │                │
│                              │   salt_composition  │                │
│                              └────────────────────┘                │
│                                                                     │
│   Data source ingestion → public.DrugMasterLinkage                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│   │ openFDA  │  │ DailyMed │  │ DrugBank │  │  RxNorm  │          │
│   │ 46,176   │  │  3,935   │  │interact. │  │  rxcui   │          │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│        └─────────────┴─────────────┴─────────────┘                │
│                              │                                      │
│                              ▼                                      │
│                   public.DrugMasterLinkage                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Low Level Architecture

### Resolution Chain

Every request starts with a `drug_id_1mg` (an integer ID from 1mg.com). The resolver turns this into clinical data through 3 sequential DB lookups:

```
drug_id_1mg (frontend input)
      │
      ▼  Step 1: drugdb.indian_brand
      ├── drug_id_1mg → rxcui[], brand_name, salt_composition
      │
      ▼  Step 2: drugdb.drug
      ├── rxcui[] → formulation_id, master_linkage_id, generic_name
      │
      ▼  Step 3: drugdb.masterlinkage_unique
      └── master_linkage_id → combined_clean_jsonb (full JSONB blob)
                │
                ├── openfda.safety.contraindications.text
                ├── openfda.clinical.mechanism_of_action.text
                ├── openfda.adverse_events.adverse_reactions.text
                ├── dailymed.drug_info.products (array)
                └── ... (all 15 label fields)
```

**Step by step:**

1. Frontend sends `drug_id_1mg` (e.g. `1002775`) plus `X-API-Key` header
2. **indian_brand lookup** — resolves drug_id_1mg to `rxcui[]`, `brand_name`, `salt_composition`
3. **drug lookup** — resolves `rxcui[]` to `formulation_id` and `master_linkage_id`
4. **masterlinkage_unique lookup** — resolves `master_linkage_id` to the full `combined_clean_jsonb`
5. Each endpoint extracts its specific fields from that JSONB blob

The resolver result is cached at `resolver:{drug_id_1mg}` so steps 1–4 are only executed once per drug per 24 hours.

---

### Caching Layer

```
Request arrives
      │
      ▼
Check Redis: GET {cache_key}
      │
      ├── HIT  → deserialize JSON → return response (no DB touched)
      │
      └── MISS → run DB query
                      │
                      ▼
                 cache SET {cache_key} TTL=86400s
                      │
                      ▼
                 return response

Redis down / connection error:
      └── log warning → fall through to DB query → non-fatal
```

- TTL: **86,400 seconds (24 hours)** for all keys
- Serialization: JSON via `json.dumps` / `json.loads`
- Failure mode: Redis errors are caught, logged, and ignored — the DB is always the source of truth

---

### Database Layer

#### `public.DrugMasterLinkage` — 50,111 records

The raw master table. Every record is one drug label from one source. Contains the original JSONB from openFDA, DailyMed, DrugBank, or RxNorm ingestion pipelines. Not queried directly by the API — used only during ETL.

#### `drugdb.masterlinkage_unique` — 6,295 records

Deduplicated view of DrugMasterLinkage. For each unique `generic_name`, one record is kept — the one with the largest `combined_clean_jsonb` payload (most complete data). This is the primary table for all label endpoint queries.

Key column:
- `combined_clean_jsonb` — structured JSONB with keys from all 4 sources merged into one document (292 openFDA keys, 129 DailyMed keys, 23 DrugBank keys, 13 RxNorm keys)

#### `drugdb.drug` — formulation-level table

One row per drug formulation. Links Indian brands (via rxcui) to the masterlinkage record.

| Column              | Description                                      |
|---------------------|--------------------------------------------------|
| `formulation_id`    | Primary key                                      |
| `master_linkage_id` | FK to masterlinkage_unique                       |
| `rxcui`             | RxNorm concept ID                                |
| `generic_name`      | Generic drug name                                |
| `dosage_forms`      | Available dosage forms                           |
| `pharmacologic_class` | Array of pharmacologic classes                 |
| `therapeutic_class` | Array of therapeutic classes                     |
| `mechanism_class`   | Array of mechanism classes                       |
| `has_dailymed`      | Boolean — DailyMed data available                |
| `has_openfda`       | Boolean — openFDA data available                 |
| `has_drugbank`      | Boolean — DrugBank data available                |
| `has_rxnorm`        | Boolean — RxNorm data available                  |

#### `drugdb.indian_brand` — Indian brand drugs

Maps 1mg.com brand IDs to RxNorm concepts.

| Column             | Description                                          |
|--------------------|------------------------------------------------------|
| `drug_id_1mg`      | 1mg.com drug ID (used as primary lookup key)         |
| `rxcui`            | RxNorm concept ID (array, may have multiple)         |
| `brand_name`       | Brand name as sold in India                          |
| `salt_composition` | Active ingredient composition string                 |
| `match_combination`| How the match was made (excludes drugbank/us_unapproved) |

#### `drugdb.ingredient_interactions` — ingredient-level drug interactions

| Column        | Description                                       |
|---------------|---------------------------------------------------|
| `id`          | ingredient_id (subject)                           |
| `reacting_id` | ingredient_id of the interacting drug             |
| `severity`    | major / moderate / minor                          |
| `mechanism`   | Free text description of interaction mechanism    |

#### `drugdb.drug_ingredient_mapping` — formulation to ingredient bridge

| Column          | Description                       |
|-----------------|-----------------------------------|
| `formulation_id`| FK to drugdb.drug                 |
| `ingredient_id` | FK to drugdb.ingredients          |

#### `drugdb.ingredients` — ingredient details

| Column       | Description                   |
|--------------|-------------------------------|
| `id`         | Primary key                   |
| `name`       | Ingredient name               |
| `drugbank_id`| DrugBank identifier           |
| `rxcui`      | RxNorm concept ID             |

#### `drugdb.dosing_regimen` — dosing rows per formulation

| Column                | Description                                          |
|-----------------------|------------------------------------------------------|
| `formulation_id`      | FK to drugdb.drug                                    |
| `age_group`           | neonate/infant/pediatric/adolescent/adult/geriatric/any |
| `frequency`           | Dosing frequency (e.g. QD, BID, TID)                |
| `route`               | Route of administration (oral, IV, etc.)             |
| `dose_amount`         | Dose amount as string (may include units)            |
| `dose_value`          | Numeric dose value for sorting                       |
| `dose_unit`           | Unit (mg, mcg, etc.)                                 |
| `duration`            | Duration of treatment                                |
| `indication`          | Clinical indication                                  |
| `administration_notes`| Special instructions                                 |
| `renal_function`      | Renal filter (default: any)                          |
| `hepatic_function`    | Hepatic filter (default: any)                        |
| `pregnancy_status`    | Pregnancy filter (default: any)                      |
| `dose_basis`          | fixed / weight-based / BSA-based                     |

---

## 4. Project Structure

```
cdss-drug-info/
├── main.py               — FastAPI app entry, startup/shutdown, middleware, routers
├── gunicorn.conf.py      — Production server config, 4 workers, uvicorn worker class
├── requirements.txt      — All dependencies with pinned versions
├── .env.example          — Environment variable template
├── Dockerfile            — Container build instructions
│
├── logs/
│   ├── api.log           — All structured JSON logs, rotated daily, 30-day retention
│   ├── access.log        — Gunicorn access log
│   └── error.log         — Error-only log, rotated daily, 90-day retention
│
├── tests/
│   ├── conftest.py       — Test fixtures: DB pool, test client, valid drug_id_1mg
│   ├── test_label.py     — Tests for all 15 label endpoints
│   ├── test_interactions.py — Tests for interactions endpoint
│   ├── test_dosing.py    — Tests for dosing regimen endpoint
│   └── test_resolver.py  — Tests for resolution chain
│
└── app/
    ├── config.py         — pydantic-settings, reads .env, validates all config vars
    ├── db.py             — asyncpg pool: min 5, max 20 connections
    ├── cache.py          — aioredis: get/set/delete with silent fail on Redis down
    ├── exceptions.py     — Custom exceptions with error codes and HTTP status codes
    │
    ├── middleware/
    │   ├── auth.py       — X-API-Key validation middleware, skips /health endpoint
    │   ├── logging.py    — structlog JSON logging per request and response
    │   └── timing.py     — X-Response-Time header, millisecond tracking
    │
    ├── models/
    │   ├── requests.py   — DrugRequest pydantic model
    │   └── responses.py  — All response pydantic models for each endpoint
    │
    ├── routers/
    │   ├── label.py      — 15 endpoints for JSONB field extraction
    │   ├── interactions.py — Drug interaction endpoint
    │   ├── drug_classes.py — Drug classification endpoint
    │   └── dosing.py     — Dosing regimen endpoint
    │
    └── services/
        ├── resolver.py   — Core 3-step resolution chain
        ├── cache_service.py — Redis cache helper with key builder
        ├── label.py      — JSONB extraction functions for all 15 label fields
        ├── interactions.py — 5-table join interaction query
        ├── drug_classes.py — Drug class query from drugdb.drug
        └── dosing.py     — Complex 5-CTE dosing regimen query
```

---

## 5. Database Statistics

| Metric                              | Count         |
|-------------------------------------|---------------|
| `public.DrugMasterLinkage` records  | 50,111        |
| `drugdb.masterlinkage_unique` records | 6,295       |
| Unique generic names                | 6,295         |
| openFDA coverage                    | 46,176 records|
| DailyMed fallback coverage          | 3,935 records |
| Records with neither source         | 0 (full coverage) |
| openFDA JSONB keys                  | 292           |
| DailyMed JSONB keys                 | 129           |
| DrugBank JSONB keys                 | 23            |
| RxNorm JSONB keys                   | 13            |

The `combined_clean_jsonb` in `masterlinkage_unique` merges all 4 sources into a single structured document with top-level keys: `openfda`, `dailymed`, `drugbank`, `rxnorm`.

---

## 6. All 19 Endpoints

All endpoints require the header `X-API-Key: <api_key>`.

All endpoints return the same envelope:

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": { },
  "meta": {
    "source": "openfda",
    "cached": false,
    "response_time_ms": 45.2,
    "product_count": null
  }
}
```

All 15 label endpoints (contraindications through food-interactions) return `data` with this structure:

```json
{
  "text": "Full text content...",
  "table": [{ "caption": "", "headers": [], "rows": [["cell"]] }],
  "subsections": [{ "section_title": "OVERDOSAGE", "content": "..." }]
}
```

Keys `table` and `subsections` are `null` when not present in the source data. `text` is `null` when no text exists for the drug.

---

### Endpoint 1: Contraindications

```
GET /api/v1/drug/{drug_id_1mg}/contraindications
```

**Description:** Returns conditions in which the drug must not be used.

**Source table:** `drugdb.masterlinkage_unique`

| Path          | Value                                                      |
|---------------|------------------------------------------------------------|
| openFDA       | `combined_clean_jsonb → openfda → safety → contraindications → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → safety → contraindications → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/contraindications
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "contraindications": "PLAVIX is contraindicated in patients with active pathological bleeding such as peptic ulcer or intracranial hemorrhage. PLAVIX is contraindicated in patients with hypersensitivity to clopidogrel or to any component of the product."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 42.1 }
}
```

**Error responses:**

```json
{ "success": false, "error_code": "DRUG_NOT_FOUND", "message": "No drug found for drug_id_1mg: 999999" }
{ "success": false, "error_code": "NO_LABEL_DATA", "message": "No label data found for master_linkage_id: ..." }
```

---

### Endpoint 2: Warnings

```
GET /api/v1/drug/{drug_id_1mg}/warnings
```

**Description:** Returns boxed warnings and general warnings/precautions.

**Source table:** `drugdb.masterlinkage_unique`

| Path          | Value                                                                            |
|---------------|----------------------------------------------------------------------------------|
| openFDA       | `safety → warnings_and_cautions → text` OR `safety → warnings → text`           |
| DailyMed fallback | `safety → warnings_and_precautions → content` OR `safety → warnings → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/warnings
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "warnings": "Bleeding Risk: Thienopyridines, including PLAVIX, increase the risk of bleeding. Premature discontinuation of PLAVIX increases the risk of cardiovascular events..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 38.5 }
}
```

---

### Endpoint 3: Mechanism of Action

```
GET /api/v1/drug/{drug_id_1mg}/mechanism-of-action
```

**Description:** Returns the pharmacological mechanism by which the drug works.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                    |
|---------|----------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → clinical → mechanism_of_action → text` |
| DailyMed fallback | None                                          |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/mechanism-of-action
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "mechanism_of_action": "Clopidogrel is a prodrug. The active metabolite of clopidogrel selectively inhibits the binding of adenosine diphosphate (ADP) to its platelet P2Y12 receptor and the subsequent ADP-mediated activation of the GPIIb/IIIa complex, thereby inhibiting platelet aggregation..."
  },
  "meta": { "source": "openfda", "cached": true, "response_time_ms": 2.3 }
}
```

---

### Endpoint 4: Microbiology

```
GET /api/v1/drug/{drug_id_1mg}/microbiology
```

**Description:** Returns microbiological susceptibility data (relevant for antibiotics).

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                |
|---------|------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → clinical → microbiology → text` |
| DailyMed fallback | None                                      |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/microbiology
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "microbiology": null
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 41.0 }
}
```

---

### Endpoint 5: Generic Name

```
GET /api/v1/drug/{drug_id_1mg}/generic-name
```

**Description:** Returns the generic (non-proprietary) name of the drug.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                  |
|---------|--------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → drug_info → generic_name` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → drug_info → products → 0 → generic_name` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/generic-name
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "generic_name": "CLOPIDOGREL BISULFATE"
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 39.8 }
}
```

---

### Endpoint 6: Patient Info

```
GET /api/v1/drug/{drug_id_1mg}/patient-info
```

**Description:** Returns patient-facing counseling information.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                            |
|---------|------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → patient_info → information_for_patients → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → patient_info → information_for_patients → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/patient-info
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "patient_info": "Tell patients that it may take them longer than usual to stop bleeding when they take PLAVIX, and that they may bruise and bleed more easily..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 44.2 }
}
```

---

### Endpoint 7: Adverse Reactions

```
GET /api/v1/drug/{drug_id_1mg}/adverse-reactions
```

**Description:** Returns clinically significant adverse reactions.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                           |
|---------|-----------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → adverse_events → adverse_reactions → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → adverse_events → adverse_reactions → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/adverse-reactions
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "adverse_reactions": "PLAVIX is associated with bleeding events. Hemorrhage was the most frequently reported adverse reaction. Serious bleeding events: 1.4% PLAVIX vs 1.3% placebo..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 40.1 }
}
```

---

### Endpoint 8: Drug Description

```
GET /api/v1/drug/{drug_id_1mg}/drug-description
```

**Description:** Returns the general drug description and chemical information.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                     |
|---------|-----------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → labeling_content → drug_description` |
| DailyMed fallback | None                                           |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/drug-description
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "drug_description": "PLAVIX (clopidogrel bisulfate) is a thienopyridine class inhibitor of ADP-induced platelet aggregation acting by direct inhibition of adenosine diphosphate (ADP) binding to its receptor and of the subsequent ADP-mediated activation of the GPIIb/IIIa complex..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 43.7 }
}
```

---

### Endpoint 9: Indications

```
GET /api/v1/drug/{drug_id_1mg}/indications
```

**Description:** Returns FDA-approved indications and usage.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                                   |
|---------|-------------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → labeling_content → indications_and_usage → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → labeling_content → indications_and_usage → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/indications
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "indications": "PLAVIX is indicated to reduce the rate of myocardial infarction and stroke in patients with non-ST-segment elevation acute coronary syndrome (unstable angina/non-Q-wave MI)..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 41.5 }
}
```

---

### Endpoint 10: Geriatric Use

```
GET /api/v1/drug/{drug_id_1mg}/geriatric-use
```

**Description:** Returns prescribing guidance for elderly patients.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                              |
|---------|--------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → population_specific → geriatric_use → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → population_specific → geriatric_use → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/geriatric-use
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "geriatric_use": "Of the total number of subjects in clinical studies of PLAVIX, 45% were 65 and over, and 15% were 75 and over. No overall differences in safety or effectiveness were observed between these subjects and younger subjects..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 39.3 }
}
```

---

### Endpoint 11: Pediatric Use

```
GET /api/v1/drug/{drug_id_1mg}/pediatric-use
```

**Description:** Returns prescribing guidance for pediatric patients.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                             |
|---------|-------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → population_specific → pediatric_use → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → population_specific → pediatric_use → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/pediatric-use
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "pediatric_use": "Safety and effectiveness in pediatric patients have not been established."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 37.9 }
}
```

---

### Endpoint 12: Pregnancy Use

```
GET /api/v1/drug/{drug_id_1mg}/pregnancy-use
```

**Description:** Returns pregnancy category and guidance.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                                |
|---------|----------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → population_specific → use_in_pregnancy → text` |
| DailyMed fallback | `combined_clean_jsonb → dailymed → population_specific → teratogenic_effects → content` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/pregnancy-use
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "pregnancy_use": "Pregnancy Category B. Reproduction studies performed in rats and rabbits revealed no evidence of impaired fertility or fetotoxicity due to clopidogrel..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 40.8 }
}
```

---

### Endpoint 13: Specific Populations

```
GET /api/v1/drug/{drug_id_1mg}/specific-populations
```

**Description:** Returns complete use-in-specific-populations section (pregnancy, lactation, pediatric, geriatric, renal/hepatic impairment).

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                                                          |
|---------|--------------------------------------------------------------------------------|
| openFDA | `combined_clean_jsonb → openfda → population_specific → use_in_specific_populations → text` |
| DailyMed fallback | None                                                                |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/specific-populations
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "specific_populations": "Pregnancy: Category B. Nursing Mothers: Studies in rats have shown that clopidogrel and/or its metabolites are excreted in the milk..."
  },
  "meta": { "source": "openfda", "cached": false, "response_time_ms": 42.0 }
}
```

---

### Endpoint 14: Products

```
GET /api/v1/drug/{drug_id_1mg}/products
```

**Description:** Returns available drug products as a flat tabular list with physical characteristics.

**Source table:** `drugdb.masterlinkage_unique`

| Path    | Value                                             |
|---------|---------------------------------------------------|
| DailyMed only | `combined_clean_jsonb → dailymed → drug_info → products` |

Returns flat array of product rows, each with:
- `generic_name`
- `dosage_form`
- `route_of_administration`
- `color` (from physical_characteristics)
- `shape` (from physical_characteristics)
- `imprint` (from physical_characteristics)
- `size_mm` (from physical_characteristics)

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/products
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": [
    {
      "generic_name": "CLOPIDOGREL BISULFATE tablet",
      "dosage_form": "TABLET",
      "route_of_administration": "ORAL",
      "color": "PINK",
      "shape": "OVAL",
      "imprint": "1171",
      "size_mm": "9"
    }
  ],
  "meta": { "source": "dailymed", "cached": false, "response_time_ms": 46.3, "product_count": 1 }
}
```

---

### Endpoint 15: Food Interactions

```
GET /api/v1/drug/{drug_id_1mg}/food-interactions
```

**Description:** Returns known food interactions for the drug.

**Source table:** `drugdb.masterlinkage_unique`

| Path     | Value                                                         |
|----------|---------------------------------------------------------------|
| DrugBank | `combined_clean_jsonb → drugbank → drug_interactions → food_interactions → text` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/food-interactions
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "food_interactions": "Avoid excessive alcohol consumption as it may increase the risk of gastrointestinal bleeding."
  },
  "meta": { "source": "drugbank", "cached": false, "response_time_ms": 38.7 }
}
```

---

### Endpoint 19: Ingredients

```
GET /api/v1/drug/{drug_id_1mg}/ingredients
```

**Description:** Returns active and inactive ingredients for each product, grouped by product name.

**Source table:** `drugdb.masterlinkage_unique`

| Path | Value |
|------|-------|
| DailyMed only | `combined_clean_jsonb → dailymed → drug_info → products → active_ingredients / inactive_ingredients` |

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/ingredients
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "active": [
      {
        "product": "CLOPIDOGREL BISULFATE tablet",
        "ingredients": [
          { "name": "CLOPIDOGREL BISULFATE", "strength": "97.875 mg/1" }
        ]
      }
    ],
    "inactive": [
      {
        "product": "CLOPIDOGREL BISULFATE tablet",
        "ingredients": ["LACTOSE MONOHYDRATE", "MICROCRYSTALLINE CELLULOSE", "HYDROXYPROPYL CELLULOSE"]
      }
    ]
  },
  "meta": { "source": "dailymed", "cached": false, "response_time_ms": 10.2, "product_count": 1 }
}
```

---

### Endpoint 16: Drug Interactions

```
GET /api/v1/drug/{drug_id_1mg}/interactions
```

**Description:** Returns all known drug-drug interactions at the ingredient level with severity and mechanism.

**Source tables:** `drugdb.ingredient_interactions`, `drugdb.drug_ingredient_mapping`, `drugdb.ingredients`, `drugdb.drug` (5-table join)

**Resolution chain:**
```
formulation_id
  → drug_ingredient_mapping  (get our ingredients)
  → ingredients              (get ingredient names)
  → ingredient_interactions  (get interactions)
  → ingredients              (get reacting ingredient names)
  → drug_ingredient_mapping  (get formulation with that ingredient)
  → drug                     (get the interacting drug name)
```

**Returns:** `our_ingredient`, `interacting_ingredient`, `interacting_drug`, `severity`, `mechanism`

**Meta includes:** `severity_counts` with `major`, `moderate`, `minor` counts

**Example scale:** Clopidogrel → 63,322 interactions (major: 26,347 | moderate: 36,769 | minor: 206)

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/interactions
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "interactions": [
      {
        "our_ingredient": "clopidogrel",
        "interacting_ingredient": "abciximab",
        "interacting_drug": "abciximab",
        "severity": "major",
        "mechanism": "Additive antiplatelet effects may increase the risk of bleeding."
      }
    ]
  },
  "meta": {
    "source": "drugdb",
    "cached": false,
    "response_time_ms": 312.4,
    "severity_counts": {
      "major": 26347,
      "moderate": 36769,
      "minor": 206
    }
  }
}
```

---

### Endpoint 17: Drug Classes

```
GET /api/v1/drug/{drug_id_1mg}/drug-classes
```

**Description:** Returns pharmacologic, therapeutic, and mechanism classification of the drug.

**Source table:** `drugdb.drug` (columns: `pharmacologic_class[]`, `therapeutic_class[]`, `mechanism_class[]`)

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/drug-classes
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "pharmacologic_class": ["Platelet Aggregation Inhibitors [MoA]", "Thienopyridines [CS]"],
    "therapeutic_class": ["ANTICOAGULANTS/THROMBOLYTICS"],
    "mechanism_class": ["P2Y12 Platelet Receptor Antagonists [MoA]"]
  },
  "meta": { "source": "drugdb", "cached": false, "response_time_ms": 28.6 }
}
```

---

### Endpoint 18: Dosing Regimen

```
GET /api/v1/drug/{drug_id_1mg}/dosing-regimen?age_group=adult
```

**Description:** Returns filtered dosing rows for the specified age group.

**Source tables:** `drugdb.dosing_regimen`, `drugdb.indian_brand`, `drugdb.drug`, `drugdb.drug_ingredient_mapping`, `drugdb.ingredients`

**Query parameter:**

| `age_group` | Population                         |
|-------------|------------------------------------|
| `neonate`   | 0–28 days                          |
| `infant`    | 1 month – 2 years                  |
| `pediatric` | 2–12 years                         |
| `adolescent`| 12–18 years                        |
| `adult`     | 18–65 years                        |
| `geriatric` | 65+ years                          |
| `any`       | All age groups                     |

**Uses a 5-CTE query:**
```
salt_ingredients → candidate_formulations → best_formulation → ranked → final SELECT
```

**Returns:** `brand_name`, `salt_composition`, `generic_name`, `frequency`, `route`, `dose_amount`, `dose_unit`, `duration`, `indication`, `instructions`

**Example scale:** Clopidogrel adult → 9 dosing rows (QD oral route)

**Example request:**

```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/drug/1002775/dosing-regimen?age_group=adult"
```

**Example response:**

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "dosing": [
      {
        "brand_name": "Deplatt",
        "salt_composition": "Clopidogrel (75mg)",
        "generic_name": "clopidogrel",
        "frequency": "QD",
        "route": "oral",
        "dose_amount": "75 mg",
        "dose_unit": "mg",
        "duration": "ongoing",
        "indication": "reduction of atherosclerotic events",
        "instructions": "Take with or without food"
      }
    ]
  },
  "meta": { "source": "drugdb", "cached": false, "response_time_ms": 87.3 }
}
```

**Error responses:**

```json
{ "success": false, "error_code": "NO_DOSING_DATA", "message": "No dosing data found for drug_id_1mg: 1002775, age_group: neonate" }
```

---

## 7. SQL Queries Reference

### Resolver Query 1 — indian_brand lookup

```sql
SELECT ib.rxcui, ib.salt_composition, ib.brand_name
FROM drugdb.indian_brand ib
WHERE ib.drug_id_1mg = $1
  AND ib.match_combination NOT IN ('drugbank', 'us_unapproved')
LIMIT 1
```

Parameters: `$1 = drug_id_1mg`

### Resolver Query 2 — drug table lookup

```sql
SELECT d.formulation_id, d.master_linkage_id, d.generic_name
FROM drugdb.drug d
WHERE d.rxcui = ANY($1::text[])
LIMIT 1
```

Parameters: `$1 = rxcui[] array from Step 1`

### Resolver Query 3 — masterlinkage_unique lookup

```sql
SELECT master_linkage_id, generic_name, combined_clean_jsonb
FROM drugdb.masterlinkage_unique
WHERE master_linkage_id = $1
LIMIT 1
```

Parameters: `$1 = master_linkage_id from Step 2`

---

### Interactions Query — 5-table join

```sql
SELECT
    i_ours.name           AS our_ingredient,
    i_theirs.name         AS interacting_ingredient,
    d_theirs.generic_name AS interacting_drug,
    ii.severity,
    ii.mechanism
FROM drugdb.drug_ingredient_mapping di_ours
JOIN drugdb.ingredients i_ours
    ON i_ours.id = di_ours.ingredient_id
JOIN drugdb.ingredient_interactions ii
    ON ii.id = di_ours.ingredient_id
JOIN drugdb.ingredients i_theirs
    ON i_theirs.id = ii.reacting_id
JOIN drugdb.drug_ingredient_mapping di_theirs
    ON di_theirs.ingredient_id = ii.reacting_id
JOIN drugdb.drug d_theirs
    ON d_theirs.formulation_id = di_theirs.formulation_id
WHERE di_ours.formulation_id = $1
ORDER BY
    CASE ii.severity
        WHEN 'major'    THEN 1
        WHEN 'moderate' THEN 2
        WHEN 'minor'    THEN 3
        ELSE 4
    END,
    i_theirs.name
```

Parameters: `$1 = formulation_id`

---

### Drug Classes Query

```sql
SELECT
    pharmacologic_class,
    therapeutic_class,
    mechanism_class
FROM drugdb.drug
WHERE formulation_id = $1
```

Parameters: `$1 = formulation_id`

---

### Dosing Regimen Query — 5-CTE query

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

Parameters:
- `$1 = drug_id_1mg`
- `$2 = age_group[]` (e.g. `['adult', 'any']` — always includes `'any'` alongside the specified group)

**Age group mapping:**

| Input        | Query array passed as `$2`             |
|--------------|----------------------------------------|
| `neonate`    | `['neonate', 'any']`                   |
| `infant`     | `['infant', 'any']`                    |
| `pediatric`  | `['pediatric', 'any']`                 |
| `adolescent` | `['adolescent', 'any']`                |
| `adult`      | `['adult', 'any']`                     |
| `geriatric`  | `['geriatric', 'adult', 'any']`        |
| `any`        | `['any']`                              |

---

## 8. Cache Keys Reference

All TTLs are **86,400 seconds (24 hours)**.

| Cache Key Pattern                                | Endpoint                    |
|--------------------------------------------------|-----------------------------|
| `resolver:{drug_id_1mg}`                         | Resolution chain (steps 1–3)|
| `label:contraindications:{master_linkage_id}`    | /contraindications          |
| `label:warnings:{master_linkage_id}`             | /warnings                   |
| `label:mechanism_of_action:{master_linkage_id}`  | /mechanism-of-action        |
| `label:microbiology:{master_linkage_id}`         | /microbiology               |
| `label:generic_name:{master_linkage_id}`         | /generic-name               |
| `label:patient_info:{master_linkage_id}`         | /patient-info               |
| `label:adverse_reactions:{master_linkage_id}`    | /adverse-reactions          |
| `label:drug_description:{master_linkage_id}`     | /drug-description           |
| `label:indications:{master_linkage_id}`          | /indications                |
| `label:geriatric_use:{master_linkage_id}`        | /geriatric-use              |
| `label:pediatric_use:{master_linkage_id}`        | /pediatric-use              |
| `label:pregnancy_use:{master_linkage_id}`        | /pregnancy-use              |
| `label:specific_populations:{master_linkage_id}` | /specific-populations       |
| `label:products:{master_linkage_id}`             | /products                   |
| `label:food_interactions:{master_linkage_id}`    | /food-interactions          |
| `label:ingredients:{master_linkage_id}`          | /ingredients                |
| `interactions:{formulation_id}`                  | /interactions               |
| `drug_classes:{formulation_id}`                  | /drug-classes               |
| `dosing:{drug_id_1mg}:{age_group}`               | /dosing-regimen             |

---

## 9. Error Codes Reference

| Error Code       | HTTP Status | Description                                                  |
|------------------|-------------|--------------------------------------------------------------|
| `DRUG_NOT_FOUND` | 404         | `drug_id_1mg` not found in `drugdb.indian_brand` table       |
| `NO_FORMULATION` | 404         | `rxcui` from `indian_brand` not matched in `drugdb.drug`     |
| `NO_LABEL_DATA`  | 404         | `master_linkage_id` not found in `drugdb.masterlinkage_unique` |
| `NO_DOSING_DATA` | 404         | No dosing rows for this drug + age_group combination         |
| `DB_ERROR`       | 500         | Database connection failure or query error                   |
| `CACHE_ERROR`    | 500         | Redis failure — non-fatal, system falls back to DB           |

---

## 10. How to Run

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
DATABASE_URL=postgresql://user:password@host:port/dbname
REDIS_HOST=localhost
REDIS_PORT=6379
API_KEY=your-api-key-here
LOG_LEVEL=INFO
CACHE_TTL=86400
MAX_DB_POOL_SIZE=20
MIN_DB_POOL_SIZE=5
SENTRY_DSN=                    # optional
```

> `DATABASE_URL` is parsed automatically into individual host/port/name/user/password fields via `model_validator` in `config.py`.

---

### Development

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

---

### Production

```bash
gunicorn -c gunicorn.conf.py main:app
```

Production config (`gunicorn.conf.py`):
- 4 Uvicorn workers
- Uvicorn worker class for async support
- Access log → `logs/access.log`
- Error log → `logs/error.log`

---

### Docker

```bash
# Build
docker build -t cdss-drug-info .

# Run
docker run -p 8000:8000 --env-file .env cdss-drug-info

# Or with Docker Compose (recommended for Redis)
docker-compose up
```

> When running in Docker, Redis is resolved as `redis://redis:6379` (internal Docker hostname). When running locally, set `REDIS_HOST=localhost`.

---

### Health Check

```bash
curl http://localhost:8000/health
```

Response:

```json
{
  "status": "healthy",
  "database": "connected",
  "cache": "connected",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

> Redis shows as `disconnected` in `/health` when running outside Docker — this is expected and non-fatal.

---

## 11. Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Run Individual Test Files

```bash
pytest tests/test_label.py -v
pytest tests/test_interactions.py -v
pytest tests/test_dosing.py -v
pytest tests/test_resolver.py -v
```

### Test Results

```
63 passed, 3 warnings
```

---

### Sample curl Commands

```bash
# Health check (no auth required)
curl http://localhost:8000/health

# Contraindications (Clopidogrel — drug_id_1mg: 1002775)
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/contraindications

# Warnings
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/warnings

# Mechanism of action
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/mechanism-of-action

# Microbiology
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/microbiology

# Generic name
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/generic-name

# Patient info
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/patient-info

# Adverse reactions
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/adverse-reactions

# Drug description
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/drug-description

# Indications
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/indications

# Geriatric use
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/geriatric-use

# Pediatric use
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/pediatric-use

# Pregnancy use
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/pregnancy-use

# Specific populations
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/specific-populations

# Products
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/products

# Food interactions
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/food-interactions

# Ingredients
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/ingredients

# Drug-drug interactions
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/interactions

# Drug classes
curl -H "X-API-Key: your-api-key" \
  http://localhost:8000/api/v1/drug/1002775/drug-classes

# Dosing regimen — adult
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/drug/1002775/dosing-regimen?age_group=adult"

# Dosing regimen — pediatric
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/drug/1002775/dosing-regimen?age_group=pediatric"
```

---

## 12. What Frontend Needs to Send

### For all 18 endpoints (label + interactions + drug-classes + ingredients):

| Item        | Value                                           |
|-------------|-------------------------------------------------|
| Path param  | `drug_id_1mg` — integer ID from 1mg.com         |
| Header      | `X-API-Key: <api_key>`                          |
| Body        | None                                            |

### For dosing regimen:

| Item         | Value                                                                         |
|--------------|-------------------------------------------------------------------------------|
| Path param   | `drug_id_1mg`                                                                 |
| Query param  | `age_group` — one of: `neonate`, `infant`, `pediatric`, `adolescent`, `adult`, `geriatric`, `any` |
| Header       | `X-API-Key: <api_key>`                                                        |
| Body         | None                                                                          |

### Standard success response structure:

```json
{
  "success": true,
  "drug_id_1mg": "1002775",
  "generic_name": "Clopidogrel bisulfate",
  "data": {
    "...endpoint specific field...": "..."
  },
  "meta": {
    "source": "openfda",
    "cached": false,
    "response_time_ms": 45.2
  }
}
```

### Standard error response structure:

```json
{
  "success": false,
  "error_code": "DRUG_NOT_FOUND",
  "message": "No drug found for drug_id_1mg: 999999",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 13. Known Issues and Notes

### Redis hostname in Docker vs. host

When running outside Docker, `/health` will report Redis as `disconnected` because the default Redis URL is `redis://redis:6379` (the Docker internal service hostname). Set `REDIS_HOST=localhost` in your `.env` when running locally. The API works correctly in both cases — Redis failure is non-fatal.

### 2 skipped tests

The 2 skipped tests validate cache-hit behavior. They require Redis to be reachable at `redis://redis:6379`. They pass inside Docker Compose but are automatically skipped when running pytest on the host.

### Interactions result size

The interactions endpoint returns all ingredient-level interactions without deduplication. For drugs with many ingredients (e.g. Clopidogrel returns 63,322 rows), response payloads are large. A deduplication and pagination strategy is planned for the next version.

### Food interactions source

Food interactions come from **DrugBank**, not DailyMed. If a drug has no DrugBank data in `combined_clean_jsonb`, this field returns `null`.

---

## 14. Implementation Notes

Four non-obvious issues were solved during implementation:

### 1. `.env` uses `DATABASE_URL`, not individual variables

The app uses a single `DATABASE_URL` connection string (PostgreSQL standard) rather than separate `DB_HOST`, `DB_PORT`, `DB_NAME`, etc. variables. A `model_validator` in `app/config.py` parses the URL and populates the individual fields internally, keeping the interface clean for deployment environments (Heroku, Railway, Render, etc.) that provide a single `DATABASE_URL`.

### 2. `BaseHTTPMiddleware` conflicts with asyncpg in tests

FastAPI's `BaseHTTPMiddleware` wraps the ASGI receive channel in a way that interferes with asyncpg's connection lifecycle under pytest-asyncio. All three middleware components (auth, logging, timing) were rewritten as **pure ASGI middleware classes** (implementing `__call__(scope, receive, send)` directly) to resolve this conflict.

### 3. `combined_clean_jsonb` returned as string from asyncpg

When asyncpg fetches a `jsonb` column, it returns a Python `str` (the raw JSON text) rather than a parsed `dict`. A `json.loads()` fallback was added in `resolver.py` — if the value is a `str`, it is deserialized before extraction. This is transparent to all callers.

### 4. Redis at `redis://redis:6379` unreachable from host

The Docker Compose stack defines Redis under the service name `redis`, so internal URLs use `redis://redis:6379`. When running the app outside Docker, this hostname doesn't resolve. The cache layer catches the connection error, logs a warning, and falls back to the database — exactly as designed. No code change needed; just set `REDIS_HOST=localhost` in local `.env`.
