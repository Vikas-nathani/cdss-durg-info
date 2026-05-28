#!/usr/bin/env python3
"""
Final comprehensive fill for top500_coverage_result.csv.

Combines:
  1. Manually verified SQL-query results (hard-coded CORRECTIONS dict)
  2. Auto-fill using in-memory matching with salt validation (for any still-missing)

Run this on the existing CSV — it overwrites in-place.
"""
import asyncio
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

IN_CSV  = ROOT / "top500_coverage_result.csv"
OUT_CSV = ROOT / "top500_coverage_result.csv"

SOURCE_RANK = {"dailymed": 1, "openfda": 2, "rxnorm": 3, "partial_drugbank": 4}

# ── MANUAL CORRECTIONS ───────────────────────────────────────────────────────
# Format:  "brand_name_csv": (drug_id_1mg, matched_brand, match_type)
# drug_id_1mg=None means genuinely not in DB.
# 9000xxx = curated Indian-brand namespace added to indian_brand.
CORRECTIONS: dict[str, tuple] = {
    # ── truly not in DB ───────────────────────────────────────────────────────
    "Cdiff 200":                (None, None, None),   # Cdiff A Gel = Adapalene, wrong drug
    "Magnex 1.5g":              (None, None, None),   # no Cefoperazone/Sulbactam 1.5g in DB
    "Sulbacin 1.5g":            (None, None, None),   # Sulbacin = Sultamicillin, wrong drug
    "Omnicef 300":              (None, None, None),   # DB Omnicef = Cefotaxime, not Cefdinir
    "Neurobion Forte":          (None, None, None),   # not in DB
    "Imipenem Cilastatin 500":  (None, None, None),   # not in DB
    "Diltiazem CD 90":          (None, None, None),   # not in DB
    "Esidrex 25":               (None, None, None),   # Esidrex = HCT brand, not in DB
    "Petril 25":                (None, None, None),   # not in DB
    "Propylthiouracil 50":      (None, None, None),   # not in DB
    "Flomax 0.4":               (None, None, None),   # not in DB
    "Rapido 0.4":               (None, None, None),   # not in DB
    "Asthalin Inhaler":         (None, None, None),   # not in DB as inhaler
    "Budesonide Rotacap 200":   (None, None, None),   # not in DB as rotacap
    "Acne Aid":                 (None, None, None),   # not in DB
    "Duac Gel":                 (None, None, None),   # not in DB
    "Ciprofloxacin Eye":        (None, None, None),   # not in DB as eye drops
    "Tear Natural":             (None, None, None),   # not in DB
    "Vitamin D3 60000IU":       (None, None, None),   # not in DB
    "Ferrous Sulphate 150":     (None, None, None),   # not in DB
    "Folic Acid 1":             (None, None, None),   # not in DB (only 5mg in 9000xxx)
    "Prozac 20":                (None, None, None),   # Prozac brand not in DB
    "Liv 52 DS":                (None, None, None),   # not in DB
    "Koflet Syrup":             (None, None, None),   # not in DB
    "Calcium Acetate 667":      (None, None, None),   # not in DB
    "Iron Sucrose 100":         (None, None, None),   # not in DB
    "Feronia XT Inj":           (None, None, None),   # not in DB

    # ── legacy non-9000xxx matches (keep for non-duplicate brands) ────────────
    "Moxikind CV 625":          (329310,  "Moxikind-CV 625 Tablet",              "hyphen_prefix"),
    "Betaloc ZOK 25":           (1118850, "betalOC 25mg Tablet",                 "stripped_prefix"),
    "Benadryl Cough":           (114690,  "Benadryl Syrup",                      "stripped_prefix"),
    "Galvus Met 50/500":        (477513,  "Galvus Met 50mg/500mg Tablet",        "prefix_variant"),
    "Janumet 50/1000":          (297270,  "Janumet 1000mg/50mg Tablet",          "prefix_variant"),
    "Mesacol 400":              (505968,  "Mesacol Tablet DR",                   "stripped_prefix"),
    "Rosuvas EZ 10/10":         (141443,  "Rosuvas EZ 10 Tablet",                "prefix_variant"),
    "Symbicort 160/4.5":        (370673,  "Symbicort 320mcg/9mcg Turbuhaler",    "stripped_prefix"),
    "Uprise D3":                (918999,  "Uprise-D3 60K Soft Gelatin Capsule",  "hyphen_prefix"),
    "Valparin CR 500":          (765408,  "Valparin Chrono 500 Tablet CR",       "stripped_prefix"),
    "Vymada 49/51":             (560343,  "Vymada 100mg Tablet",                 "prefix_variant"),
    "Xatral 10":                (732906,  "Alfuzosin Tablet",                    "generic_name"),
    "Panimun 25":               (246497,  "Panimun Bioral 25mg Capsule",         "exact"),
    "Arimidex 1":               (163750,  "Arimidex 1mg Tablet",                 "exact"),
    "Vigamox 0.5%":             (60391,   "VigaMOX Ophthalmic Solution",         "stripped_prefix"),
    "Digene Syrup":             (1153647, "Digene Insta Shots Orange",           "stripped_prefix"),
    "Antacid Syrup":            (463467,  "Antacid Tablet",                      "stripped_prefix"),
    "Imodium 2":                (135618,  "Imodium Capsule",                     "stripped_prefix"),

    # ── 9000xxx curated namespace — exact brand matches ───────────────────────
    "Voveran 50":               (9000002, "Voveran 50",               "exact"),
    "Azithrocin 250":           (9000004, "Azithrocin 250",           "exact"),
    "Nuvox 500":                (9000005, "Nuvox 500",                "exact"),
    "Avelox 400":               (9000006, "Avelox 400",               "exact"),
    "Vancomycin 500":           (9000008, "Vancomycin 500",           "exact"),
    "Rifampicin 450":           (9000009, "Rifampicin 450",           "exact"),
    "Pyrazinamide 750":         (9000010, "Pyrazinamide 750",         "exact"),
    "Ethambutol 800":           (9000011, "Ethambutol 800",           "exact"),
    "Cefdinir 300":             (9000012, "Cefdinir 300",             "exact"),
    "Sporanox 100":             (9000015, "Sporanox 100",             "exact"),
    "Oseltamivir 75":           (9000016, "Oseltamivir 75",           "exact"),
    "Tenofovir 300":            (9000017, "Tenofovir 300",            "exact"),
    "Lamivudine 150":           (9000018, "Lamivudine 150",           "exact"),
    "Emtriva 200":              (9000019, "Emtriva 200",              "exact"),
    "Niclocide 500":            (9000020, "Niclocide 500",            "exact"),
    "Chloroquine 250":          (9000021, "Chloroquine 250",          "exact"),
    "Primaquine 7.5":           (9000022, "Primaquine 7.5",           "exact"),
    "Coartem":                  (9000023, "Coartem",                  "exact"),
    "Glucophage 500":           (9000024, "Glucophage 500",           "exact"),
    "Glipizide 5":              (9000026, "Glipizide 5",              "exact"),
    "Diabenyl 5":               (9000027, "Diabenyl 5",               "exact"),
    "Janumet 50/500":           (9000028, "Janumet 50/500",           "exact"),
    "Nesina 25":                (9000029, "Nesina 25",                "exact"),
    "Farxiga 10":               (9000030, "Farxiga 10",               "exact"),
    "Invokana 100":             (9000031, "Invokana 100",             "exact"),
    "Glitazone MF 15/500":      (9000032, "Glitazone MF 15/500",     "exact"),
    "Basalin":                  (9000033, "Basalin",                  "exact"),
    "Lipitor 20":               (9000034, "Lipitor 20",               "exact"),
    "Loprin 150":               (9000036, "Loprin 150",               "exact"),
    "Carvedilol 6.25":          (9000038, "Carvedilol 6.25",          "exact"),
    "Bisoprolol 2.5":           (9000039, "Bisoprolol 2.5",           "exact"),
    "Norvasc 5":                (9000042, "Norvasc 5",                "exact"),
    "Felodipine 5":             (9000043, "Felodipine 5",             "exact"),
    "Verapamil SR 120":         (9000044, "Verapamil SR 120",         "exact"),
    "Perindopril 4":            (9000047, "Perindopril 4",            "exact"),
    "Losartan 50":              (9000048, "Losartan 50",              "exact"),
    "Micardis 40":              (9000050, "Micardis 40",              "exact"),
    "Valsartan 80":             (9000051, "Valsartan 80",             "exact"),
    "Irbesartan 150":           (9000052, "Irbesartan 150",           "exact"),
    "Hydrochlorothiazide 25":   (9000054, "Hydrochlorothiazide 25",   "exact"),
    "Torsemide 10":             (9000055, "Torsemide 10",             "exact"),
    "Amiodarone 200":           (9000056, "Amiodarone 200",           "exact"),
    "Nitrocontin 2.6":          (9000057, "Nitrocontin 2.6",          "exact"),
    "Ikorel 5":                 (9000059, "Ikorel 5",                 "exact"),
    "Vastarel MR 35":           (9000060, "Vastarel MR 35",           "exact"),
    "Ranexa 500":               (9000061, "Ranexa 500",               "exact"),
    "Warfarin 5":               (9000062, "Warfarin 5",               "exact"),
    "Rivaroxaban 20":           (9000063, "Rivaroxaban 20",           "exact"),
    "Apixaban 5":               (9000064, "Apixaban 5",               "exact"),
    "Dabigatran 150":           (9000065, "Dabigatran 150",           "exact"),
    "Enoxaparin 40":            (9000066, "Enoxaparin 40",            "exact"),
    "Clexane 40":               (9000067, "Clexane 40",               "exact"),
    "Procoralan 5":             (9000069, "Procoralan 5",             "exact"),
    "Sacubitril Valsartan 49/51": (9000070, "Sacubitril Valsartan 49/51", "exact"),
    "Fenofibrate 145":          (9000071, "Fenofibrate 145",          "exact"),
    "Tricor 145":               (9000071, "Fenofibrate 145",          "generic_name"),
    "Nexium 40":                (9000075, "Nexium 40",                "exact"),
    "Lansoprazole 30":          (9000076, "Lansoprazole 30",          "exact"),
    "Famotidine 20":            (9000077, "Famotidine 20",            "exact"),
    "Motilium 10":              (9000078, "Motilium 10",              "exact"),
    "Mosapride 5":              (9000079, "Mosapride 5",              "exact"),
    "Mosid 5":                  (9000080, "Mosid 5",                  "exact"),
    "Duspatalin 135":           (9000081, "Duspatalin 135",           "exact"),
    "Buscopan 10":              (9000082, "Buscopan 10",              "exact"),
    "Dicyclomine 20":           (9000084, "Dicyclomine 20",           "exact"),
    "Gelusil MPS":              (9000085, "Gelusil MPS",              "exact"),
    "Odanset 4":                (9000087, "Odanset 4",                "exact"),
    "Granisetron 1":            (9000088, "Granisetron 1",            "exact"),
    "Kytril 1":                 (9000089, "Kytril 1",                 "exact"),
    "Sucralfate 1g":            (9000090, "Sucralfate 1g",            "exact"),
    "Electrobion":              (9000092, "Electrobion",              "exact"),
    "Softovac":                 (9000093, "Softovac",                 "exact"),
    "Dulcolax 5":               (9000095, "Dulcolax 5",               "exact"),
    "Xifaxan 400":              (9000096, "Xifaxan 400",              "exact"),
    "Asacol 400":               (9000097, "Asacol 400",               "exact"),
    "Urdox 300":                (9000098, "Urdox 300",                "exact"),
    "Silymarin 140":            (9000099, "Silymarin 140",            "exact"),
    "Legalon 140":              (9000100, "Legalon 140",              "exact"),
    "Ventolin Inhaler":         (9000102, "Ventolin Inhaler",         "exact"),
    "Duolin Respules":          (9000103, "Duolin Respules",          "exact"),
    "Ultibro":                  (9000104, "Ultibro",                  "exact"),
    "Anoro Ellipta":            (9000105, "Anoro Ellipta",            "exact"),
    "Pulmicort 200":            (9000106, "Pulmicort 200",            "exact"),
    "Singulair 10":             (9000107, "Singulair 10",             "exact"),
    "Theophylline SR 200":      (9000108, "Theophylline SR 200",      "exact"),
    "Mucosolvan 30":            (9000109, "Mucosolvan 30",            "exact"),
    "Dextromethorphan 10":      (9000110, "Dextromethorphan 10",      "exact"),
    "Codeine Linctus":          (9000111, "Codeine Linctus",          "exact"),
    "Phenytoin 100":            (9000112, "Phenytoin 100",            "exact"),
    "Carbamazepine 200":        (9000113, "Carbamazepine 200",        "exact"),
    "Valproate 500":            (9000114, "Valproate 500",            "exact"),
    "Topamax 25":               (9000115, "Topamax 25",               "exact"),
    "Clonazepam 0.5":           (9000116, "Clonazepam 0.5",           "exact"),
    "Sumatriptan 50":           (9000119, "Sumatriptan 50",           "exact"),
    "Rizatriptan 10":           (9000120, "Rizatriptan 10",           "exact"),
    "Sinemet 110":              (9000121, "Sinemet 110",              "exact"),
    "Donecept 5":               (9000122, "Donecept 5",               "exact"),
    "Aricept 5":                (9000123, "Aricept 5",                "exact"),
    "Rivastigmine 1.5":         (9000124, "Rivastigmine 1.5",         "exact"),
    "Memantine 5":              (9000125, "Memantine 5",              "exact"),
    "Alprazolam 0.25":          (9000128, "Alprazolam 0.25",          "exact"),
    "Etizolam 0.5":             (9000129, "Etizolam 0.5",             "exact"),
    "Zolpidem 10":              (9000130, "Zolpidem 10",              "exact"),
    "Ambien 10":                (9000131, "Ambien 10",                "exact"),
    "Nitrazepam 5":             (9000132, "Nitrazepam 5",             "exact"),
    "Cipralex 10":              (9000136, "Cipralex 10",              "exact"),
    "Venlafaxine 75":           (9000137, "Venlafaxine 75",           "exact"),
    "Effexor XR 75":            (9000138, "Effexor XR 75",            "exact"),
    "Cymbalta 30":              (9000140, "Cymbalta 30",              "exact"),
    "Imipramine 25":            (9000141, "Imipramine 25",            "exact"),
    "Mirtazapine 15":           (9000142, "Mirtazapine 15",           "exact"),
    "Bupropion 150":            (9000143, "Bupropion 150",            "exact"),
    "Wellbutrin 150":           (9000144, "Wellbutrin 150",           "exact"),
    "Seroquel 25":              (9000148, "Seroquel 25",              "exact"),
    "Abilify 10":               (9000150, "Abilify 10",               "exact"),
    "Clozapine 25":             (9000151, "Clozapine 25",             "exact"),
    "Sizoril 25":               (9000152, "Sizoril 25",               "exact"),
    "Buspirone 10":             (9000156, "Buspirone 10",             "exact"),
    "Buspar 10":                (9000157, "Buspar 10",                "exact"),
    "Neomercazole 5":           (9000158, "Neomercazole 5",           "exact"),
    "Carbimazole 5":            (9000159, "Carbimazole 5",            "exact"),
    "Mala-N":                   (9000160, "Mala-N",                   "exact"),
    "Methergine 0.2":           (9000162, "Methergine 0.2",           "exact"),
    "Misoprostol 200":          (9000163, "Misoprostol 200",          "exact"),
    "Cytotec 200":              (9000164, "Cytotec 200",              "exact"),
    "Cialis 20":                (9000169, "Cialis 20",                "exact"),
    "Furesem 40":               (9000170, "Furesem 40",               "exact"),
    "Vesicare 5":               (9000171, "Vesicare 5",               "exact"),
    "Betnovate N Cream":        (9000173, "Betnovate N Cream",        "exact"),
    "Hydrocortisone 1%":        (9000174, "Hydrocortisone 1%",        "exact"),
    "Mupirocin 2%":             (9000175, "Mupirocin 2%",             "exact"),
    "Bactroban 2%":             (9000176, "Bactroban 2%",             "exact"),
    "Retin-A 0.025%":           (9000177, "Retin-A 0.025%",          "exact"),
    "Oratane 20":               (9000179, "Oratane 20",               "exact"),
    "Permethrin 5%":            (9000180, "Permethrin 5%",            "exact"),
    "Lindane 1%":               (9000181, "Lindane 1%",               "exact"),
    "Tacrolimus 0.1%":          (9000183, "Tacrolimus 0.1%",          "exact"),
    "Protopic 0.1%":            (9000184, "Protopic 0.1%",            "exact"),
    "Halobetasol 0.05%":        (9000185, "Halobetasol 0.05%",        "exact"),
    "Clobetasol 0.05%":         (9000186, "Clobetasol 0.05%",         "exact"),
    "Timolol 0.5% Eye":         (9000187, "Timolol 0.5% Eye",         "exact"),
    "Timoptic 0.5%":            (9000188, "Timoptic 0.5%",            "exact"),
    "Latanoprost 0.005%":       (9000189, "Latanoprost 0.005%",       "exact"),
    "Bimatoprost 0.03%":        (9000190, "Bimatoprost 0.03%",        "exact"),
    "Lumigan 0.03%":            (9000191, "Lumigan 0.03%",            "exact"),
    "Dexamethasone Eye":        (9000193, "Dexamethasone Eye",        "exact"),
    "Maxidex 0.1%":             (9000194, "Maxidex 0.1%",             "exact"),
    "Ofloxacin Eye":            (9000195, "Ofloxacin Eye",            "exact"),
    "Exocin 0.3%":              (9000196, "Exocin 0.3%",              "exact"),
    "Sodium Chloride Eye":      (9000197, "Sodium Chloride Eye",      "exact"),
    "Becosules":                (9000198, "Becosules",                "exact"),
    "Methylcobalamin 500":      (9000199, "Methylcobalamin 500",      "exact"),
    "Mecobalamin 500":          (9000200, "Mecobalamin 500",          "exact"),
    "Calcium Carbonate 500":    (9000201, "Calcium Carbonate 500",    "exact"),
    "Caltrate 600":             (9000202, "Caltrate 600",             "exact"),
    "Dexorange Capsule":        (9000203, "Dexorange Capsule",        "exact"),
    "Zincovit":                 (9000204, "Zincovit",                 "exact"),
    "Limcee 500":               (9000205, "Limcee 500",               "exact"),
    "Thiamine 100":             (9000206, "Thiamine 100",             "exact"),
    "Pyridoxine 40":            (9000207, "Pyridoxine 40",            "exact"),
    "Dexamethasone 0.5":        (9000208, "Dexamethasone 0.5",        "exact"),
    "Hydrocortisone 100":       (9000210, "Hydrocortisone 100",       "exact"),
    "Fludrocortisone 0.1":      (9000212, "Fludrocortisone 0.1",      "exact"),
    "Florinef 0.1":             (9000213, "Florinef 0.1",             "exact"),
    "Methotrexate 2.5":         (9000215, "Methotrexate 2.5",         "exact"),
    "Imuran 50":                (9000217, "Imuran 50",                "exact"),
    "Cyclosporine 25":          (9000218, "Cyclosporine 25",          "exact"),
    "Tacrolimus 1":             (9000219, "Tacrolimus 1",             "exact"),
    "Mycophenolate 500":        (9000220, "Mycophenolate 500",        "exact"),
    "Colchicine 0.5":           (9000221, "Colchicine 0.5",           "exact"),
    "Colcibra 0.5":             (9000222, "Colcibra 0.5",             "exact"),
    "Nolvadex 20":              (9000225, "Nolvadex 20",              "exact"),
    "Letrozole 2.5":            (9000226, "Letrozole 2.5",            "exact"),
    "Femara 2.5":               (9000227, "Femara 2.5",               "exact"),
    "Anastrozole 1":            (9000228, "Anastrozole 1",            "exact"),
    "Gleevec 400":              (9000229, "Gleevec 400",              "exact"),
    "Capecitabine 500":         (9000230, "Capecitabine 500",         "exact"),
    "Ondansetron 8":            (9000231, "Ondansetron 8",            "exact"),
    "Zofran 8":                 (9000232, "Zofran 8",                 "exact"),
    "Granisetron 2":            (9000233, "Granisetron 2",            "exact"),
    "Dexamethasone 4":          (9000234, "Dexamethasone 4",          "exact"),
    "Erythropoietin 2000":      (9000235, "Erythropoietin 2000",      "exact"),
    "Hydroxyurea 500":          (9000236, "Hydroxyurea 500",          "exact"),
    "Hydrea 500":               (9000237, "Hydrea 500",               "exact"),
    "Deferoxamine 500":         (9000238, "Deferoxamine 500",         "exact"),
    "Exjade 250":               (9000239, "Exjade 250",               "exact"),
}

