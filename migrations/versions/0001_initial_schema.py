"""Начальная схема: пользователи, категории, операции, бюджеты, курсы.

Индексов, кроме PRIMARY KEY и UNIQUE, здесь намеренно нет. По ТЗ (раздел 10.3)
сначала снимаются замеры «до» на миллионе операций, и только потом индексы
добавляются отдельной миграцией — иначе неясно, что именно они дали.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Типы создаются и удаляются явно (`create_type=False`).
# SQLAlchemy умеет создавать ENUM сам, вместе с первой использующей его
# таблицей, но `drop_table` такой тип НЕ удаляет. В результате downgrade
# проходит, а следующий upgrade падает с «type tx_kind already exists».
# Поэтому здесь жизненный цикл типов прописан руками.
TX_KIND = postgresql.ENUM("expense", "income", name="tx_kind", create_type=False)
DIGEST_MODE = postgresql.ENUM("off", "daily", "weekly", name="digest_mode", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    TX_KIND.create(bind, checkfirst=True)
    DIGEST_MODE.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("base_currency", sa.CHAR(length=3), server_default="KZT", nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("digest", DIGEST_MODE, server_default="off", nullable=False),
        # Только SHA-256 хэш токена: утечка таблицы не даёт доступа к API.
        # UNIQUE здесь ещё и рабочий индекс — по нему идёт поиск при авторизации.
        sa.Column("api_token_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("tg_id", name="uq_users_tg_id"),
        sa.UniqueConstraint("api_token_hash", name="uq_users_api_token_hash"),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), nullable=False),
        # NULL — системная категория, общая для всех пользователей.
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", TX_KIND, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), server_default="📦", nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_categories_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("user_id", "kind", "name", name="uq_categories_user_name"),
    )
    # UNIQUE (user_id, kind, name) не защищает системные категории: в Postgres
    # два NULL считаются разными значениями, поэтому (NULL, 'expense', 'Еда')
    # вставляется сколько угодно раз — проверено. Частичный уникальный индекс
    # закрывает дыру и делает data-миграцию 0002 идемпотентной.
    op.create_index(
        "uq_categories_system_name",
        "categories",
        ["kind", "name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", TX_KIND, nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        # Курс фиксируется на момент операции и потом не пересчитывается.
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), server_default="1", nullable=False),
        sa.Column("amount_base", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="bot", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Мягкое удаление: запись можно вернуть кнопкой «Вернуть».
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_transactions_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_transactions_category"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
    )

    op.create_table(
        "budgets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("limit_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.CheckConstraint("limit_amount > 0", name="ck_budgets_limit_positive"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_budgets_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_budgets_category"),
        sa.PrimaryKeyConstraint("id", name="pk_budgets"),
        sa.UniqueConstraint("user_id", "category_id", name="uq_budgets_user_category"),
    )

    # Составной PRIMARY KEY — и есть защита от повторных уведомлений:
    # вставка идёт через INSERT … ON CONFLICT DO NOTHING RETURNING, и
    # сообщение отправляется только если строка реально вставилась.
    op.create_table(
        "budget_alerts",
        sa.Column("budget_id", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("threshold", sa.SmallInteger(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"], ["budgets.id"], name="fk_budget_alerts_budget", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("budget_id", "period_start", "threshold", name="pk_budget_alerts"),
    )

    op.create_table(
        "digest_log",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_digest_log_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "local_date", name="pk_digest_log"),
    )

    op.create_table(
        "exchange_rates",
        sa.Column("base", sa.CHAR(length=3), nullable=False),
        sa.Column("quote", sa.CHAR(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("base", "quote", name="pk_exchange_rates"),
    )


def downgrade() -> None:
    # Порядок обратный созданию: сначала зависимые таблицы.
    op.drop_table("exchange_rates")
    op.drop_table("digest_log")
    op.drop_table("budget_alerts")
    op.drop_table("budgets")
    op.drop_table("transactions")
    op.drop_table("categories")
    op.drop_table("users")

    bind = op.get_bind()
    DIGEST_MODE.drop(bind, checkfirst=True)
    TX_KIND.drop(bind, checkfirst=True)
