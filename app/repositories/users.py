"""Доступ к пользователям.

Репозиторий только читает и пишет. Бизнес-правил здесь нет — они в сервисах.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import DigestMode, User
from app.infra import orm


def to_domain(row: orm.User) -> User:
    return User(
        id=row.id,
        tg_id=row.tg_id,
        username=row.username,
        base_currency=row.base_currency,
        timezone=row.timezone,
        digest=row.digest,
        created_at=row.created_at,
        has_api_token=row.api_token_hash is not None,
    )


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        row = await self._session.get(orm.User, user_id)
        return to_domain(row) if row else None

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        row = await self._session.scalar(select(orm.User).where(orm.User.tg_id == tg_id))
        return to_domain(row) if row else None

    async def get_by_token_hash(self, token_hash: str) -> User | None:
        """Поиск по уникальному индексу хэша — так работает Bearer-авторизация."""
        row = await self._session.scalar(
            select(orm.User).where(orm.User.api_token_hash == token_hash)
        )
        return to_domain(row) if row else None

    async def get_or_create(
        self,
        tg_id: int,
        *,
        username: str | None,
        base_currency: str,
        timezone: str,
    ) -> tuple[User, bool]:
        """Возвращает пользователя и признак «создан только что».

        Повторный `/start` не должен ни плодить дубли, ни сбрасывать
        настройки. Поэтому это одна атомарная вставка с
        `ON CONFLICT DO NOTHING`, а не «проверил и вставил»: между проверкой
        и вставкой мог бы вклиниться параллельный апдейт того же
        пользователя.
        """
        statement = (
            insert(orm.User)
            .values(
                tg_id=tg_id,
                username=username,
                base_currency=base_currency,
                timezone=timezone,
            )
            .on_conflict_do_nothing(index_elements=["tg_id"])
            .returning(orm.User)
        )
        created = (await self._session.scalars(statement)).one_or_none()
        if created is not None:
            return to_domain(created), True

        existing = await self.get_by_tg_id(tg_id)
        if existing is None:  # pragma: no cover — возможно лишь при гонке с удалением
            msg = f"пользователь tg_id={tg_id} исчез между вставкой и чтением"
            raise RuntimeError(msg)
        return existing, False

    async def update_settings(
        self,
        user_id: int,
        *,
        base_currency: str | None = None,
        timezone: str | None = None,
        digest: DigestMode | None = None,
        username: str | None = None,
    ) -> User | None:
        """Меняет только переданные поля."""
        values: dict[str, object] = {}
        if base_currency is not None:
            values["base_currency"] = base_currency
        if timezone is not None:
            values["timezone"] = timezone
        if digest is not None:
            values["digest"] = digest
        if username is not None:
            values["username"] = username
        if not values:
            return await self.get_by_id(user_id)

        row = (
            await self._session.scalars(
                update(orm.User).where(orm.User.id == user_id).values(**values).returning(orm.User)
            )
        ).one_or_none()
        return to_domain(row) if row else None

    async def set_token_hash(self, user_id: int, token_hash: str | None) -> bool:
        """Записывает или снимает хэш API-токена. Старый токен сразу перестаёт работать.

        `RETURNING` вместо `rowcount`: результат приходит из того же запроса,
        лишнего SELECT нет, и типы SQLAlchemy не приходится обманывать.
        """
        updated = (
            await self._session.scalars(
                update(orm.User)
                .where(orm.User.id == user_id)
                .values(api_token_hash=token_hash)
                .returning(orm.User.id)
            )
        ).one_or_none()
        return updated is not None
