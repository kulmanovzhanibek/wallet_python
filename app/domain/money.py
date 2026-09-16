"""Деньги: Decimal, округление, форматирование.

`float` для денег не используется нигде: 0.1 + 0.2 != 0.3, и на сумме из
тысячи операций расхождение становится видно пользователю. Все суммы —
`Decimal`, а наружу (JSON, CSV) уходят строками, чтобы клиент на JS не
превратил их обратно в double.

Округление — `ROUND_HALF_EVEN` («банковское»): половина уходит к чётной
цифре, поэтому на большом наборе округлений ошибка не накапливается в одну
сторону, в отличие от школьного `ROUND_HALF_UP`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

MONEY_EXP: Final = Decimal("0.01")
"""Шкала денежных сумм: NUMERIC(14,2) в БД."""

RATE_EXP: Final = Decimal("0.00000001")
"""Шкала курсов: NUMERIC(18,8) в БД."""

MAX_AMOUNT: Final = Decimal(10) ** 12
"""Разумный предел суммы одной операции (10^12)."""

THIN_SPACE: Final = " "
"""Разделитель разрядов при показе пользователю."""

CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "KZT": "₸",
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
}


class MoneyError(ValueError):
    """Сумма не прошла проверку."""


def currency_symbol(code: str) -> str:
    """Символ валюты или сам код, если символа не знаем."""
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())


def quantize_money(value: Decimal) -> Decimal:
    """Приводит сумму к двум знакам после запятой банковским округлением."""
    return value.quantize(MONEY_EXP, rounding=ROUND_HALF_EVEN)


def quantize_rate(value: Decimal) -> Decimal:
    """Приводит курс к восьми знакам после запятой."""
    return value.quantize(RATE_EXP, rounding=ROUND_HALF_EVEN)


def to_base(amount: Decimal, fx_rate: Decimal) -> Decimal:
    """Пересчитывает сумму в основную валюту.

    Округление делается один раз — здесь, на момент сохранения `amount_base`.
    Промежуточные вычисления идут с полной точностью Decimal.
    """
    return quantize_money(amount * fx_rate)


def validate_amount(value: Decimal) -> Decimal:
    """Проверяет сумму операции: конечная, больше нуля, не больше предела."""
    if not value.is_finite():
        raise MoneyError("сумма должна быть конечным числом")
    amount = quantize_money(value)
    if amount <= 0:
        raise MoneyError("сумма должна быть больше нуля")
    if amount >= MAX_AMOUNT:
        raise MoneyError(f"сумма должна быть меньше {MAX_AMOUNT:f}")
    return amount


def decimal_from_str(raw: str) -> Decimal:
    """Аккуратный разбор десятичной строки (принимает и запятую)."""
    text = raw.strip().replace(",", ".").replace(" ", "").replace(" ", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"не похоже на число: {raw!r}") from exc


def format_amount(value: Decimal) -> str:
    """`1500` → `1 500`, `3500.50` → `3 500,50`, `1500.00` → `1 500`."""
    amount = quantize_money(value)
    sign = "-" if amount < 0 else ""
    whole, _, frac = f"{abs(amount):.2f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", THIN_SPACE)
    if frac == "00":
        return f"{sign}{grouped}"
    return f"{sign}{grouped},{frac}"


def format_money(value: Decimal, currency: str) -> str:
    """`1 500 ₸` — сумма с разделителями разрядов и символом валюты."""
    return f"{format_amount(value)} {currency_symbol(currency)}"


def format_signed_money(value: Decimal, currency: str) -> str:
    """Баланс периода: со знаком, в том числе явным плюсом."""
    prefix = "+" if value > 0 else ""
    return f"{prefix}{format_money(value, currency)}"


def money_to_api(value: Decimal) -> str:
    """Сумма для JSON и CSV: всегда строка с двумя знаками и точкой."""
    return f"{quantize_money(value):.2f}"


def rate_to_api(value: Decimal) -> str:
    """Курс для JSON: всегда строка с восемью знаками."""
    return f"{quantize_rate(value):.8f}"


def percent_of(part: Decimal, whole: Decimal) -> int:
    """Целый процент, округлённый вниз. Ноль в знаменателе даёт 0."""
    if whole == 0:
        return 0
    return int(part * 100 // whole)
