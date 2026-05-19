import time
import structlog
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from app.db import get_pool
from app.cache import get_cached, set_cached, build_key
from app.config import settings
from app.services.dosing import get_dosing
from app.models.responses import DrugResponse, MetaResponse, ErrorResponse
from app.exceptions import NoDosingDataException, DrugNotFoundException

router = APIRouter(tags=["dosing"])
logger = structlog.get_logger(__name__)


def classify_age(age_years: float) -> str:
    # Age in years; fractions allowed (e.g. 0.083 = 1 month)
    if age_years < 0.077:      # < ~4 weeks
        return "neonate"
    if age_years < 1:
        return "infant"
    if age_years < 12:
        return "pediatric"
    if age_years < 18:
        return "adolescent"
    if age_years < 65:
        return "adult"
    return "geriatric"


@router.get("/drug/{drug_id_1mg}/dosing-regimen")
async def dosing_regimen(
    drug_id_1mg: str,
    request: Request,
    age: float = Query(..., description="Patient age in years (e.g. 0.5 for 6 months, 5 for 5 years)"),
):
    if age < 0:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error_code="VALIDATION_ERROR",
                message="age must be a non-negative number",
                request_id=str(id(request)),
            ).model_dump(),
        )

    age_group = classify_age(age)

    start = time.perf_counter()
    pool = get_pool()

    cache_key = build_key("dosing", drug_id_1mg, age_group)
    cached = await get_cached(cache_key)
    cached_hit = cached is not None

    if cached_hit:
        data = cached
        generic_name = data[0].get("generic_name") if isinstance(data, list) and data else None
    else:
        try:
            data = await get_dosing(drug_id_1mg, age_group, pool)
            generic_name = data[0].get("generic_name") if data else None
            await set_cached(cache_key, data, ttl=settings.CACHE_TTL)
        except NoDosingDataException as e:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(
                    error_code=e.error_code,
                    message=e.message,
                    request_id=str(id(request)),
                ).model_dump(),
            )
        except DrugNotFoundException as e:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(
                    error_code=e.error_code,
                    message=e.message,
                    request_id=str(id(request)),
                ).model_dump(),
            )
        except Exception as e:
            logger.error("dosing_error", drug_id_1mg=drug_id_1mg, error=str(e), exc_info=True)
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    error_code="DB_ERROR",
                    message=str(e),
                    request_id=str(id(request)),
                ).model_dump(),
            )

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return DrugResponse(
        success=True,
        drug_id_1mg=drug_id_1mg,
        generic_name=generic_name,
        data=data,
        meta=MetaResponse(source="database", cached=cached_hit, response_time_ms=duration_ms),
    )
