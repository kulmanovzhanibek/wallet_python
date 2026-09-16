"""Системные категории с ключевыми словами для парсера.

`user_id IS NULL` — категория общая для всех. Пользователь может добавить
свои, но эти есть сразу после `/start`, иначе первая же «кофе 1500» не
нашла бы, куда записаться.

Список лежит прямо здесь, а не в общей константе: миграция — это снимок
состояния на момент времени, и она не должна менять поведение задним числом,
если константу потом поправят. Ключевые слова сопоставляются по началу слова
без учёта регистра, поэтому «продукт» покрывает и «продукты», и «продуктовый».

Вставка идемпотентна: ON CONFLICT DO NOTHING по частичному уникальному
индексу `uq_categories_system_name` из миграции 0001.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (kind, name, emoji, keywords)
CATEGORIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "expense",
        "Еда",
        "🍔",
        ("еда", "продукт", "магазин", "супермаркет", "хлеб", "молок", "мясо", "овощ", "фрукт"),
    ),
    (
        "expense",
        "Кафе",
        "☕",
        ("кофе", "кафе", "ресторан", "обед", "ужин", "завтрак", "бар", "пицц", "суши", "бургер"),
    ),
    (
        "expense",
        "Транспорт",
        "🚕",
        ("такси", "автобус", "метро", "бензин", "заправк", "парковк", "каршеринг", "поезд"),
    ),
    (
        "expense",
        "Жильё",
        "🏠",
        ("аренда", "квартплат", "коммунал", "ипотек", "свет", "газ", "вода", "отоплен"),
    ),
    (
        "expense",
        "Здоровье",
        "💊",
        ("аптек", "лекарств", "врач", "стоматолог", "клиник", "анализ", "витамин"),
    ),
    ("expense", "Одежда", "👕", ("одежд", "обув", "кроссовк", "футболк", "джинс", "куртк")),
    (
        "expense",
        "Развлечения",
        "🎮",
        ("кино", "театр", "концерт", "игр", "подписк", "книг", "музей", "клуб"),
    ),
    ("expense", "Связь", "📱", ("связь", "телефон", "мобильн", "тариф", "интернет")),
    ("expense", "Образование", "🎓", ("курс", "учеб", "школ", "универ", "репетитор", "тренинг")),
    ("expense", "Красота", "💅", ("салон", "парикмахер", "маникюр", "космет", "стрижк")),
    ("expense", "Подарки", "🎁", ("подар", "цвет", "сувенир")),
    # Категория по умолчанию: ключевых слов нет, в неё попадает всё,
    # что не нашлось по словарю.
    ("expense", "Другое", "📦", ()),
    ("income", "Зарплата", "💰", ("зарплат", "оклад", "аванс", "получк")),
    ("income", "Подработка", "💸", ("подработк", "фриланс", "халтур", "гонорар", "шабашк")),
    ("income", "Проценты", "📈", ("процент", "вклад", "депозит", "дивиденд", "кэшбэк", "кешбэк")),
    ("income", "Подарок", "🎁", ("подар", "презент")),
    ("income", "Другое", "📦", ()),
)


def upgrade() -> None:
    categories = sa.table(
        "categories",
        sa.column("user_id", sa.BigInteger),
        sa.column("kind", postgresql.ENUM(name="tx_kind", create_type=False)),
        sa.column("name", sa.Text),
        sa.column("emoji", sa.Text),
        sa.column("keywords", postgresql.ARRAY(sa.Text)),
    )
    op.execute(
        postgresql.insert(categories)
        .values(
            [
                {
                    "user_id": None,
                    "kind": kind,
                    "name": name,
                    "emoji": emoji,
                    "keywords": list(keywords),
                }
                for kind, name, emoji, keywords in CATEGORIES
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["kind", "name"], index_where=sa.text("user_id IS NULL")
        )
    )


def downgrade() -> None:
    # Удаляем только системные категории и только те, что заводила миграция.
    # Пользовательские (user_id IS NOT NULL) не трогаем.
    op.execute(
        sa.text(
            "DELETE FROM categories WHERE user_id IS NULL AND (kind, name) IN :pairs"
        ).bindparams(
            sa.bindparam(
                "pairs",
                value=[(kind, name) for kind, name, _, _ in CATEGORIES],
                expanding=True,
            )
        )
    )
