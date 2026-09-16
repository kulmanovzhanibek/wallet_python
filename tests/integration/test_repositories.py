"""Репозитории на настоящем PostgreSQL.

Главное, что здесь проверяется, — изоляция пользователей: чужую запись
нельзя ни прочитать, ни изменить, и фильтр по `user_id` стоит внутри SQL,
а не после загрузки.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Category, DigestMode, Transaction, TxKind, User
from app.repositories.categories import CategoriesRepository
from app.repositories.transactions import TransactionsRepository
from app.repositories.users import UsersRepository

OCCURRED = datetime(2026, 9, 16, 9, 32, tzinfo=UTC)


@pytest_asyncio.fixture
async def users(session: AsyncSession) -> UsersRepository:
    return UsersRepository(session)


@pytest_asyncio.fixture
async def categories(session: AsyncSession) -> CategoriesRepository:
    return CategoriesRepository(session)


@pytest_asyncio.fixture
async def transactions(session: AsyncSession) -> TransactionsRepository:
    return TransactionsRepository(session)


@pytest_asyncio.fixture
async def alice(users: UsersRepository) -> User:
    user, _ = await users.get_or_create(
        100_001, username="alice", base_currency="KZT", timezone="Asia/Almaty"
    )
    return user


@pytest_asyncio.fixture
async def bob(users: UsersRepository) -> User:
    user, _ = await users.get_or_create(
        100_002, username="bob", base_currency="RUB", timezone="Europe/Moscow"
    )
    return user


@pytest_asyncio.fixture
async def cafe(categories: CategoriesRepository, alice: User) -> Category:
    visible = await categories.list_visible(alice.id, TxKind.expense)
    return next(category for category in visible if category.name == "Кафе")


@pytest_asyncio.fixture
async def taxi(categories: CategoriesRepository, alice: User) -> Category:
    visible = await categories.list_visible(alice.id, TxKind.expense)
    return next(category for category in visible if category.name == "Транспорт")


class TestUsers:
    async def test_create_and_read_back(self, users: UsersRepository) -> None:
        created, is_new = await users.get_or_create(
            200_001, username="zhanibek", base_currency="KZT", timezone="Asia/Almaty"
        )
        assert is_new
        assert created.tg_id == 200_001
        assert created.base_currency == "KZT"
        assert created.digest is DigestMode.off
        assert not created.has_api_token

        by_tg = await users.get_by_tg_id(200_001)
        by_id = await users.get_by_id(created.id)
        assert by_tg == by_id == created

    async def test_repeated_start_neither_duplicates_nor_resets(
        self, users: UsersRepository
    ) -> None:
        """Повторный /start: тот же пользователь, настройки на месте."""
        first, is_new = await users.get_or_create(
            200_002, username="user", base_currency="USD", timezone="UTC"
        )
        assert is_new
        await users.update_settings(first.id, timezone="Asia/Almaty", digest=DigestMode.daily)

        second, is_new_again = await users.get_or_create(
            200_002, username="user", base_currency="KZT", timezone="UTC"
        )
        assert not is_new_again
        assert second.id == first.id
        assert second.base_currency == "USD"
        assert second.timezone == "Asia/Almaty"
        assert second.digest is DigestMode.daily

    async def test_unknown_user(self, users: UsersRepository) -> None:
        assert await users.get_by_tg_id(999_999_999) is None
        assert await users.get_by_id(999_999_999) is None

    async def test_update_only_given_fields(self, users: UsersRepository, alice: User) -> None:
        updated = await users.update_settings(alice.id, base_currency="EUR")
        assert updated is not None
        assert updated.base_currency == "EUR"
        assert updated.timezone == alice.timezone
        assert updated.digest is alice.digest

    async def test_update_without_fields_is_a_no_op(
        self, users: UsersRepository, alice: User
    ) -> None:
        assert await users.update_settings(alice.id) == alice

    async def test_update_missing_user(self, users: UsersRepository) -> None:
        assert await users.update_settings(999_999_999, timezone="UTC") is None


class TestApiToken:
    async def test_set_find_and_revoke(self, users: UsersRepository, alice: User) -> None:
        token_hash = "a" * 64
        assert await users.set_token_hash(alice.id, token_hash)

        found = await users.get_by_token_hash(token_hash)
        assert found is not None
        assert found.id == alice.id
        assert found.has_api_token

        # Перевыпуск: старый хэш перестаёт работать сразу.
        assert await users.set_token_hash(alice.id, "b" * 64)
        assert await users.get_by_token_hash(token_hash) is None
        assert await users.get_by_token_hash("b" * 64) is not None

        # Отзыв без выпуска нового.
        assert await users.set_token_hash(alice.id, None)
        assert await users.get_by_token_hash("b" * 64) is None

    async def test_unknown_hash(self, users: UsersRepository) -> None:
        assert await users.get_by_token_hash("f" * 64) is None

    async def test_set_token_for_missing_user(self, users: UsersRepository) -> None:
        assert not await users.set_token_hash(999_999_999, "c" * 64)


class TestCategories:
    async def test_system_categories_are_visible_to_everyone(
        self, categories: CategoriesRepository, alice: User
    ) -> None:
        visible = await categories.list_visible(alice.id)
        assert len(visible) == 17
        assert all(category.is_system for category in visible)
        assert {category.name for category in visible} >= {"Кафе", "Транспорт", "Другое"}

    async def test_kind_filter(self, categories: CategoriesRepository, alice: User) -> None:
        expenses = await categories.list_visible(alice.id, TxKind.expense)
        incomes = await categories.list_visible(alice.id, TxKind.income)
        assert {category.kind for category in expenses} == {TxKind.expense}
        assert {category.kind for category in incomes} == {TxKind.income}
        assert len(expenses) + len(incomes) == 17

    async def test_own_category_is_not_visible_to_others(
        self, categories: CategoriesRepository, alice: User, bob: User
    ) -> None:
        mine = await categories.create(
            user_id=alice.id, kind=TxKind.expense, name="Ремонт", emoji="🔧", keywords=("ремонт",)
        )
        assert mine.user_id == alice.id
        assert not mine.is_system

        alice_names = {c.name for c in await categories.list_visible(alice.id)}
        bob_names = {c.name for c in await categories.list_visible(bob.id)}
        assert "Ремонт" in alice_names
        assert "Ремонт" not in bob_names

        assert await categories.get_visible(mine.id, alice.id) is not None
        assert await categories.get_visible(mine.id, bob.id) is None

    async def test_system_categories_come_first_and_order_is_stable(
        self, categories: CategoriesRepository, alice: User
    ) -> None:
        """Клавиатура в боте не должна перетасовываться между вызовами."""
        await categories.create(user_id=alice.id, kind=TxKind.expense, name="Ремонт")
        first = await categories.list_visible(alice.id, TxKind.expense)
        second = await categories.list_visible(alice.id, TxKind.expense)
        assert [c.id for c in first] == [c.id for c in second]
        assert first[-1].name == "Ремонт"

    async def test_delete_only_own(
        self, categories: CategoriesRepository, alice: User, bob: User, cafe: Category
    ) -> None:
        mine = await categories.create(user_id=alice.id, kind=TxKind.expense, name="Ремонт")

        assert not await categories.delete_own(cafe.id, alice.id)  # системная
        assert not await categories.delete_own(mine.id, bob.id)  # чужая
        assert await categories.delete_own(mine.id, alice.id)
        assert await categories.get_visible(mine.id, alice.id) is None

    async def test_has_transactions_counts_soft_deleted_too(
        self,
        categories: CategoriesRepository,
        transactions: TransactionsRepository,
        alice: User,
        cafe: Category,
    ) -> None:
        """Удалённую запись можно вернуть, поэтому категория под ней ещё нужна."""
        assert not await categories.has_transactions(cafe.id)

        created = await make_transaction(transactions, alice, cafe)
        assert await categories.has_transactions(cafe.id)

        await transactions.soft_delete(created.id, alice.id)
        assert await categories.has_transactions(cafe.id)


async def make_transaction(
    repository: TransactionsRepository,
    user: User,
    category: Category,
    *,
    amount: str = "1500.00",
    occurred_at: datetime = OCCURRED,
    kind: TxKind = TxKind.expense,
    note: str | None = "кофе",
) -> Transaction:
    return await repository.create(
        user_id=user.id,
        category_id=category.id,
        kind=kind,
        amount=Decimal(amount),
        currency=user.base_currency,
        fx_rate=Decimal(1),
        amount_base=Decimal(amount),
        note=note,
        occurred_at=occurred_at,
    )


class TestTransactions:
    async def test_create_stores_exact_decimals(
        self, transactions: TransactionsRepository, alice: User, cafe: Category
    ) -> None:
        created = await transactions.create(
            user_id=alice.id,
            category_id=cafe.id,
            kind=TxKind.expense,
            amount=Decimal("3500.50"),
            currency="KZT",
            fx_rate=Decimal("1.00000000"),
            amount_base=Decimal("3500.50"),
            note="обед",
            occurred_at=OCCURRED,
        )
        assert created.amount == Decimal("3500.50")
        assert created.amount_base == Decimal("3500.50")
        assert created.occurred_at == OCCURRED
        assert created.source == "bot"
        assert created.deleted_at is None

    async def test_other_users_record_is_invisible(
        self, transactions: TransactionsRepository, alice: User, bob: User, cafe: Category
    ) -> None:
        """Чужой id не должен ничего отдавать — API вернёт 404, не раскрывая запись."""
        created = await make_transaction(transactions, alice, cafe)
        assert await transactions.get(created.id, alice.id) is not None
        assert await transactions.get(created.id, bob.id) is None

    async def test_get_with_category(
        self, transactions: TransactionsRepository, alice: User, cafe: Category
    ) -> None:
        created = await make_transaction(transactions, alice, cafe)
        found = await transactions.get_with_category(created.id, alice.id)
        assert found is not None
        assert found.category is not None
        assert found.category.name == "Кафе"
        assert found.category.emoji == "☕"

    async def test_change_category_only_for_own_record(
        self,
        transactions: TransactionsRepository,
        alice: User,
        bob: User,
        cafe: Category,
        taxi: Category,
    ) -> None:
        created = await make_transaction(transactions, alice, cafe)

        assert await transactions.set_category(created.id, bob.id, taxi.id) is None
        updated = await transactions.set_category(created.id, alice.id, taxi.id)
        assert updated is not None
        assert updated.category_id == taxi.id

    async def test_soft_delete_and_restore(
        self, transactions: TransactionsRepository, alice: User, bob: User, cafe: Category
    ) -> None:
        created = await make_transaction(transactions, alice, cafe)
        transaction_id = created.id

        assert await transactions.soft_delete(transaction_id, bob.id) is None

        deleted = await transactions.soft_delete(transaction_id, alice.id)
        assert deleted is not None
        assert deleted.deleted_at is not None

        # После мягкого удаления запись не видна обычным чтением, но её
        # можно достать явно — для кнопки «Вернуть».
        assert await transactions.get(transaction_id, alice.id) is None
        assert await transactions.get(transaction_id, alice.id, include_deleted=True) is not None

        # Повторное удаление ничего не меняет.
        assert await transactions.soft_delete(transaction_id, alice.id) is None

        restored = await transactions.restore(transaction_id, alice.id)
        assert restored is not None
        assert restored.deleted_at is None
        assert await transactions.get(transaction_id, alice.id) is not None
        # Повторный возврат — тоже ничего.
        assert await transactions.restore(transaction_id, alice.id) is None

    async def test_restore_only_own(
        self, transactions: TransactionsRepository, alice: User, bob: User, cafe: Category
    ) -> None:
        created = await make_transaction(transactions, alice, cafe)
        transaction_id = created.id
        await transactions.soft_delete(transaction_id, alice.id)
        assert await transactions.restore(transaction_id, bob.id) is None


class TestPagination:
    @pytest_asyncio.fixture
    async def many(
        self, transactions: TransactionsRepository, alice: User, cafe: Category
    ) -> list[int]:
        """25 операций: первые пять — с одинаковым occurred_at."""
        ids: list[int] = []
        for index in range(25):
            shift = timedelta(minutes=0 if index < 5 else index)
            created = await make_transaction(
                transactions, alice, cafe, occurred_at=OCCURRED + shift, amount=f"{index + 1}.00"
            )
            ids.append(created.id)
        return ids

    async def test_newest_first(
        self, transactions: TransactionsRepository, alice: User, many: list[int]
    ) -> None:
        page = await transactions.list_page_offset(alice.id, limit=10)
        assert len(page) == 10
        moments = [item.occurred_at for item in page]
        assert moments == sorted(moments, reverse=True)

    async def test_every_record_appears_exactly_once(
        self, transactions: TransactionsRepository, alice: User, many: list[int]
    ) -> None:
        """Ключевая проверка: одинаковый occurred_at не должен ломать страницы."""
        seen: list[int] = []
        offset = 0
        while page := await transactions.list_page_offset(alice.id, limit=7, offset=offset):
            seen.extend(item.id for item in page)
            offset += 7

        assert len(seen) == len(many)
        assert set(seen) == set(many)
        assert len(set(seen)) == len(seen)

    async def test_soft_deleted_are_skipped(
        self, transactions: TransactionsRepository, alice: User, many: list[int]
    ) -> None:
        await transactions.soft_delete(many[0], alice.id)
        assert await transactions.count(alice.id) == len(many) - 1
        page = await transactions.list_page_offset(alice.id, limit=100)
        assert many[0] not in {item.id for item in page}

    async def test_filters(
        self,
        transactions: TransactionsRepository,
        alice: User,
        cafe: Category,
        many: list[int],
    ) -> None:
        await make_transaction(
            transactions,
            alice,
            cafe,
            kind=TxKind.income,
            amount="500000.00",
            occurred_at=OCCURRED + timedelta(days=1),
        )
        assert await transactions.count(alice.id, kind=TxKind.income) == 1
        assert await transactions.count(alice.id, kind=TxKind.expense) == len(many)
        assert await transactions.count(alice.id, since=OCCURRED + timedelta(days=1)) == 1
        assert await transactions.count(alice.id, until=OCCURRED + timedelta(days=1)) == len(many)

    async def test_other_users_records_are_not_in_the_page(
        self, transactions: TransactionsRepository, alice: User, bob: User, many: list[int]
    ) -> None:
        assert await transactions.count(bob.id) == 0
        assert await transactions.list_page_offset(bob.id) == []


@pytest.mark.parametrize("kind", [TxKind.expense, TxKind.income])
async def test_fallback_category_is_available_for_both_kinds(
    categories: CategoriesRepository, alice: User, kind: TxKind
) -> None:
    visible = await categories.list_visible(alice.id, kind)
    assert any(category.name == "Другое" for category in visible)
