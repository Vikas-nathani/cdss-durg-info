import structlog
from typing import Any, Optional

logger = structlog.get_logger(__name__)


def _safe_get(data: dict, *keys) -> Optional[Any]:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            logger.info("label_path_missing", missing_at=key, path=keys)
            return None
        current = current.get(key)
        if current is None:
            logger.info("label_path_missing", missing_at=key, path=keys)
            return None
    return current


def _extract_rich(section) -> dict:
    """Extract text, table, subsections from an openFDA section object or plain string."""
    if section is None:
        return {"text": None, "table": None, "subsections": None}
    if isinstance(section, str):
        return {"text": section or None, "table": None, "subsections": None}
    if not isinstance(section, dict):
        return {"text": None, "table": None, "subsections": None}
    text_raw = section.get("text")
    if isinstance(text_raw, list):
        parts = [str(t).strip() for t in text_raw if t]
        if not parts:
            text = None
        elif len(parts) == 1:
            text = parts[0] or None
        else:
            # Multiple list items occur when several product labels (different
            # manufacturers / formulations) exist for the same drug. Their
            # warnings are nearly identical but not byte-for-byte equal.
            # If any two items share the same first 50 chars they are the same
            # section repeated from different labels → keep the longest one.
            # If they have distinct openings they are genuinely separate
            # sections → join them.
            prefixes = [p[:50] for p in parts]
            if len(set(prefixes)) < len(parts):
                text = max(parts, key=len) or None
            else:
                text = "\n".join(parts) or None
    elif isinstance(text_raw, str):
        text = text_raw or None
    else:
        text = None
    table_raw = section.get("table")
    table = table_raw if isinstance(table_raw, list) and table_raw else None
    subs_raw = section.get("subsections")
    subsections = subs_raw if isinstance(subs_raw, list) and subs_raw else None
    return {"text": text, "table": table, "subsections": subsections}


def _extract_dailymed(section) -> dict:
    """Extract from DailyMed section: content→text, no table, optional subsections."""
    if not isinstance(section, dict):
        return {"text": None, "table": None, "subsections": None}
    text = section.get("content") or None
    subs_raw = section.get("subsections")
    subsections = subs_raw if isinstance(subs_raw, list) and subs_raw else None
    return {"text": text, "table": None, "subsections": subsections}


def _has_content(result: dict) -> bool:
    return bool(result.get("text") or result.get("table") or result.get("subsections"))


def get_contraindications(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "safety", "contraindications")
    result = _extract_rich(section)
    if not _has_content(result):
        dm_section = _safe_get(jsonb, "dailymed", "safety", "contraindications")
        result = _extract_dailymed(dm_section)
    return result


def get_warnings(jsonb: dict) -> dict:
    openfda_safety = _safe_get(jsonb, "openfda", "safety") or {}
    for key in ("warnings_and_cautions", "warnings", "boxed_warning"):
        section = openfda_safety.get(key)
        if section:
            result = _extract_rich(section)
            if _has_content(result):
                return result
    dailymed_safety = _safe_get(jsonb, "dailymed", "safety") or {}
    for key in ("warnings_and_precautions", "warnings", "boxed_warning"):
        section = dailymed_safety.get(key)
        if section:
            result = _extract_dailymed(section)
            if _has_content(result):
                return result
    return {"text": None, "table": None, "subsections": None}


def get_mechanism_of_action(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "clinical", "mechanism_of_action")
    return _extract_rich(section)


def get_microbiology(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "clinical", "microbiology")
    return _extract_rich(section)


def get_generic_name(jsonb: dict) -> dict:
    value = _safe_get(jsonb, "openfda", "drug_info", "generic_name")
    if value is None:
        products = _safe_get(jsonb, "dailymed", "drug_info", "products")
        if isinstance(products, list) and products:
            value = products[0].get("generic_name")
    return {"text": value if isinstance(value, str) else None, "table": None, "subsections": None}


def get_patient_info(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "patient_info", "information_for_patients")
    result = _extract_rich(section)
    if not _has_content(result):
        dm_section = _safe_get(jsonb, "dailymed", "patient_info", "information_for_patients")
        result = _extract_dailymed(dm_section)
    return result


def get_adverse_reactions(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "adverse_events", "adverse_reactions")
    result = _extract_rich(section)
    if not _has_content(result):
        dm_section = _safe_get(jsonb, "dailymed", "adverse_events", "adverse_reactions")
        result = _extract_dailymed(dm_section)
    return result


