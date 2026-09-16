"""Фикстуры интеграционных тестов: настоящий PostgreSQL, настоящие миграции.

Откуда берётся база:

1. `TEST_DATABASE_URL` — если задана, используется она. Так тесты идут в CI
   (там Postgres поднимает GitHub Actions) и локально против `make up`.
2. Иначе поднимается контейнер через testcontainers.

Если ни того, ни другого нет, тесты помечаются как пропущенные — кроме
случая `REQUIRE_INTEGRATION=1`, когда пропуск означал бы, что CI молча
перестал их проверять.

Изоляция: каждый тест работает в собственной транзакции, которая в конце
откатывается. `join_transaction_mode="create_savepoint"` делает так, что
`commit()` внутри кода превращается в освобождение savepoint, а не в
настоящий коммит — данные одного теста не видны другому.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import ENV_CONFIG, to_async_dsn

try:
    from testcontainers.postgres import PostgresContainer
except ImportError:  # pragma: no cover — testcontainers не установлен
    # Импорт под try, а не внутри функции: локальный импорт спотыкается о
    # правило PLC0415, а подавлять его через noqa неудобно — ruff то считает
    # подавление лишним, то снова требует его.
    PostgresContainer = None


class TestDatabaseSettings(BaseSettings):
    """URL тестовой базы из окружения или `.env`.

    Через `pydantic-settings`, а не `os.environ`, чтобы `make test` работал
    сразу после `make up` — так же, как это уже делает `alembic`.
    """

    model_config = ENV_CONFIG

    test_database_url: str | None = None


SKIP_REASON = (
    "нужен PostgreSQL: задайте TEST_DATABASE_URL (например, из `make up`) "
    "или запустите Docker для testcontainers"
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Всё в этом пакете — интеграционные тесты, помечаем автоматически."""
    for item in items:
        item.add_marker(pytest.mark.integration)


def skip_or_fail(reason: str) -> NoReturn:
    """Пропускает тесты локально, но валит прогон в CI.

    `REQUIRE_INTEGRATION=1` защищает от худшего сценария: интеграционные
    тесты молча перестали выполняться, а CI по-прежнему зелёный.
    """
    if os.environ.get("REQUIRE_INTEGRATION") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if url := TestDatabaseSettings().test_database_url:
        yield to_async_dsn(url)
        return

    if PostgresContainer is None:  # pragma: no cover — testcontainers не установлен
        skip_or_fail(SKIP_REASON)

    try:
        with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
            yield container.get_connection_url()
    except Exception as exc:
        skip_or_fail(f"{SKIP_REASON} ({exc.__class__.__name__})")


@pytest.fixture(scope="session", autouse=True)
def migrated(database_url: str) -> Iterator[None]:
    """Применяет миграции на чистую базу один раз за прогон."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    # NullPool: пул на тест не нужен, зато нет соединений, переживающих
    # событийный цикл теста.
    created = create_async_engine(database_url, poolclass=NullPool)
    yield created
    await created.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия в откатываемой транзакции."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async_session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield async_session
        finally:
            await async_session.close()
            await transaction.rollback()
