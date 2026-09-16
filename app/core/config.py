"""Конфигурация приложения.

Все настройки читаются из переменных окружения (или `.env`). Приложение
падает на старте, если обязательная переменная не задана, — это осознанное
решение: лучше не подняться, чем работать с пустым токеном бота.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Currency = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]

SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"KZT", "RUB", "USD", "EUR"})
"""Валюты, которые бот предлагает кнопками. Ограничение продуктовое, не техническое."""

PIVOT_CURRENCY = "USD"
"""Опорная валюта: кросс-курсы считаются через неё."""


class AppEnv(StrEnum):
    dev = "dev"
    test = "test"
    prod = "prod"


class Settings(BaseSettings):
    """Настройки процесса. Один экземпляр на процесс, см. :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = AppEnv.dev
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Telegram ---------------------------------------------------------
    bot_token: SecretStr
    webhook_base_url: str = ""
    webhook_secret: SecretStr = SecretStr("")
    webhook_drop_pending_updates: bool = True
    set_webhook_on_startup: bool = True

    # --- PostgreSQL -------------------------------------------------------
    database_url: PostgresDsn
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_echo: bool = False

    # --- Redis ------------------------------------------------------------
    redis_url: RedisDsn

    # --- Курсы валют ------------------------------------------------------
    fx_api_url: str = "https://open.er-api.com/v6/latest"
    fx_api_key: SecretStr = SecretStr("")
    fx_cache_ttl_seconds: int = Field(default=2 * 60 * 60, ge=60)
    fx_connect_timeout: float = Field(default=2.0, gt=0)
    fx_read_timeout: float = Field(default=5.0, gt=0)

    # --- Продуктовые значения по умолчанию --------------------------------
    default_currency: Currency = "KZT"
    default_timezone: str = "UTC"

    # --- Лимиты (Strong) --------------------------------------------------
    bot_rate_limit_per_minute: int = Field(default=30, ge=1)
    api_rate_limit_per_minute: int = Field(default=120, ge=1)
    telegram_messages_per_second: int = Field(default=25, ge=1, le=30)

    # --- Прочее -----------------------------------------------------------
    idempotency_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60)
    update_dedup_ttl_seconds: int = Field(default=60 * 60, ge=60)
    export_chunk_size: int = Field(default=1_000, ge=100, le=50_000)
    history_page_size: int = Field(default=10, ge=1, le=100)
    undo_window_seconds: int = Field(default=10 * 60, ge=0)
    metrics_enabled: bool = True

    @field_validator("default_currency", mode="after")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        if value not in SUPPORTED_CURRENCIES:
            msg = f"DEFAULT_CURRENCY={value} не входит в {sorted(SUPPORTED_CURRENCIES)}"
            raise ValueError(msg)
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """DSN для SQLAlchemy: обязательно асинхронный драйвер."""
        url = str(self.database_url)
        if "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}/telegram/webhook"

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.prod


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Настройки процесса. Кэшируются, чтобы `.env` читался один раз."""
    return Settings()  # type: ignore[call-arg]  # значения приходят из окружения
