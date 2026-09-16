"""Таблицы SQLAlchemy — один в один со схемой из ТЗ (раздел 6).

Это слой инфраструктуры: он знает про Postgres, а доменный слой про него —
нет. Репозитории превращают эти строки в dataclass-сущности из
`app.domain.models`.

Индексов здесь намеренно нет, кроме `PRIMARY KEY` и `UNIQUE`: по ТЗ сначала
снимаются замеры «до», и только потом индексы добавляются отдельной
миграцией. Так видно, что именно они дали.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.models import DigestMode, TxKind


def pg_enum(enum_cls: type[TxKind] | type[DigestMode], name: str) -> Enum:
    """Postgres-ENUM с *значениями* Python-перечисления, а не именами.

    Без `values_callable` SQLAlchemy положил бы в тип имена членов
    (`expense` совпадает, но для `DigestMode.off` это было бы `off` только
    случайно) — фиксируем явно, чтобы БД и код не разъехались.
    """
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda enum: [member.value for member in enum],
        native_enum=True,
    )


class Base(DeclarativeBase):
    """Общая база: от неё берётся `metadata` для Alembic."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="KZT")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    digest: Mapped[DigestMode] = mapped_column(
        pg_enum(DigestMode, "digest_mode"), nullable=False, server_default="off"
    )
    # Храним только SHA-256 хэш: утечка таблицы не даёт доступа к API.
    api_token_hash: Mapped[str | None] = mapped_column(CHAR(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "name", name="uq_categories_user_name"),
        # UNIQUE выше не защищает системные категории: в Postgres два NULL
        # считаются разными значениями, поэтому (NULL, 'expense', 'Еда')
        # вставляется сколько угодно раз. Частичный уникальный индекс
        # закрывает это и заодно делает data-миграцию идемпотентной —
        # по нему работает ON CONFLICT DO NOTHING.
        Index(
            "uq_categories_system_name",
            "kind",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # NULL — системная категория, видна всем пользователям.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[TxKind] = mapped_column(pg_enum(TxKind, "tx_kind"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(Text, nullable=False, server_default="📦")
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id"), nullable=False
    )
    kind: Mapped[TxKind] = mapped_column(pg_enum(TxKind, "tx_kind"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    # Курс на момент операции. Меняя основную валюту, старые операции не
    # пересчитываем — см. docs/adr/003.
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, server_default="1")
    amount_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="bot")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Мягкое удаление: запись можно вернуть кнопкой «Вернуть».
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_budgets_user_category"),
        CheckConstraint("limit_amount > 0", name="ck_budgets_limit_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id"), nullable=False
    )
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class BudgetAlert(Base):
    """Отправленные уведомления о бюджете.

    Составной PRIMARY KEY — и есть защита от дублей: вставка идёт через
    `INSERT … ON CONFLICT DO NOTHING RETURNING`, и сообщение уходит
    пользователю только если строка реально вставилась.
    """

    __tablename__ = "budget_alerts"

    budget_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("budgets.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(primary_key=True)
    threshold: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 80 или 100
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DigestLog(Base):
    """Отправленные дайджесты: тот же приём, что и с `budget_alerts`."""

    __tablename__ = "digest_log"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    local_date: Mapped[date] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    base: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    quote: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "Base",
    "Budget",
    "BudgetAlert",
    "Category",
    "DigestLog",
    "ExchangeRate",
    "Transaction",
    "User",
]
