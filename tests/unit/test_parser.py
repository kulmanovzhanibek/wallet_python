"""Парсер сообщений: «кофе 1500» и всё, что рядом.

Категории передаются параметром, поэтому тесты идут без БД.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.models import Category, TxKind
from app.domain.parser import (
    ParsedTransaction,
    ParseError,
    ParseStatus,
    fallback_category,
    match_category,
    parse_amount,
    parse_message,
    parse_raw,
    words,
)

CATEGORIES: tuple[Category, ...] = (
    Category(1, None, TxKind.expense, "Кафе", "☕", ("кофе", "кафе", "обед", "ресторан")),
    Category(2, None, TxKind.expense, "Транспорт", "🚕", ("такси", "метро", "бензин")),
    Category(3, None, TxKind.expense, "Еда", "🍔", ("продукт", "еда", "магазин")),
    Category(4, None, TxKind.expense, "Развлечения", "🎮", ("кино", "игр")),
    Category(5, None, TxKind.expense, "Другое", "📦", ()),
    Category(6, None, TxKind.income, "Зарплата", "💰", ("зарплат", "оклад")),
    Category(7, None, TxKind.income, "Подарок", "🎁", ("подар",)),
    Category(8, None, TxKind.income, "Другое", "📦", ()),
)


def parse(text: str, currency: str = "KZT") -> ParsedTransaction:
    return parse_message(text, default_currency=currency, categories=CATEGORIES)


class TestSpecExamples:
    """Таблица из раздела 5.2 ТЗ — построчно."""

    @pytest.mark.parametrize(
        ("text", "amount", "category", "kind"),
        [
            ("кофе 1500", "1500.00", "Кафе", TxKind.expense),
            ("1500 кофе", "1500.00", "Кафе", TxKind.expense),
            ("такси 2 500", "2500.00", "Транспорт", TxKind.expense),
            ("обед 3500,50", "3500.50", "Кафе", TxKind.expense),
            ("обед 3500.50", "3500.50", "Кафе", TxKind.expense),
            ("+500000 зарплата", "500000.00", "Зарплата", TxKind.income),
            ("продукты 12к", "12000.00", "Еда", TxKind.expense),
        ],
    )
    def test_ready(self, text: str, amount: str, category: str, kind: TxKind) -> None:
        result = parse(text)
        assert result.status is ParseStatus.ready
        assert result.amount == Decimal(amount)
        assert result.kind is kind
        assert result.category is not None
        assert result.category.name == category

    def test_foreign_currency(self) -> None:
        result = parse("такси 12 usd")
        assert result.status is ParseStatus.ready
        assert (result.amount, result.currency) == (Decimal("12.00"), "USD")

    def test_unparsable_text_is_an_error(self) -> None:
        with pytest.raises(ParseError, match="ни суммы, ни знакомой категории"):
            parse("привет")

    def test_category_without_amount_asks_for_amount(self) -> None:
        result = parse("кофе")
        assert result.status is ParseStatus.need_amount
        assert result.amount is None
        assert result.category is not None
        assert result.category.name == "Кафе"

    def test_amount_without_note_asks_for_category(self) -> None:
        result = parse("1500")
        assert result.status is ParseStatus.need_category
        assert result.amount == Decimal("1500.00")
        assert result.category is None
        assert result.note == ""


class TestNumberFormats:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("кофе 1500", "1500.00"),
            ("кофе 999", "999.00"),
            ("кофе 1", "1.00"),
            # Разделители разрядов: пробел, неразрывный пробел, точка, запятая.
            ("кофе 2 500", "2500.00"),
            ("кофе 12 500", "12500.00"),
            ("кофе 1 500 000", "1500000.00"),
            ("кофе 2 500", "2500.00"),
            ("кофе 1.500", "1500.00"),
            ("кофе 1,500", "1500.00"),
            # Дробная часть: одна или две цифры, точка или запятая.
            ("кофе 3500,50", "3500.50"),
            ("кофе 3500.50", "3500.50"),
            ("кофе 3500,5", "3500.50"),
            ("кофе 0,99", "0.99"),
            ("кофе 12 500,75", "12500.75"),
            # Суффикс тысяч в четырёх написаниях.
            ("кофе 12к", "12000.00"),
            ("кофе 12k", "12000.00"),
            ("кофе 12К", "12000.00"),
            ("кофе 12K", "12000.00"),
            ("кофе 12.5к", "12500.00"),
            ("кофе 1,5к", "1500.00"),
        ],
    )
    def test_amount(self, text: str, expected: str) -> None:
        assert parse(text).amount == Decimal(expected)

    def test_dotted_thousands_do_not_become_kopecks(self) -> None:
        """«1.500» — это тысяча пятьсот, а не 1.50 с потерянным нулём."""
        assert parse("кофе 1.500").amount == Decimal("1500.00")

    def test_letter_suffix_is_not_a_unit(self) -> None:
        """«12кг» — килограммы, а не двенадцать тысяч."""
        result = parse("12кг картошки 900")
        assert result.amount == Decimal("900.00")
        assert "12кг" in result.note

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("12кг картошки 900", "900.00"),
            ("1500 за 2 кофе", "1500.00"),
            ("кино 2 билета 7000", "7000.00"),
        ],
    )
    def test_largest_number_wins(self, text: str, expected: str) -> None:
        """В сообщении с количеством и ценой суммой становится цена."""
        assert parse(text).amount == Decimal(expected)


class TestCurrency:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("такси 12 usd", "USD"),
            ("такси 12 USD", "USD"),
            ("такси 12 доллар", "USD"),
            ("такси 12 долларов", "USD"),
            ("такси 12$", "USD"),
            ("такси 12usd", "USD"),
            ("кофе 1500 тг", "KZT"),
            ("кофе 1500 тенге", "KZT"),
            ("кофе 1500₸", "KZT"),
            ("кофе 100 руб", "RUB"),
            ("кофе 100 рублей", "RUB"),
            ("кофе 100₽", "RUB"),
            ("кофе 10 евро", "EUR"),
            ("кофе 10 eur", "EUR"),
            ("кофе 10€", "EUR"),
        ],
    )
    def test_recognised(self, text: str, expected: str) -> None:
        result = parse(text)
        assert result.currency == expected
        assert result.currency not in result.note

    def test_defaults_to_user_currency(self) -> None:
        assert parse("кофе 1500").currency == "KZT"
        assert parse("кофе 1500", currency="RUB").currency == "RUB"

    @pytest.mark.parametrize("text", ["поездка в европу 50000", "работа 100", "еда 500"])
    def test_words_that_only_look_like_currencies(self, text: str) -> None:
        """«европу» — не евро, «работа» — не рубли: слова сверяются целиком."""
        assert parse(text).currency == "KZT"


class TestKind:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("+500000 зарплата", TxKind.income),
            ("зарплата +500000", TxKind.income),
            ("+5000 подарок", TxKind.income),
            ("кофе 1500", TxKind.expense),
            ("-1500 кофе", TxKind.expense),
        ],
    )
    def test_sign_decides(self, text: str, expected: TxKind) -> None:
        assert parse(text).kind is expected

    def test_income_words_alone_do_not_make_income(self) -> None:
        """«подарок 5000» — подарил или подарили? Парсер не угадывает."""
        result = parse("подарок 5000")
        assert result.kind is TxKind.expense
        assert result.category is not None
        assert result.category.name == "Другое"

    def test_category_is_searched_within_the_kind(self) -> None:
        """У расхода и дохода свои наборы категорий."""
        assert parse("+5000 подарок").category is not None
        assert parse("+5000 подарок").category.name == "Подарок"  # type: ignore[union-attr]


class TestErrors:
    @pytest.mark.parametrize("text", ["", "   ", "\n", " "])
    def test_empty_input(self, text: str) -> None:
        with pytest.raises(ParseError, match="пустое сообщение"):
            parse(text)

    @pytest.mark.parametrize("text", ["привет", "как дела", "спасибо!", "ок"])
    def test_no_amount_and_no_category(self, text: str) -> None:
        with pytest.raises(ParseError, match="ни суммы, ни знакомой категории"):
            parse(text)

    @pytest.mark.parametrize("text", ["кофе 0", "кофе 0,00", "кофе -0"])
    def test_zero_is_rejected(self, text: str) -> None:
        with pytest.raises(ParseError, match="больше нуля"):
            parse(text)

    @pytest.mark.parametrize("text", ["кофе 1000000000000", "кофе 9999999999999"])
    def test_absurdly_large_is_rejected(self, text: str) -> None:
        with pytest.raises(ParseError, match="меньше"):
            parse(text)


class TestNote:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("кофе 1500", "кофе"),
            ("1500 кофе", "кофе"),
            ("кофе   1500", "кофе"),
            ("кофе 1500!", "кофе"),
            ("обед в ресторане 5000", "обед в ресторане"),
            ("1500 - кофе", "кофе"),
            ("КОФЕ 1500", "КОФЕ"),
        ],
    )
    def test_note_is_cleaned(self, text: str, expected: str) -> None:
        assert parse(text).note == expected

    def test_original_case_is_kept(self) -> None:
        """Сопоставление — без учёта регистра, но в заметке текст как ввели."""
        result = parse("КОФЕ 1500")
        assert result.note == "КОФЕ"
        assert result.category is not None
        assert result.category.name == "Кафе"


class TestMatchCategory:
    @pytest.mark.parametrize(
        ("note", "expected"),
        [
            ("кофе", "Кафе"),
            ("кофейня", "Кафе"),  # по началу слова
            ("Кофе", "Кафе"),  # без учёта регистра
            ("КОФЕ", "Кафе"),
            ("продукты", "Еда"),
            ("продуктовый магазин", "Еда"),
            ("такси до дома", "Транспорт"),
            ("игрушки", "Развлечения"),
            ("бензин", "Транспорт"),
        ],
    )
    def test_matches(self, note: str, expected: str) -> None:
        category = match_category(note, CATEGORIES, TxKind.expense)
        assert category is not None
        assert category.name == expected

    @pytest.mark.parametrize("note", ["привет", "", "   ", "12345"])
    def test_no_match(self, note: str) -> None:
        assert match_category(note, CATEGORIES, TxKind.expense) is None

    def test_longest_keyword_wins(self) -> None:
        """Конкретный ключ важнее общего."""
        categories = (
            Category(1, None, TxKind.expense, "Общее", "📦", ("маг",)),
            Category(2, None, TxKind.expense, "Магазин", "🏬", ("магазин",)),
        )
        category = match_category("магазин", categories, TxKind.expense)
        assert category is not None
        assert category.name == "Магазин"

    def test_kind_is_respected(self) -> None:
        assert match_category("зарплата", CATEGORIES, TxKind.expense) is None
        assert match_category("зарплата", CATEGORIES, TxKind.income) is not None

    def test_unknown_note_falls_back_to_other(self) -> None:
        result = parse("шиномонтаж 8000")
        assert result.status is ParseStatus.ready
        assert result.category is not None
        assert result.category.name == "Другое"

    def test_fallback_category_lookup(self) -> None:
        expense = fallback_category(CATEGORIES, TxKind.expense)
        income = fallback_category(CATEGORIES, TxKind.income)
        assert expense is not None
        assert income is not None
        assert expense.id != income.id
        assert fallback_category((), TxKind.expense) is None


class TestParseAmount:
    """Отдельный разбор суммы — для шага FSM «сколько?»."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1500", "1500.00"),
            ("2 500", "2500.00"),
            ("3500,50", "3500.50"),
            ("3500.50", "3500.50"),
            ("12к", "12000.00"),
            (" 1500 ", "1500.00"),
            ("1,5к", "1500.00"),
        ],
    )
    def test_accepts(self, raw: str, expected: str) -> None:
        assert parse_amount(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", ["много", "", "  ", "1500 кофе", "кофе 1500", "1500₸"])
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(ParseError):
            parse_amount(raw)

    @pytest.mark.parametrize("raw", ["0", "0,00", "1000000000000"])
    def test_rejects_out_of_range(self, raw: str) -> None:
        with pytest.raises(ParseError):
            parse_amount(raw)

    @pytest.mark.parametrize("raw", ["-100", "+100", "-1500,50"])
    def test_rejects_signed_amount(self, raw: str) -> None:
        """Расход или доход выбран на предыдущем шаге — знак тут противоречит ему."""
        with pytest.raises(ParseError, match="без знака"):
            parse_amount(raw)


class TestHelpers:
    def test_words_splits_on_non_letters(self) -> None:
        assert words("Кофе, 1500 руб!") == ["кофе", "руб"]

    def test_parse_raw_keeps_currency_unresolved(self) -> None:
        """`parse_raw` не подставляет валюту пользователя — это делает `parse_message`."""
        raw = parse_raw("кофе 1500")
        assert raw.currency is None
        assert parse_raw("кофе 12 usd").currency == "USD"