# ── SALT VALIDATION ──────────────────────────────────────────────────────────
STOPWORDS = {
    'mg','mcg','ml','iu','and','plus','with','tablet','capsule','syrup',
    'injection','cream','gel','ointment','drops','solution','inhaler',
    'suspension','sachet','powder','sr','er','mr','cr','xr','od','forte',
    'acid','sodium','potassium','calcium','zinc','iron','hydrochloride',
    'sulphate','sulfate','nitrate','citrate','phosphate','acetate',
    'fumarate','maleate','tartrate','disoproxil','fumarate','propionate',
}

def salt_keywords(text: str) -> set[str]:
    if not text:
        return set()
    words = re.sub(r'[^a-zA-Z ]', ' ', text.lower()).split()
    return {w for w in words if w not in STOPWORDS and len(w) >= 4}

def salt_match(db_salt: str, csv_salt: str) -> bool:
    if not db_salt or not csv_salt:
        return True
    db_kw  = salt_keywords(db_salt)
    csv_kw = salt_keywords(csv_salt)
    if not db_kw or not csv_kw:
        return True
    return bool(db_kw & csv_kw)

def best_of(rows, csv_salt=""):
    valid = [r for r in rows if salt_match(r.get("salt_composition", ""), csv_salt)]
    if not valid:
        valid = rows  # fallback: ignore salt check if no valid match
    return min(valid, key=lambda r: (len(r["brand_name"]),
                                     SOURCE_RANK.get(r["match_combination"], 5)))

