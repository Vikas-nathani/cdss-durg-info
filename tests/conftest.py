import pytest
import pytest_asyncio
import httpx
from app.config import settings
from app.db import create_pool, close_pool, get_pool
from app.cache import create_redis, close_redis
from app import cache as cache_module
import main as main_module

app = main_module.app


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _startup():
    """Start DB pool and Redis once per session on the shared session event loop."""
    await create_pool()
    try:
        await create_redis()
    except Exception:
        pass
    yield
    try:
        await close_pool()
    except Exception:
        pass
    try:
        await close_redis()
    except Exception:
        pass


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(_startup):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_pool(_startup):
    return get_pool()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_client(_startup):
    return cache_module._redis


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def valid_drug_id(db_pool):
    """
    Find a drug_id_1mg that fully resolves through all 3 resolver steps by
    mimicking the resolver's exact LIMIT-1 query chain.
    """
    from app.services.resolver import resolve_drug
    from app.exceptions import DrugNotFoundException, NoFormulationException, NoLabelDataException

    async with db_pool.acquire() as conn:
        candidates = await conn.fetch(
            """
            SELECT ib.drug_id_1mg
            FROM drugdb.indian_brand ib
            WHERE ib.match_combination NOT IN ('drugbank', 'us_unapproved')
              AND ib.rxcui IS NOT NULL
            LIMIT 50
            """
        )

    for row in candidates:
        drug_id = row["drug_id_1mg"]
        try:
            await resolve_drug(drug_id, db_pool)
            return drug_id
        except (DrugNotFoundException, NoFormulationException, NoLabelDataException):
            continue
        except Exception:
            continue

    return "803103"  # known-good fallback


@pytest.fixture
def invalid_drug_id():
    return "INVALID_999999"


API_KEY = settings.API_KEY
