"""Разбор сообщения вида «кофе 1500».

Чистая функция без обращений к БД: категории приходят параметром, поэтому
парсер тестируется юнит-тестами без поднятой инфраструктуры.

Что решает парсер, а что нет:

* сумма всегда `Decimal` — `float` для денег не используется нигде;
* тип операции задаёт только знак `+` (доход). Угадывать доход по словам
  («подарок 5000» — подарил или подарили?) парсер не берётся;
* если суммы нет, но слово похоже на категорию — сценарий спросит сумму;
  если суммы нет и слово ни на что не похоже — это ошибка разбора;
* если сумма есть, а описания нет — сценарий предложит выбрать категорию.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from app.domain.models import Category, TxKind
from app.domain.money import MoneyError, validate_amount

FALLBACK_CATEGORY_NAME: Final = "Другое"
"""Категория, в которую попадает всё, что не нашлось по словарю."""

THOUSAND_SUFFIX_MULTIPLIER: Final = Decimal(1000)

NBSP: Final = " "

CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "₸": "KZT",
    "$": "USD",
    "€": "EUR",
    "₽": "RUB",
}

# Слова валют сопоставляются ЦЕЛИКОМ, а не по началу: иначе «поездка в
# европу» превратилась бы в евро, а «работа» — в рубли.
CURRENCY_WORDS: Final[dict[str, str]] = {
    "kzt": "KZT",
    "тг": "KZT",
    "тнг": "KZT",
    "тенге": "KZT",
    "rub": "RUB",
    "руб": "RUB",
    "рубль": "RUB",
    "рубля": "RUB",
    "рублей": "RUB",
    "рубли": "RUB",
    "usd": "USD",
    "бакс": "USD",
    "баксов": "USD",
    "доллар": "USD",
    "доллара": "USD",
    "долларов": "USD",
    "eur": "EUR",
    "евро": "EUR",
}

# Разбор числа. Порядок альтернатив важен: сначала запись с разделителями
# разрядов, иначе «2 500» распалось бы на 2 и 500.
AMOUNT_RE: Final = re.compile(
    r"""
    (?P<sign>[-+])?
    (?P<int>
        # 2 500 | 12 500 | 1.500 | 1,500 — разделитель разрядов, ровно по 3 цифры
        \d{1,3}(?:[\s., ]\d{3})+(?!\d)
      | \d+
    )
    # Дробная часть: 1-2 цифры и дальше не цифра, чтобы «1.500» осталось
    # тысячей, а не превратилось в 1.50 с хвостом «0».
    (?:[.,](?P<frac>\d{1,2})(?!\d))?
    # Суффикс тысяч: 12к → 12000. Проверка «дальше не буква» относится именно
    # к суффиксу: если поставить её после всей группы, она разрешит обрезать
    # «12кг» до «1» — после единицы идёт цифра, и запрет формально выполнен.
    (?:(?P<suffix>[кkКK])(?![^\W\d_]))?
    """,
    re.VERBOSE,
)

WORD_RE: Final = re.compile(r"[^\W\d_]+", re.UNICODE)
"""Слово — последовательность букв: цифры и знаки в сопоставлении не участвуют."""


class ParseError(ValueError):
    """Сообщение не удалось разобрать."""


class ParseStatus(StrEnum):
    """Что делать с разобранным сообщением."""

    ready = "ready"
    """Есть и сумма, и категория — можно записывать."""

    need_amount = "need_amount"
    """Категория понятна, суммы нет — сценарий спросит сумму."""

    need_category = "need_category"
    """Сумма есть, описания нет — сценарий предложит выбрать категорию."""


@dataclass(frozen=True, slots=True)
class RawAmount:
    """Результат разбора числа и текста, до сопоставления с категориями."""

    kind: TxKind
    amount: Decimal | None
    currency: str | None
    note: str


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    """Готовый черновик операции."""

    status: ParseStatus
    kind: TxKind
    currency: str
    note: str
    amount: Decimal | None = None
    category: Category | None = None


def words(text: str) -> list[str]:
    """Слова сообщения в нижнем регистре."""
    return [match.group(0).lower() for match in WORD_RE.finditer(text)]


def parse_amount(raw: str) -> Decimal:
    """Разбирает число из ответа пользователя на вопрос «сколько?».

    Принимает те же формы, что и парсер сообщений: `1500`, `2 500`,
    `3500,50`, `12к`. Бросает :class:`ParseError` на всём остальном.

    Знак здесь запрещён: на этом шаге сценария расход или доход уже выбран,
    и «-100» в ответ на вопрос о сумме — противоречие, а не уточнение.
    Угадывать, что имел в виду пользователь, парсер не берётся.
    """
    match = AMOUNT_RE.fullmatch(raw.replace(NBSP, " ").strip())
    if match is None or not match.group("int"):
        raise ParseError(f"не похоже на сумму: {raw!r}")
    if match.group("sign"):
        raise ParseError("сумму нужно указать без знака")
    return _validate(_to_decimal(match))


def _to_decimal(match: re.Match[str]) -> Decimal:
    """Собирает `Decimal` из частей совпадения."""
    digits = re.sub(r"[\s., ]", "", match.group("int"))
    frac = match.group("frac") or "0"
    try:
        value = Decimal(f"{digits}.{frac}")
    except InvalidOperation as exc:  # pragma: no cover — regex не пускает мусор
        raise ParseError(f"не похоже на сумму: {match.group(0)!r}") from exc
    if match.group("suffix"):
        value *= THOUSAND_SUFFIX_MULTIPLIER
    return value


def _validate(value: Decimal) -> Decimal:
    try:
        return validate_amount(value)
    except MoneyError as exc:
        raise ParseError(str(exc)) from exc


def _extract_symbol(text: str) -> tuple[str | None, str]:
    """Вынимает символ валюты. Ищется до суммы: символ к ней приклеен («1500₸»)."""
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code, text.replace(symbol, " ")
    return None, text


def _extract_currency_word(text: str) -> tuple[str | None, str]:
    """Вынимает валюту, написанную словом.

    Вызывается уже после вырезания суммы, поэтому «12usd» разбирается так же,
    как «12 usd»: к этому моменту от числа остаётся отдельный токен «usd».
    """
    tokens = text.split()
    for index, token in enumerate(tokens):
        code = CURRENCY_WORDS.get(token.strip(".,!?;:").lower())
        if code is not None:
            return code, " ".join((*tokens[:index], *tokens[index + 1 :]))
    return None, text


def _pick_amount(text: str) -> re.Match[str] | None:
    """Выбирает из сообщения число, которое является суммой.

    Правило: побеждает самое большое число. В сообщении с одним числом —
    а таких большинство, включая все примеры из ТЗ — это ничего не меняет,
    зато «12кг картошки 900» и «1500 за 2 кофе» разбираются так, как человек
    и имел в виду. При равенстве побеждает первое вхождение.
    """
    best: tuple[Decimal, re.Match[str]] | None = None
    for match in AMOUNT_RE.finditer(text):
        # Все группы необязательные, поэтому регулярка умеет совпасть с пустой
        # строкой — такие совпадения пропускаем.
        if not match.group("int"):
            continue
        value = _to_decimal(match)
        if best is None or value > best[0]:
            best = (value, match)
    return best[1] if best is not None else None


def _clean(text: str) -> str:
    """Убирает лишние пробелы и знаки, оставшиеся после вырезания числа."""
    return " ".join(text.replace(NBSP, " ").split()).strip(" .,;:!?-+")


def parse_raw(text: str) -> RawAmount:
    """Достаёт из сообщения знак, сумму, валюту и описание."""
    normalised = text.replace(NBSP, " ").strip()
    if not normalised:
        raise ParseError("пустое сообщение")

    symbol_currency, rest = _extract_symbol(normalised)
    match = _pick_amount(rest)

    if match is None:
        word_currency, without_currency = _extract_currency_word(rest)
        return RawAmount(
            kind=TxKind.expense,
            amount=None,
            currency=symbol_currency or word_currency,
            note=_clean(without_currency),
        )

    remainder = f"{rest[: match.start()]} {rest[match.end() :]}"
    word_currency, note = _extract_currency_word(remainder)
    return RawAmount(
        kind=TxKind.income if match.group("sign") == "+" else TxKind.expense,
        amount=_validate(_to_decimal(match)),
        currency=symbol_currency or word_currency,
        note=_clean(note),
    )


def match_category(note: str, categories: Sequence[Category], kind: TxKind) -> Category | None:
    """Ищет категорию по словарю ключевых слов.

    Сопоставление — по началу слова и без учёта регистра: ключ «продукт»
    покрывает и «продукты», и «продуктовый». Если подошло несколько
    категорий, выигрывает та, у которой ключ длиннее: он конкретнее.
    """
    note_words = words(note)
    if not note_words:
        return None

    best: tuple[int, Category] | None = None
    for category in categories:
        if category.kind is not kind:
            continue
        for keyword in category.keywords:
            key = keyword.lower()
            if not key or not any(word.startswith(key) for word in note_words):
                continue
            if best is None or len(key) > best[0]:
                best = (len(key), category)
    return best[1] if best is not None else None


def fallback_category(categories: Sequence[Category], kind: TxKind) -> Category | None:
    """Категория «Другое» нужного типа — куда попадает всё неопознанное."""
    for category in categories:
        if category.kind is kind and category.name == FALLBACK_CATEGORY_NAME:
            return category
    return None


def parse_message(
    text: str,
    *,
    default_currency: str,
    categories: Sequence[Category],
) -> ParsedTransaction:
    """Разбирает сообщение пользователя в черновик операции.

    :raises ParseError: если в сообщении нет ни суммы, ни знакомого слова.
    """
    raw = parse_raw(text)
    currency = raw.currency or default_currency
    category = match_category(raw.note, categories, raw.kind)

    if raw.amount is None:
        if category is None:
            raise ParseError("не нашёл ни суммы, ни знакомой категории")
        return ParsedTransaction(
            status=ParseStatus.need_amount,
            kind=raw.kind,
            currency=currency,
            note=raw.note,
            category=category,
        )

    if not raw.note:
        return ParsedTransaction(
            status=ParseStatus.need_category,
            kind=raw.kind,
            currency=currency,
            note="",
            amount=raw.amount,
        )

    return ParsedTransaction(
        status=ParseStatus.ready,
        kind=raw.kind,
        currency=currency,
        note=raw.note,
        amount=raw.amount,
        category=category or fallback_category(categories, raw.kind),
    )
