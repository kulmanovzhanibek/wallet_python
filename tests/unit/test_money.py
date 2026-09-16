"""Деньги: округление, валидация, форматирование."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.domain.money import (
    MAX_AMOUNT,
    MoneyError,
    currency_symbol,
    decimal_from_str,
    format_amount,
    format_money,
    format_signed_money,
    money_to_api,
    percent_of,
    quantize_money,
    quantize_rate,
    rate_to_api,
    to_base,
    validate_amount,
)


class TestQuantize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Банковское округление: половина уходит к чётной цифре.
            ("2.345", "2.34"),
            ("2.355", "2.36"),
            ("2.365", "2.36"),
            ("0.005", "0.00"),
            ("0.015", "0.02"),
            # Обычные случаи.
            ("1500", "1500.00"),
            ("1500.004", "1500.00"),
            ("1500.006", "1500.01"),
            ("-2.345", "-2.34"),
        ],
    )
    def test_half_even(self, raw: str, expected: str) -> None:
        assert quantize_money(Decimal(raw)) == Decimal(expected)

    def test_half_even_does_not_drift(self) -> None:
        """На наборе «ровных половинок» банковское округление не уводит сумму вверх."""
        values = [Decimal("0.005") + Decimal("0.01") * i for i in range(100)]
        rounded = sum((quantize_money(v) for v in values), Decimal(0))
        exact = sum(values, Decimal(0))
        assert abs(rounded - exact) <= Decimal("0.01")

    def test_rate_scale(self) -> None:
        assert quantize_rate(Decimal("478.123456789")) == Decimal("478.12345679")


class TestToBase:
    @pytest.mark.parametrize(
        ("amount", "rate", "expected"),
        [
            ("12", "478.5", "5742.00"),
            ("1500", "1", "1500.00"),
            ("12.34", "0.00218", "0.03"),
            ("100", "5.6789", "567.89"),
        ],
    )
    def test_conversion(self, amount: str, rate: str, expected: str) -> None:
        assert to_base(Decimal(amount), Decimal(rate)) == Decimal(expected)

    def test_rounds_once_at_the_end(self) -> None:
        """Промежуточное умножение идёт с полной точностью, округление — одно."""
        assert to_base(Decimal("3"), Decimal("0.33333333")) == Decimal("1.00")


class TestValidateAmount:
    @pytest.mark.parametrize("raw", ["0", "-1", "-0.01", "0.001"])
    def test_rejects_non_positive(self, raw: str) -> None:
        with pytest.raises(MoneyError, match="больше нуля"):
            validate_amount(Decimal(raw))

    def test_rejects_too_large(self) -> None:
        with pytest.raises(MoneyError, match="меньше"):
            validate_amount(MAX_AMOUNT)

    def test_rejects_nan(self) -> None:
        with pytest.raises(MoneyError, match="конечным"):
            validate_amount(Decimal("NaN"))

    def test_accepts_and_normalises(self) -> None:
        assert validate_amount(Decimal("1500.004")) == Decimal("1500.00")


class TestFormat:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1500", "1 500"),
            ("245300", "245 300"),
            ("1000000", "1 000 000"),
            ("999", "999"),
            ("0", "0"),
            ("3500.50", "3 500,50"),
            ("3500.05", "3 500,05"),
            ("1500.00", "1 500"),
            ("-354700", "-354 700"),
        ],
    )
    def test_format_amount(self, raw: str, expected: str) -> None:
        assert format_amount(Decimal(raw)) == expected

    @pytest.mark.parametrize(
        ("currency", "expected"),
        [("KZT", "1 500 ₸"), ("RUB", "1 500 ₽"), ("USD", "1 500 $"), ("EUR", "1 500 €")],
    )
    def test_format_money(self, currency: str, expected: str) -> None:
        assert format_money(Decimal("1500"), currency) == expected

    def test_unknown_currency_falls_back_to_code(self) -> None:
        assert currency_symbol("GBP") == "GBP"
        assert format_money(Decimal("10"), "gbp") == "10 GBP"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("354700", "+354 700 ₸"), ("-354700", "-354 700 ₸"), ("0", "0 ₸")],
    )
    def test_signed(self, raw: str, expected: str) -> None:
        assert format_signed_money(Decimal(raw), "KZT") == expected

    def test_api_strings_keep_precision(self) -> None:
        """В JSON суммы уходят строками: у клиента не будет двоичной погрешности."""
        assert money_to_api(Decimal("1500")) == "1500.00"
        assert money_to_api(Decimal("0.1") + Decimal("0.2")) == "0.30"
        assert rate_to_api(Decimal("1")) == "1.00000000"


class TestMisc:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1500", "1500"), ("3500,50", "3500.50"), ("1 500", "1500"), (" 12.5 ", "12.5")],
    )
    def test_decimal_from_str(self, raw: str, expected: str) -> None:
        assert decimal_from_str(raw) == Decimal(expected)

    def test_decimal_from_str_rejects_garbage(self) -> None:
        with pytest.raises(MoneyError):
            decimal_from_str("много")

    @pytest.mark.parametrize(
        ("part", "whole", "expected"),
        [("98000", "245300", 39), ("120000", "120000", 100), ("1", "0", 0), ("0", "100", 0)],
    )
    def test_percent_of(self, part: str, whole: str, expected: int) -> None:
        assert percent_of(Decimal(part), Decimal(whole)) == expected
