import time
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.db import get_pool
from app.cache import get_cached, set_cached, build_key
from app.config import settings
from app.services.resolver import resolve_drug
from app.services.interactions import get_interactions
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
