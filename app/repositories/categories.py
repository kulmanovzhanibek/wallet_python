"""Доступ к категориям: системные (`user_id IS NULL`) и пользовательские."""

from __future__ import annotations

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Category, TxKind
from app.infra import orm


def to_domain(row: orm.Category) -> Category:
    return Category(
        id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        name=row.name,
        emoji=row.emoji,
        keywords=tuple(row.keywords),
    )


class CategoriesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_visible(self, user_id: int, kind: TxKind | None = None) -> list[Category]:
        """Категории, доступные пользователю: системные плюс его собственные.

        Порядок устойчивый — системные сначала: иначе клавиатура в боте
        перетасовывалась бы между вызовами.
        """
        statement = select(orm.Category).where(
            (orm.Category.user_id.is_(None)) | (orm.Category.user_id == user_id)
        )
        if kind is not None:
            statement = statement.where(orm.Category.kind == kind)
        statement = statement.order_by(
            orm.Category.user_id.is_(None).desc(),
            orm.Category.id,
        )
        return [to_domain(row) for row in await self._session.scalars(statement)]

    async def get_visible(self, category_id: int, user_id: int) -> Category | None:
        """Категория, если она системная или принадлежит этому пользователю.

        Проверка владельца — частью запроса, а не после загрузки: так чужую
        категорию нельзя ни прочитать, ни подставить в операцию.
        """
        row = await self._session.scalar(
            select(orm.Category).where(
                orm.Category.id == category_id,
                (orm.Category.user_id.is_(None)) | (orm.Category.user_id == user_id),
            )
        )
        return to_domain(row) if row else None

    async def create(
        self,
        *,
        user_id: int,
        kind: TxKind,
        name: str,
        emoji: str = "📦",
        keywords: tuple[str, ...] = (),
    ) -> Category:
        row = orm.Category(
            user_id=user_id,
            kind=kind,
            name=name,
            emoji=emoji,
            keywords=list(keywords),
        )
        self._session.add(row)
        await self._session.flush()
        return to_domain(row)

    async def has_transactions(self, category_id: int) -> bool:
        """Есть ли операции в категории — включая мягко удалённые.

        Удалённую запись можно вернуть кнопкой «Вернуть», поэтому категория
        под ней всё ещё нужна.
        """
        return bool(
            await self._session.scalar(
                select(exists().where(orm.Transaction.category_id == category_id))
            )
        )

    async def delete_own(self, category_id: int, user_id: int) -> bool:
        """Удаляет только свою категорию. Системные (`user_id IS NULL`) не трогает."""
        deleted = (
            await self._session.scalars(
                delete(orm.Category)
                .where(
                    orm.Category.id == category_id,
                    orm.Category.user_id == user_id,
                )
                .returning(orm.Category.id)
            )
        ).one_or_none()
        return deleted is not None
