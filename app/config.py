from pydantic_settings import BaseSettings
from pydantic import model_validator
from typing import Optional
from urllib.parse import urlparse


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379"
    API_KEY: str
    SENTRY_DSN: Optional[str] = None
    LOG_LEVEL: str = "INFO"
    CACHE_TTL: int = 86400
    MAX_DB_POOL_SIZE: int = 20
    MIN_DB_POOL_SIZE: int = 5
    ENVIRONMENT: str = "production"

    # Parsed from DATABASE_URL
    DB_HOST: str = ""
    DB_PORT: int = 5432
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""

    # Parsed from REDIS_URL
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @model_validator(mode="after")
    def parse_urls(self):
        db = urlparse(self.DATABASE_URL)
        self.DB_HOST = db.hostname or ""
        self.DB_PORT = db.port or 5432
        self.DB_NAME = (db.path or "").lstrip("/")
        self.DB_USER = db.username or ""
        self.DB_PASSWORD = db.password or ""

        r = urlparse(self.REDIS_URL)
        self.REDIS_HOST = r.hostname or "localhost"
        self.REDIS_PORT = r.port or 6379
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
