"""Движок и фабрика сессий SQLAlchemy.

Один engine на процесс: он держит пул соединений, и создавать его на каждый
запрос означало бы открывать TCP-соединение к Postgres на каждый запрос.
Создаётся в `lifespan` (API) или `on_startup` (worker) и закрывается там же.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


class Database:
    """Владелец engine и фабрики сессий."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 5,
        echo: bool = False,
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=echo,
            # asyncpg кэширует подготовленные выражения на соединение; при работе
            # через pgbouncer в transaction-режиме это ломается, поэтому размер
            # кэша держим управляемым и оставляем префикс имени по умолчанию.
            connect_args={"timeout": 10},
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.sqlalchemy_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=settings.db_echo,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Сессия с гарантированным закрытием.

        Коммит — ответственность сервисного слоя: он знает границы бизнес-
        транзакции. Незакоммиченная работа откатывается при выходе.
        """
        async with self.session_factory() as session:
            yield session

    # ASYNC109: таймаут здесь — часть контракта /ready, внутри он реализован
    # через asyncio.timeout, а не передаётся дальше в вызовы.
    async def healthcheck(self, timeout: float = 1.0) -> bool:  # noqa: ASYNC109
        """`SELECT 1` с таймаутом — для `/ready`."""
        try:
            async with asyncio.timeout(timeout), self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    def pool_stats(self) -> dict[str, int]:
        """Заполненность пула — для метрик."""
        pool = self.engine.pool
        checked_out = getattr(pool, "checkedout", None)
        size = getattr(pool, "size", None)
        overflow = getattr(pool, "overflow", None)
        return {
            "size": size() if callable(size) else 0,
            "checked_out": checked_out() if callable(checked_out) else 0,
            "overflow": overflow() if callable(overflow) else 0,
        }

    async def dispose(self) -> None:
        await self.engine.dispose()