def strip_num_end(name: str) -> str:
    """Crocin 500 → Crocin, Norflox TZ stays unchanged."""
    c = re.sub(r'\s+[\d.,]+\s*(mg|mcg|ml|g|iu|%)?$', '', name, flags=re.IGNORECASE).strip()
    if c and c.lower() != name.lower():
        return c
    c2 = re.sub(r'\s+[\d.,]+$', '', name).strip()
    return c2 if (c2 and c2.lower() != name.lower()) else ""

def auto_lookup(brand: str, csv_salt: str, exact_map, lower_pairs):
    key = brand.lower()

    def hits_by_prefix(q):
        return [r for (lb, r) in lower_pairs if lb.startswith(q)]

    def pick(hits):
        if not hits:
            return None
        valid = [r for r in hits if salt_match(r.get("salt_composition", ""), csv_salt)]
        pool  = valid or hits
        pool.sort(key=lambda r: (len(r["brand_name"]),
                                  SOURCE_RANK.get(r["match_combination"], 5)))
        return pool[0]

    # 1. Exact
    if key in exact_map:
        r = pick(exact_map[key])
        if r:
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "exact"

    # 2. brand + mg: "Crocin 500" → "crocin 500mg"
    mg_key = re.sub(r'(\d)$', r'\1mg', key.rstrip())
    if mg_key != key:
        r = pick(hits_by_prefix(mg_key))
        if r:
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "mg_prefix"

    # 3. Full brand prefix
    r = pick(hits_by_prefix(key))
    if r:
        return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "prefix"

    # 4. Hyphen: "Norflox TZ" → "norflox-tz"
    parts = brand.split()
    if len(parts) >= 2:
        hyph = (parts[0] + "-" + " ".join(parts[1:])).lower()
        r = pick(hits_by_prefix(hyph))
        if r:
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "hyphen_prefix"

    # 5. Dosage-stripped prefix (must be ≥ 5 chars and salt must match)
    stripped = strip_num_end(brand)
    if stripped and len(stripped) >= 5:
        skey = stripped.lower()
        candidate_hits = hits_by_prefix(skey)
        valid_hits = [r for r in candidate_hits
                      if salt_match(r.get("salt_composition", ""), csv_salt)]
        if valid_hits:
            r = pick(valid_hits)
            return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "stripped_prefix"

        # 5b. Hyphen of stripped: "Moxikind CV 625" → "moxikind-cv"
        sp = stripped.split()
        if len(sp) >= 2:
            hyph2 = (sp[0] + "-" + " ".join(sp[1:])).lower()
            r = pick(hits_by_prefix(hyph2))
            if r:
                return r["drug_id_1mg"], r["brand_name"], r["rxcui"], "hyphen_stripped"

    return None, None, None, "not_found"


