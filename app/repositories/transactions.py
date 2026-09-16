"""Доступ к операциям.

Каждый метод, который достаёт или меняет запись, фильтрует по `user_id`
внутри SQL, а не после загрузки: подменённый `callback_data` или чужой
`id` в API не должны давать доступ к записи другого пользователя.

О пагинации: сейчас она через `OFFSET` — это намеренно «версия до» из
раздела 10.3 ТЗ. На ней снимаются замеры на миллионе операций, после чего
добавляется индекс `(user_id, occurred_at DESC, id DESC)` и пагинация
переводится на keyset. Сравнение попадёт в `docs/perf.md` и ADR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Transaction, TxKind
from app.infra import orm
from app.repositories.categories import to_domain as category_to_domain


def to_domain(row: orm.Transaction, category: orm.Category | None = None) -> Transaction:
    return Transaction(
        id=row.id,
        user_id=row.user_id,
        category_id=row.category_id,
        kind=row.kind,
        amount=row.amount,
        currency=row.currency,
        fx_rate=row.fx_rate,
        amount_base=row.amount_base,
        note=row.note,
        source=row.source,
        occurred_at=row.occurred_at,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        category=category_to_domain(category) if category else None,
    )


class TransactionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        category_id: int,
        kind: TxKind,
        amount: Decimal,
        currency: str,
        fx_rate: Decimal,
        amount_base: Decimal,
        note: str | None,
        occurred_at: datetime,
        source: str = "bot",
    ) -> Transaction:
        row = orm.Transaction(
            user_id=user_id,
            category_id=category_id,
            kind=kind,
            amount=amount,
            currency=currency,
            fx_rate=fx_rate,
            amount_base=amount_base,
            note=note,
            occurred_at=occurred_at,
            source=source,
        )
        self._session.add(row)
        await self._session.flush()
        return to_domain(row)

    def _owned(self, transaction_id: int, user_id: int) -> Select[tuple[orm.Transaction]]:
        return select(orm.Transaction).where(
            orm.Transaction.id == transaction_id,
            orm.Transaction.user_id == user_id,
        )

    async def get(
        self, transaction_id: int, user_id: int, *, include_deleted: bool = False
    ) -> Transaction | None:
        statement = self._owned(transaction_id, user_id)
        if not include_deleted:
            statement = statement.where(orm.Transaction.deleted_at.is_(None))
        row = await self._session.scalar(statement)
        return to_domain(row) if row else None

    async def get_with_category(
        self, transaction_id: int, user_id: int, *, include_deleted: bool = False
    ) -> Transaction | None:
        """Операция вместе с категорией — для показа карточки в боте."""
        statement = (
            select(orm.Transaction, orm.Category)
            .join(orm.Category, orm.Category.id == orm.Transaction.category_id)
            .where(
                orm.Transaction.id == transaction_id,
                orm.Transaction.user_id == user_id,
            )
        )
        if not include_deleted:
            statement = statement.where(orm.Transaction.deleted_at.is_(None))
        row = (await self._session.execute(statement)).first()
        return to_domain(row[0], row[1]) if row else None

    async def set_category(
        self, transaction_id: int, user_id: int, category_id: int
    ) -> Transaction | None:
        """Меняет категорию. Возвращает None, если записи нет или она чужая."""
        row = (
            await self._session.scalars(
                update(orm.Transaction)
                .where(
                    orm.Transaction.id == transaction_id,
                    orm.Transaction.user_id == user_id,
                    orm.Transaction.deleted_at.is_(None),
                )
                .values(category_id=category_id)
                .returning(orm.Transaction)
            )
        ).one_or_none()
        return to_domain(row) if row else None

    async def soft_delete(
        self, transaction_id: int, user_id: int, *, now: datetime | None = None
    ) -> Transaction | None:
        """Мягкое удаление. Повторный вызов ничего не меняет и вернёт None."""
        row = (
            await self._session.scalars(
                update(orm.Transaction)
                .where(
                    orm.Transaction.id == transaction_id,
                    orm.Transaction.user_id == user_id,
                    orm.Transaction.deleted_at.is_(None),
                )
                .values(deleted_at=now or datetime.now(UTC))
                .returning(orm.Transaction)
            )
        ).one_or_none()
        return to_domain(row) if row else None

    async def restore(self, transaction_id: int, user_id: int) -> Transaction | None:
        """Возвращает мягко удалённую запись. Кнопка «Вернуть» в боте."""
        row = (
            await self._session.scalars(
                update(orm.Transaction)
                .where(
                    orm.Transaction.id == transaction_id,
                    orm.Transaction.user_id == user_id,
                    orm.Transaction.deleted_at.is_not(None),
                )
                .values(deleted_at=None)
                .returning(orm.Transaction)
            )
        ).one_or_none()
        return to_domain(row) if row else None

    async def list_page_offset(
        self,
        user_id: int,
        *,
        limit: int = 10,
        offset: int = 0,
        kind: TxKind | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Transaction]:
        """Страница истории через `OFFSET` — «версия до» из раздела 10.3 ТЗ.

        Недостаток виден на больших смещениях: чтобы отдать 500-ю страницу,
        Postgres всё равно читает и отбрасывает 5000 строк. На этапе замеров
        это заменяется на keyset.
        """
        statement = (
            select(orm.Transaction, orm.Category)
            .join(orm.Category, orm.Category.id == orm.Transaction.category_id)
            .where(orm.Transaction.user_id == user_id, orm.Transaction.deleted_at.is_(None))
        )
        if kind is not None:
            statement = statement.where(orm.Transaction.kind == kind)
        if since is not None:
            statement = statement.where(orm.Transaction.occurred_at >= since)
        if until is not None:
            statement = statement.where(orm.Transaction.occurred_at < until)

        # id в сортировке обязателен: без него операции с одинаковым
        # occurred_at могут менять порядок между страницами, и запись
        # либо продублируется, либо пропадёт.
        statement = (
            statement.order_by(orm.Transaction.occurred_at.desc(), orm.Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = await self._session.execute(statement)
        return [to_domain(transaction, category) for transaction, category in rows]

    async def count(
        self,
        user_id: int,
        *,
        kind: TxKind | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        statement = select(func.count()).where(
            orm.Transaction.user_id == user_id, orm.Transaction.deleted_at.is_(None)
        )
        if kind is not None:
            statement = statement.where(orm.Transaction.kind == kind)
        if since is not None:
            statement = statement.where(orm.Transaction.occurred_at >= since)
        if until is not None:
            statement = statement.where(orm.Transaction.occurred_at < until)
        return await self._session.scalar(statement) or 0
