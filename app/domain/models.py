"""Доменные сущности.

Это чистые dataclass-объекты: слой `domain` не знает ни про SQLAlchemy, ни
про aiogram, ни про FastAPI. Репозитории превращают строки БД в эти
объекты, сервисы работают только с ними, а хэндлеры и роутеры их
форматируют. Так бизнес-логику можно тестировать без БД.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class TxKind(StrEnum):
    """Тип операции. Значения совпадают с Postgres-enum `tx_kind`."""

    expense = "expense"
    income = "income"

    @property
    def sign(self) -> int:
        return -1 if self is TxKind.expense else 1


class DigestMode(StrEnum):
    """Режим дайджеста. Значения совпадают с Postgres-enum `digest_mode`."""

    off = "off"
    daily = "daily"
    weekly = "weekly"


class PeriodKind(StrEnum):
    """Периоды, которые умеют считать отчёты."""

    day = "day"
    week = "week"
    month = "month"
    prev_month = "prev_month"


@dataclass(frozen=True, slots=True)
class User:
    id: int
    tg_id: int
    username: str | None
    base_currency: str
    timezone: str
    digest: DigestMode
    created_at: datetime
    has_api_token: bool = False


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    user_id: int | None  # None — системная категория, видна всем
    kind: TxKind
    name: str
    emoji: str
    keywords: tuple[str, ...] = ()

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int
    user_id: int
    category_id: int
    kind: TxKind
    amount: Decimal
    currency: str
    fx_rate: Decimal
    amount_base: Decimal
    note: str | None
    source: str
    occurred_at: datetime
    created_at: datetime
    deleted_at: datetime | None = None
    category: Category | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True, slots=True)
class Budget:
    id: int
    user_id: int
    category_id: int
    limit_amount: Decimal
    category: Category | None = None


@dataclass(frozen=True, slots=True)
class BudgetProgress:
    """Бюджет вместе с фактическими тратами за текущий календарный месяц."""

    budget: Budget
    category: Category
    spent: Decimal
    currency: str

    @property
    def limit_amount(self) -> Decimal:
        return self.budget.limit_amount

    @property
    def percent(self) -> int:
        """Процент использования, округлённый вниз. Лимит > 0 гарантирован CHECK."""
        return int(self.spent * 100 // self.limit_amount)


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category_id: int
    name: str
    emoji: str
    kind: TxKind
    total: Decimal

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True, slots=True)
class PeriodBounds:
    """Границы периода: полуинтервал [start, end) в UTC + локальные даты."""

    start_utc: datetime
    end_utc: datetime
    start_local: date
    end_local: date  # включительно, для показа пользователю
    timezone: str
    kind: PeriodKind


@dataclass(frozen=True, slots=True)
class Report:
    period: PeriodBounds
    currency: str
    expense_total: Decimal
    income_total: Decimal
    by_category: tuple[CategoryTotal, ...] = ()

    @property
    def balance(self) -> Decimal:
        return self.income_total - self.expense_total

    def share(self, total: Decimal) -> Decimal:
        """Доля категории в расходах периода."""
        if self.expense_total == 0:
            return Decimal(0)
        return total / self.expense_total


@dataclass(frozen=True, slots=True)
class DailyTotal:
    day: date
    expense: Decimal
    income: Decimal


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    base: str
    quote: str
    rate: Decimal
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class Page:
    """Страница курсорной пагинации."""

    items: tuple[Transaction, ...] = ()
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Свежевыпущенный API-токен: открытая часть существует только здесь."""

    token: str
    token_hash: str = field(repr=False)
