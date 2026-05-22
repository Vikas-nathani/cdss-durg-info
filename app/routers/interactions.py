import time
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.db import get_pool
from app.cache import get_cached, set_cached, build_key
from app.config import settings
from app.services.resolver import resolve_drug
from app.services.interactions import get_interactions, check_drug_interaction
from app.models.responses import DrugResponse, MetaResponse, ErrorResponse
from app.exceptions import DrugNotFoundException, NoFormulationException, NoLabelDataException

router = APIRouter(tags=["interactions"])
logger = structlog.get_logger(__name__)


@router.get("/drug/{drug_id_1mg}/interactions")
async def drug_interactions(drug_id_1mg: str, request: Request):
    start = time.perf_counter()
    pool = get_pool()

    try:
        resolved = await resolve_drug(drug_id_1mg, pool)
    except (DrugNotFoundException, NoFormulationException, NoLabelDataException) as e:
        return JSONResponse(
            status_code=e.status_code,
            content=ErrorResponse(
                error_code=e.error_code,
                message=e.message,
                request_id=str(id(request)),
            ).model_dump(),
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error_code="DB_ERROR",
                message=str(e),
                request_id=str(id(request)),
            ).model_dump(),
        )

    cache_key = build_key("interactions", resolved.formulation_id)
    cached = await get_cached(cache_key)
    cached_hit = cached is not None

    if cached_hit:
        result = cached
    else:
        result = await get_interactions(resolved.formulation_id, pool)
        await set_cached(cache_key, result, ttl=settings.CACHE_TTL)

    severity_counts = result.get("severity_counts", {})
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    meta = MetaResponse(source="database", cached=cached_hit, response_time_ms=duration_ms)
    meta_dict = meta.model_dump()
    meta_dict["severity_counts"] = severity_counts

    return {
        "success": True,
        "drug_id_1mg": drug_id_1mg,
        "generic_name": resolved.generic_name,
        "data": result.get("interactions", []),
        "meta": meta_dict,
    }


@router.get("/drug/{drug_id_1mg}/check-interaction/{other_drug_id}")
async def check_interaction_between_drugs(
    drug_id_1mg: str,
    other_drug_id: str,
    request: Request,
):
    start = time.perf_counter()
    pool = get_pool()

    # Resolve both drugs in parallel
    try:
        import asyncio
        resolved1, resolved2 = await asyncio.gather(
            resolve_drug(drug_id_1mg, pool),
            resolve_drug(other_drug_id, pool),
        )
    except (DrugNotFoundException, NoFormulationException, NoLabelDataException) as e:
        return JSONResponse(
            status_code=e.status_code,
            content=ErrorResponse(
                error_code=e.error_code,
                message=e.message,
                request_id=str(id(request)),
            ).model_dump(),
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error_code="DB_ERROR",
                message=str(e),
                request_id=str(id(request)),
            ).model_dump(),
        )

    # Cache key is order-independent (sort so drug1+drug2 == drug2+drug1)
    sorted_ids = sorted([resolved1.formulation_id, resolved2.formulation_id])
    cache_key = build_key("check_interaction", sorted_ids[0], sorted_ids[1])
    cached = await get_cached(cache_key)
    cached_hit = cached is not None

    try:
        if cached_hit:
            result = cached
        else:
            result = await check_drug_interaction(
                resolved1.formulation_id, resolved2.formulation_id, pool
            )
            await set_cached(cache_key, result, ttl=settings.CACHE_TTL)
    except Exception as e:
        logger.error("check_interaction_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error_code="DB_ERROR",
                message=str(e),
                request_id=str(id(request)),
            ).model_dump(),
        )

    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    return {
        "success": True,
        "drug_1": {
            "drug_id_1mg": drug_id_1mg,
            "generic_name": resolved1.generic_name,
        },
        "drug_2": {
            "drug_id_1mg": other_drug_id,
            "generic_name": resolved2.generic_name,
        },
        "has_interaction": result["has_interaction"],
        "highest_severity": result["highest_severity"],
        "severity_summary": result["severity_counts"],
        "data": result["interactions"],
        "meta": {
            "source": "database",
            "cached": cached_hit,
            "response_time_ms": duration_ms,
        },
    }
