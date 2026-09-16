"""Миграции: применяются на чистую базу, откатываются и применяются снова."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

EXPECTED_TABLES = {
    "users",
    "categories",
    "transactions",
    "budgets",
    "budget_alerts",
    "digest_log",
    "exchange_rates",
}


class TestSchema:
    async def test_all_tables_exist(self, session: AsyncSession) -> None:
        rows = await session.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        assert set(rows) >= EXPECTED_TABLES

    async def test_money_columns_are_numeric_not_float(self, session: AsyncSession) -> None:
        """Деньги в БД — NUMERIC. double precision здесь означал бы потерю копеек."""
        rows = await session.execute(
            text("""
                SELECT column_name, data_type, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_name = 'transactions'
                  AND column_name IN ('amount', 'amount_base', 'fx_rate')
                ORDER BY column_name
            """)
        )
        assert [tuple(row) for row in rows] == [
            ("amount", "numeric", 14, 2),
            ("amount_base", "numeric", 14, 2),
            ("fx_rate", "numeric", 18, 8),
        ]

    async def test_timestamps_carry_timezone(self, session: AsyncSession) -> None:
        rows = await session.scalars(
            text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'transactions'
                  AND column_name IN ('occurred_at', 'created_at', 'deleted_at')
            """)
        )
        assert set(rows) == {"timestamp with time zone"}

    async def test_transactions_have_no_extra_indexes_yet(self, session: AsyncSession) -> None:
        """Раздел 10.3 ТЗ: сначала замеры без индексов, потом индексы."""
        rows = await session.scalars(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'transactions'")
        )
        assert set(rows) == {"pk_transactions"}

    async def test_enum_types_exist(self, session: AsyncSession) -> None:
        rows = await session.scalars(
            text("SELECT typname FROM pg_type WHERE typname IN ('tx_kind', 'digest_mode')")
        )
        assert set(rows) == {"tx_kind", "digest_mode"}


class TestConstraints:
    async def test_negative_amount_is_rejected(self, session: AsyncSession) -> None:
        await session.execute(text("DELETE FROM users"))
        user_id = await session.scalar(
            text("INSERT INTO users (tg_id) VALUES (900001) RETURNING id")
        )
        category_id = await session.scalar(
            text("SELECT id FROM categories WHERE user_id IS NULL AND kind = 'expense' LIMIT 1")
        )
        with pytest.raises(Exception, match="ck_transactions_amount_positive"):
            await session.execute(
                text("""
                    INSERT INTO transactions
                        (user_id, category_id, kind, amount, currency, amount_base, occurred_at)
                    VALUES (:u, :c, 'expense', -100, 'KZT', -100, now())
                """),
                {"u": user_id, "c": category_id},
            )

    async def test_duplicate_system_category_is_rejected(self, session: AsyncSession) -> None:
        """UNIQUE (user_id, kind, name) этого не ловит: два NULL в Postgres различны."""
        with pytest.raises(Exception, match="uq_categories_system_name"):
            await session.execute(
                text("""
                    INSERT INTO categories (user_id, kind, name, emoji)
                    VALUES (NULL, 'expense', 'Еда', '🍔')
                """)
            )


class TestSystemCategories:
    async def test_created_by_data_migration(self, session: AsyncSession) -> None:
        count = await session.scalar(text("SELECT count(*) FROM categories WHERE user_id IS NULL"))
        assert count == 17

    @pytest.mark.parametrize("kind", ["expense", "income"])
    async def test_fallback_category_exists_for_each_kind(
        self, session: AsyncSession, kind: str
    ) -> None:
        """Категория «Другое» обязана быть: в неё падает всё неопознанное парсером."""
        found = await session.scalar(
            text("""
                SELECT count(*) FROM categories
                WHERE user_id IS NULL AND kind = :kind AND name = 'Другое'
            """),
            {"kind": kind},
        )
        assert found == 1

    async def test_keywords_cover_the_spec_examples(self, session: AsyncSession) -> None:
        """Примеры из таблицы ТЗ должны находить категорию по словарю из миграции."""
        rows = await session.execute(
            text("""
                SELECT name, keywords FROM categories
                WHERE user_id IS NULL AND kind = 'expense'
            """)
        )
        keywords = {name: set(words) for name, words in rows}
        assert "кофе" in keywords["Кафе"]
        assert "обед" in keywords["Кафе"]
        assert "такси" in keywords["Транспорт"]
        assert "продукт" in keywords["Еда"]
        assert keywords["Другое"] == set()


class TestUpgradeDowngradeCycle:
    """ТЗ: миграции применяются на чистую БД и откатываются.

    Цикл прогоняется на отдельной базе, чтобы не мешать остальным тестам.
    """

    @pytest.fixture
    async def scratch_url(self, engine: AsyncEngine, database_url: str) -> AsyncIterator[str]:
        name = "koshelek_migration_cycle_test"
        # CREATE DATABASE нельзя выполнить внутри транзакции.
        autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with autocommit.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        url = database_url.rsplit("/", 1)[0] + f"/{name}"
        try:
            yield url
        finally:
            async with autocommit.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))

    @staticmethod
    async def _alembic(config: Config, direction: str, revision: str) -> None:
        """Запускает alembic в отдельном потоке.

        `migrations/env.py` вызывает `asyncio.run`, а мы уже внутри
        работающего событийного цикла — в потоке своего цикла нет, и всё
        сходится.
        """
        action = command.upgrade if direction == "up" else command.downgrade
        await asyncio.to_thread(action, config, revision)

    async def test_upgrade_downgrade_upgrade(
        self, scratch_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(os.environ, "DATABASE_URL", scratch_url)
        config = Config("alembic.ini")

        await self._alembic(config, "up", "head")
        assert await self._tables(scratch_url) >= EXPECTED_TABLES

        await self._alembic(config, "down", "base")
        assert await self._tables(scratch_url) == {"alembic_version"}
        # Типы ENUM тоже должны уйти, иначе следующий upgrade упадёт с
        # «type tx_kind already exists» — именно это и проверяет второй upgrade.
        assert await self._enum_types(scratch_url) == set()

        await self._alembic(config, "up", "head")
        assert await self._tables(scratch_url) >= EXPECTED_TABLES

    @staticmethod
    async def _tables(url: str) -> set[str]:
        created = create_async_engine(url, poolclass=NullPool)
        try:
            async with created.connect() as connection:
                rows = await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                return set(rows)
        finally:
            await created.dispose()

    @staticmethod
    async def _enum_types(url: str) -> set[str]:
        created = create_async_engine(url, poolclass=NullPool)
        try:
            async with created.connect() as connection:
                rows = await connection.scalars(
                    text("SELECT typname FROM pg_type WHERE typname IN ('tx_kind','digest_mode')")
                )
                return set(rows)
        finally:
            await created.dispose()