def get_drug_description(jsonb: dict) -> dict:
    value = _safe_get(jsonb, "openfda", "labeling_content", "drug_description")
    return {"text": value if isinstance(value, str) else None, "table": None, "subsections": None}


def get_indications(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "labeling_content", "indications_and_usage")
    result = _extract_rich(section)
    if not _has_content(result):
        dm_section = _safe_get(jsonb, "dailymed", "labeling_content", "indications_and_usage")
        result = _extract_dailymed(dm_section)
    return result


def get_population_info(jsonb: dict, age: int) -> dict:
    if age < 18:
        category = "pediatric"
        section = _safe_get(jsonb, "openfda", "population_specific", "pediatric_use")
        result = _extract_rich(section)
        source = "openfda"
        if not _has_content(result):
            dm_section = _safe_get(jsonb, "dailymed", "population_specific", "pediatric_use")
            result = _extract_dailymed(dm_section)
            source = "dailymed" if _has_content(result) else None
    elif age >= 65:
        category = "geriatric"
        section = _safe_get(jsonb, "openfda", "population_specific", "geriatric_use")
        result = _extract_rich(section)
        source = "openfda"
        if not _has_content(result):
            dm_section = _safe_get(jsonb, "dailymed", "population_specific", "geriatric_use")
            result = _extract_dailymed(dm_section)
            source = "dailymed" if _has_content(result) else None
    else:
        category = "adult"
        result = {"text": None, "table": None, "subsections": None}
        source = None
    return {
        "population_category": category,
        "text": result.get("text"),
        "table": result.get("table"),
        "subsections": result.get("subsections"),
        "source": source,
    }


def get_pregnancy_use(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "population_specific", "use_in_pregnancy")
    result = _extract_rich(section)
    if not _has_content(result):
        dm_section = _safe_get(jsonb, "dailymed", "population_specific", "teratogenic_effects")
        result = _extract_dailymed(dm_section)
    return result


def get_specific_populations(jsonb: dict) -> dict:
    section = _safe_get(jsonb, "openfda", "population_specific", "use_in_specific_populations")
    return _extract_rich(section)


def get_products(jsonb: dict) -> list:
    products = _safe_get(jsonb, "dailymed", "drug_info", "products")
    if not isinstance(products, list):
        return []
    result = []
    for p in products:
        if not isinstance(p, dict):
            continue
        pc = p.get("physical_characteristics") or {}
        result.append({
            "generic_name": p.get("generic_name") or "",
            "dosage_form": p.get("dosage_form"),
            "route_of_administration": p.get("route_of_administration"),
            "color": pc.get("color"),
            "shape": pc.get("shape"),
            "imprint": pc.get("imprint"),
            "size_mm": pc.get("size_mm"),
        })
    return result


def _fi_to_str(item) -> str | None:
    if isinstance(item, dict):
        return item.get("text") or item.get("content") or None
    return str(item) if item is not None else None


def get_food_interactions(jsonb: dict) -> dict:
    drugbank = jsonb.get("drugbank")
    if not isinstance(drugbank, list):
        return {"text": None, "table": None, "subsections": None}
    items = []
    for entry in drugbank:
        if not isinstance(entry, dict):
            continue
        fi = entry.get("drug_interactions", {}).get("food_interactions")
        if isinstance(fi, list):
            for item in fi:
                s = _fi_to_str(item)
                if s:
                    items.append(s)
        elif fi is not None:
            s = _fi_to_str(fi)
            if s:
                items.append(s)
    if not items:
        return {"text": None, "table": None, "subsections": None}
    return {"text": "\n".join(items), "table": None, "subsections": None}


def get_ingredients(jsonb: dict) -> dict:
    products = _safe_get(jsonb, "dailymed", "drug_info", "products")
    if not isinstance(products, list):
        return {"active": [], "inactive": []}
    active = []
    inactive = []
    for p in products:
        if not isinstance(p, dict):
            continue
        product_name = p.get("generic_name") or ""
        ai = p.get("active_ingredients")
        active.append({
            "product": product_name,
            "ingredients": ai if isinstance(ai, list) else [],
        })
        ii = p.get("inactive_ingredients")
        inactive.append({
            "product": product_name,
            "ingredients": ii if isinstance(ii, list) else [],
        })
    return {"active": active, "inactive": inactive}
