"""Конфигурация: без обязательных переменных приложение не должно стартовать."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppEnv, Settings

REQUIRED = {
    "BOT_TOKEN": "123456:test-token",
    "DATABASE_URL": "postgresql+asyncpg://koshelek:koshelek@db:5432/koshelek",
    "REDIS_URL": "redis://redis:6379/0",
}


def build(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> Settings:
    """Собирает Settings из чистого окружения, игнорируя локальный .env."""
    for key in (*REQUIRED, "DEFAULT_CURRENCY", "APP_ENV", "DB_POOL_SIZE", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    env = {**REQUIRED, **overrides}
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestRequiredVariables:
    @pytest.mark.parametrize("missing", sorted(REQUIRED))
    def test_missing_variable_is_fatal(self, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
        with pytest.raises(ValidationError) as exc:
            build(monkeypatch, **{missing: None})
        assert missing.lower() in str(exc.value).lower()

    def test_all_required_present_is_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = build(monkeypatch)
        assert settings.app_env is AppEnv.dev
        assert settings.default_currency == "KZT"
        assert settings.db_pool_size == 10


class TestValidation:
    def test_unknown_default_currency_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError, match="DEFAULT_CURRENCY"):
            build(monkeypatch, DEFAULT_CURRENCY="GBP")

    def test_broken_database_url_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError):
            build(monkeypatch, DATABASE_URL="not-a-dsn")

    def test_pool_size_bounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError):
            build(monkeypatch, DB_POOL_SIZE="0")

    def test_unknown_log_level_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError):
            build(monkeypatch, LOG_LEVEL="TRACE")


class TestDerivedValues:
    def test_sync_dsn_is_upgraded_to_asyncpg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = build(
            monkeypatch, DATABASE_URL="postgresql://koshelek:koshelek@db:5432/koshelek"
        )
        assert settings.sqlalchemy_url.startswith("postgresql+asyncpg://")

    def test_webhook_url_is_built_from_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = build(monkeypatch, WEBHOOK_BASE_URL="https://wallet.example.com/")
        assert settings.webhook_url == "https://wallet.example.com/telegram/webhook"

    def test_secrets_are_not_printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Токен не должен утекать ни в repr, ни в логи через str(settings)."""
        settings = build(monkeypatch)
        assert "123456:test-token" not in repr(settings)
        assert settings.bot_token.get_secret_value() == "123456:test-token"

    def test_is_prod_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert build(monkeypatch, APP_ENV="prod").is_prod
        assert not build(monkeypatch, APP_ENV="dev").is_prod
