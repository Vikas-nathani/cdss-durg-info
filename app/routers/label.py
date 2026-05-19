import time
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.db import get_pool
from app.cache import get_cached, set_cached, build_key
from app.config import settings
from app.services.resolver import resolve_drug
from app.services import label as label_svc
from app.models.responses import DrugResponse, MetaResponse, ErrorResponse
from app.exceptions import DrugNotFoundException, NoFormulationException, NoLabelDataException

router = APIRouter(tags=["label"])
logger = structlog.get_logger(__name__)


async def _label_endpoint(
    drug_id_1mg: str,
    endpoint_name: str,
    extract_fn,
    request: Request,
):
    start = time.perf_counter()
    pool = get_pool()

    try:
        resolved = await resolve_drug(drug_id_1mg, pool)
    except DrugNotFoundException as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error_code=e.error_code,
                message=e.message,
                request_id=str(id(request)),
            ).model_dump(),
        )
    except NoFormulationException as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error_code=e.error_code,
                message=e.message,
                request_id=str(id(request)),
            ).model_dump(),
        )
    except NoLabelDataException as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error_code=e.error_code,
                message=e.message,
                request_id=str(id(request)),
            ).model_dump(),
        )
    except Exception as e:
        logger.error("label_endpoint_error", endpoint=endpoint_name, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error_code="DB_ERROR",
                message=str(e),
                request_id=str(id(request)),
            ).model_dump(),
        )

    cache_key = build_key("label", endpoint_name, resolved.master_linkage_id)
    cached = await get_cached(cache_key)
    cached_hit = cached is not None

    if cached_hit:
        data = cached
    else:
        data = extract_fn(resolved.combined_clean_jsonb or {})
        if data is not None:
            await set_cached(cache_key, data, ttl=settings.CACHE_TTL)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return DrugResponse(
        success=True,
        drug_id_1mg=drug_id_1mg,
        generic_name=resolved.generic_name,
        data=data,
        meta=MetaResponse(source="database", cached=cached_hit, response_time_ms=duration_ms),
    )


@router.get("/drug/{drug_id_1mg}/contraindications")
async def get_contraindications(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "contraindications", label_svc.get_contraindications, request
    )


@router.get("/drug/{drug_id_1mg}/warnings")
async def get_warnings(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "warnings", label_svc.get_warnings, request
    )


@router.get("/drug/{drug_id_1mg}/mechanism-of-action")
async def get_mechanism_of_action(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "mechanism_of_action", label_svc.get_mechanism_of_action, request
    )


@router.get("/drug/{drug_id_1mg}/microbiology")
async def get_microbiology(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "microbiology", label_svc.get_microbiology, request
    )


@router.get("/drug/{drug_id_1mg}/generic-name")
async def get_generic_name(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "generic_name", label_svc.get_generic_name, request
    )


@router.get("/drug/{drug_id_1mg}/patient-info")
async def get_patient_info(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "patient_info", label_svc.get_patient_info, request
    )


@router.get("/drug/{drug_id_1mg}/adverse-reactions")
async def get_adverse_reactions(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "adverse_reactions", label_svc.get_adverse_reactions, request
    )


@router.get("/drug/{drug_id_1mg}/drug-description")
async def get_drug_description(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "drug_description", label_svc.get_drug_description, request
    )


@router.get("/drug/{drug_id_1mg}/indications")
async def get_indications(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "indications", label_svc.get_indications, request
    )


@router.get("/drug/{drug_id_1mg}/geriatric-use")
async def get_geriatric_use(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "geriatric_use", label_svc.get_geriatric_use, request
    )


@router.get("/drug/{drug_id_1mg}/pediatric-use")
async def get_pediatric_use(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "pediatric_use", label_svc.get_pediatric_use, request
    )


@router.get("/drug/{drug_id_1mg}/pregnancy-use")
async def get_pregnancy_use(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "pregnancy_use", label_svc.get_pregnancy_use, request
    )


@router.get("/drug/{drug_id_1mg}/specific-populations")
async def get_specific_populations(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "specific_populations", label_svc.get_specific_populations, request
    )


@router.get("/drug/{drug_id_1mg}/products")
async def get_products(drug_id_1mg: str, request: Request):
    start = time.perf_counter()
    pool = get_pool()
    try:
        resolved = await resolve_drug(drug_id_1mg, pool)
    except DrugNotFoundException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except NoFormulationException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except NoLabelDataException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except Exception as e:
        logger.error("label_endpoint_error", endpoint="products", error=str(e), exc_info=True)
        return JSONResponse(status_code=500, content=ErrorResponse(error_code="DB_ERROR", message=str(e), request_id=str(id(request))).model_dump())

    cache_key = build_key("label", "products", resolved.master_linkage_id)
    cached = await get_cached(cache_key)
    cached_hit = cached is not None
    if cached_hit:
        data = cached
    else:
        data = label_svc.get_products(resolved.combined_clean_jsonb or {})
        await set_cached(cache_key, data, ttl=settings.CACHE_TTL)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return DrugResponse(
        success=True,
        drug_id_1mg=drug_id_1mg,
        generic_name=resolved.generic_name,
        data=data,
        meta=MetaResponse(source="dailymed", cached=cached_hit, response_time_ms=duration_ms, product_count=len(data)),
    )


@router.get("/drug/{drug_id_1mg}/food-interactions")
async def get_food_interactions(drug_id_1mg: str, request: Request):
    return await _label_endpoint(
        drug_id_1mg, "food_interactions", label_svc.get_food_interactions, request
    )


@router.get("/drug/{drug_id_1mg}/ingredients")
async def get_ingredients(drug_id_1mg: str, request: Request):
    start = time.perf_counter()
    pool = get_pool()
    try:
        resolved = await resolve_drug(drug_id_1mg, pool)
    except DrugNotFoundException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except NoFormulationException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except NoLabelDataException as e:
        return JSONResponse(status_code=404, content=ErrorResponse(error_code=e.error_code, message=e.message, request_id=str(id(request))).model_dump())
    except Exception as e:
        logger.error("label_endpoint_error", endpoint="ingredients", error=str(e), exc_info=True)
        return JSONResponse(status_code=500, content=ErrorResponse(error_code="DB_ERROR", message=str(e), request_id=str(id(request))).model_dump())

    cache_key = build_key("label", "ingredients", resolved.master_linkage_id)
    cached = await get_cached(cache_key)
    cached_hit = cached is not None
    if cached_hit:
        data = cached
    else:
        data = label_svc.get_ingredients(resolved.combined_clean_jsonb or {})
        await set_cached(cache_key, data, ttl=settings.CACHE_TTL)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    product_count = len(data.get("active", []))
    return DrugResponse(
        success=True,
        drug_id_1mg=drug_id_1mg,
        generic_name=resolved.generic_name,
        data=data,
        meta=MetaResponse(source="dailymed", cached=cached_hit, response_time_ms=duration_ms, product_count=product_count),
    )
