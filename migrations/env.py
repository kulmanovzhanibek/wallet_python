"""Точка входа Alembic.

URL берётся из `DATABASE_URL`: миграции применяются одноразовым сервисом в
compose и шагом в CI, и обязательный `BOT_TOKEN` им не нужен.
Движок асинхронный (asyncpg), поэтому миграции выполняются внутри
`connection.run_sync`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import DatabaseSettings
from app.infra.orm import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Отсюда autogenerate узнаёт, какой схема должна быть.
target_metadata = Base.metadata


def database_url() -> str:
    """DSN из `DATABASE_URL` или из `.env`, если переменная не выставлена."""
    return DatabaseSettings().sqlalchemy_url  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    """Режим `--sql`: печатает SQL, не подключаясь к базе."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Замечать изменения типов и серверных значений по умолчанию:
        # иначе autogenerate молча пропустит правку NUMERIC(14,2).
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