async def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found", file=sys.stderr)
        sys.exit(1)

    rows_in = []
    with open(IN_CSV, newline="", encoding="utf-8") as f:
        rows_in = list(csv.DictReader(f))

    # ── 1. Apply manual corrections ──────────────────────────────────────────
    corr_applied = 0
    for row in rows_in:
        brand = row["brand_name_csv"]
        if brand not in CORRECTIONS:
            continue
        drug_id, matched_name, match_type = CORRECTIONS[brand]
        if drug_id is None:
            row["status"]        = "MISSING"
            row["match_type"]    = "not_found"
            row["drug_id_1mg"]   = ""
            row["matched_brand"] = ""
            row["rxcui"]         = ""
        else:
            row["status"]        = "FOUND"
            row["match_type"]    = match_type
            row["drug_id_1mg"]   = str(drug_id)
            row["matched_brand"] = matched_name or ""
            row["rxcui"]         = row.get("rxcui", "")
        corr_applied += 1

    missing_now = [r for r in rows_in if r["status"] == "MISSING"]
    print(f"Manual corrections applied: {corr_applied}")
    print(f"Still missing after corrections: {len(missing_now)}")

    # ── 2. Auto-fill remaining MISSING entries ────────────────────────────────
    # Brands in CORRECTIONS with None are already handled; only fetch DB if there
    # are genuinely uncovered MISSING brands that need auto-lookup.
    needs_autofill = [
        r for r in missing_now
        if r["brand_name_csv"] not in CORRECTIONS
    ]

    auto_filled = 0
    still_missing = 0
    exact_map: dict[str, list] = defaultdict(list)
    lower_pairs = []

    if needs_autofill:
        print(f"Fetching all indian_brand rows for {len(needs_autofill)} uncovered brands...")
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            all_rows = await conn.fetch(
                "SELECT drug_id_1mg, brand_name, salt_composition, rxcui, match_combination "
                "FROM drugdb.indian_brand"
            )
        finally:
            await conn.close()
        print(f"Loaded {len(all_rows)} rows")
        for r in all_rows:
            exact_map[r["brand_name"].lower()].append(dict(r))
        lower_pairs = [(r["brand_name"].lower(), dict(r)) for r in all_rows]
    else:
        print("All MISSING brands covered by CORRECTIONS — skipping DB fetch.")

    for row in rows_in:
        if row["status"] != "MISSING":
            continue

        brand    = row["brand_name_csv"]
        csv_salt = row["salt_csv"]

        # Skip brands already explicitly set to None in CORRECTIONS
        if brand in CORRECTIONS and CORRECTIONS[brand][0] is None:
            still_missing += 1
            continue

        drug_id, matched_name, rxcui, strategy = auto_lookup(
            brand, csv_salt, exact_map, lower_pairs
        )

        if drug_id:
            row["status"]        = "FOUND"
            row["match_type"]    = strategy
            row["drug_id_1mg"]   = str(drug_id)
            row["matched_brand"] = matched_name
            row["rxcui"]         = str(rxcui) if rxcui else ""
            auto_filled += 1
            print(f"  AUTO  #{row['no']:>3}  {brand:<42} → {drug_id}  [{matched_name}]  ({strategy})")
        else:
            still_missing += 1

    # ── 3. Write CSV ─────────────────────────────────────────────────────────
    fieldnames = ["no", "brand_name_csv", "salt_csv", "category",
                  "status", "match_type", "drug_id_1mg", "matched_brand", "rxcui"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_in)

    total       = len(rows_in)
    found_total = sum(1 for r in rows_in if r["status"] == "FOUND")
    miss_total  = sum(1 for r in rows_in if r["status"] == "MISSING")

    print(f"\n{'='*60}")
    print(f"Manual corrections : {corr_applied}")
    print(f"Auto-filled        : {auto_filled}")
    print(f"TOTAL FOUND        : {found_total}/{total}  ({found_total/total*100:.1f}%)")
    print(f"STILL MISSING      : {miss_total}/{total}  ({miss_total/total*100:.1f}%)")

    by_type: dict[str, int] = {}
    for r in rows_in:
        by_type[r["match_type"]] = by_type.get(r["match_type"], 0) + 1
    print("\nBreakdown by match type:")
    for mt, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {mt:<28} : {cnt}")

    print(f"\nUpdated: {OUT_CSV}")

    missing_list = [r for r in rows_in if r["status"] == "MISSING"]
    if missing_list:
        print(f"\nStill missing ({len(missing_list)}):")
        for r in missing_list:
            print(f"  #{r['no']:>3}  {r['brand_name_csv']}")


if __name__ == "__main__":
    asyncio.run(main())
