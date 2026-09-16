"""Границы периодов: считаются в TZ пользователя, отдаются в UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest
from app.domain.models import PeriodKind
from app.domain.periods import (
    TimezoneError,
    custom_bounds,
    is_valid_timezone,
    local_today,
    month_label,
    month_start,
    next_month_start,
    period_bounds,
    period_label,
    prev_month_start,
    to_utc,
    week_start,
)

ANCHOR = date(2026, 9, 16)  # среда


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TestDay:
    @pytest.mark.parametrize(
        ("timezone", "start", "end"),
        [
            ("Asia/Almaty", "2026-09-15T19:00:00Z", "2026-09-16T19:00:00Z"),
            ("Europe/Moscow", "2026-09-15T21:00:00Z", "2026-09-16T21:00:00Z"),
            ("UTC", "2026-09-16T00:00:00Z", "2026-09-17T00:00:00Z"),
            # Часовой пояс UTC+14: локальный день начинается «вчера» по UTC.
            ("Pacific/Kiritimati", "2026-09-15T10:00:00Z", "2026-09-16T10:00:00Z"),
            # UTC-11: локальный день заканчивается «завтра» по UTC.
            ("Pacific/Niue", "2026-09-16T11:00:00Z", "2026-09-17T11:00:00Z"),
        ],
    )
    def test_bounds_shift_with_timezone(self, timezone: str, start: str, end: str) -> None:
        bounds = period_bounds(PeriodKind.day, timezone, anchor=ANCHOR)
        assert iso(bounds.start_utc) == start
        assert iso(bounds.end_utc) == end
        assert bounds.start_local == bounds.end_local == ANCHOR


class TestWeek:
    def test_starts_on_monday(self) -> None:
        bounds = period_bounds(PeriodKind.week, "Asia/Almaty", anchor=ANCHOR)
        assert bounds.start_local == date(2026, 9, 14)
        assert bounds.start_local.weekday() == 0
        assert bounds.end_local == date(2026, 9, 20)
        assert iso(bounds.start_utc) == "2026-09-13T19:00:00Z"

    @pytest.mark.parametrize("day", range(14, 21))
    def test_any_day_of_week_gives_same_bounds(self, day: int) -> None:
        bounds = period_bounds(PeriodKind.week, "UTC", anchor=date(2026, 9, day))
        assert bounds.start_local == date(2026, 9, 14)
        assert bounds.end_local == date(2026, 9, 20)

    def test_week_start_helper(self) -> None:
        assert week_start(date(2026, 9, 14)) == date(2026, 9, 14)
        assert week_start(date(2026, 9, 20)) == date(2026, 9, 14)


class TestMonth:
    def test_month(self) -> None:
        bounds = period_bounds(PeriodKind.month, "Asia/Almaty", anchor=ANCHOR)
        assert bounds.start_local == date(2026, 9, 1)
        assert bounds.end_local == date(2026, 9, 30)
        assert iso(bounds.start_utc) == "2026-08-31T19:00:00Z"
        assert iso(bounds.end_utc) == "2026-09-30T19:00:00Z"

    def test_prev_month(self) -> None:
        bounds = period_bounds(PeriodKind.prev_month, "Asia/Almaty", anchor=ANCHOR)
        assert bounds.start_local == date(2026, 8, 1)
        assert bounds.end_local == date(2026, 8, 31)
        assert iso(bounds.end_utc) == "2026-08-31T19:00:00Z"

    def test_prev_month_crosses_year(self) -> None:
        bounds = period_bounds(PeriodKind.prev_month, "UTC", anchor=date(2026, 1, 10))
        assert bounds.start_local == date(2025, 12, 1)
        assert bounds.end_local == date(2025, 12, 31)

    def test_month_of_february_in_leap_year(self) -> None:
        bounds = period_bounds(PeriodKind.month, "UTC", anchor=date(2028, 2, 5))
        assert bounds.end_local == date(2028, 2, 29)

    @pytest.mark.parametrize(
        ("anchor", "start", "following"),
        [
            (date(2026, 9, 16), date(2026, 9, 1), date(2026, 10, 1)),
            (date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
        ],
    )
    def test_month_helpers(self, anchor: date, start: date, following: date) -> None:
        assert month_start(anchor) == start
        assert next_month_start(anchor) == following

    def test_prev_month_start_helper(self) -> None:
        assert prev_month_start(date(2026, 1, 31)) == date(2025, 12, 1)


class TestDaylightSaving:
    def test_short_day_when_clocks_jump_forward(self) -> None:
        """В Берлине 29.03.2026 сутки длятся 23 часа — интервал это учитывает."""
        bounds = period_bounds(PeriodKind.day, "Europe/Berlin", anchor=date(2026, 3, 29))
        assert bounds.end_utc - bounds.start_utc == timedelta(hours=23)

    def test_long_day_when_clocks_fall_back(self) -> None:
        bounds = period_bounds(PeriodKind.day, "Europe/Berlin", anchor=date(2026, 10, 25))
        assert bounds.end_utc - bounds.start_utc == timedelta(hours=25)

    def test_missing_local_midnight_keeps_days_continuous(self) -> None:
        """В Сантьяго перевод часов в 00:00: полуночи нет, но дни не рвутся."""
        first = period_bounds(PeriodKind.day, "America/Santiago", anchor=date(2026, 9, 5))
        second = period_bounds(PeriodKind.day, "America/Santiago", anchor=date(2026, 9, 6))
        assert first.end_utc == second.start_utc
        assert second.end_utc - second.start_utc == timedelta(hours=23)

    def test_month_of_dst_change_covers_every_day(self) -> None:
        bounds = period_bounds(PeriodKind.month, "Europe/Berlin", anchor=date(2026, 3, 10))
        days = [
            period_bounds(PeriodKind.day, "Europe/Berlin", anchor=date(2026, 3, d))
            for d in range(1, 32)
        ]
        assert days[0].start_utc == bounds.start_utc
        assert days[-1].end_utc == bounds.end_utc
        # Дни идут вплотную: без дыр и без наложений.
        assert all(a.end_utc == b.start_utc for a, b in pairwise(days))


class TestTimezoneHandling:
    def test_local_today_across_utc_midnight(self) -> None:
        """23:30 UTC — в Алматы уже следующий день, в Гонолулу ещё предыдущий."""
        moment = datetime(2026, 9, 16, 23, 30, tzinfo=UTC)
        assert local_today("Asia/Almaty", moment) == date(2026, 9, 17)
        assert local_today("UTC", moment) == date(2026, 9, 16)
        assert local_today("Pacific/Honolulu", moment) == date(2026, 9, 16)

        moment = datetime(2026, 9, 16, 0, 30, tzinfo=UTC)
        assert local_today("Pacific/Honolulu", moment) == date(2026, 9, 15)

    def test_unknown_timezone(self) -> None:
        assert is_valid_timezone("Asia/Almaty")
        assert not is_valid_timezone("Asia/Almata")
        with pytest.raises(TimezoneError):
            to_utc(ANCHOR, "Mars/Olympus")


class TestCustomAndLabels:
    def test_custom_bounds_include_last_day(self) -> None:
        bounds = custom_bounds(date(2026, 9, 1), date(2026, 9, 3), "UTC")
        assert iso(bounds.start_utc) == "2026-09-01T00:00:00Z"
        assert iso(bounds.end_utc) == "2026-09-04T00:00:00Z"

    def test_custom_bounds_reject_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="раньше начала"):
            custom_bounds(date(2026, 9, 3), date(2026, 9, 1), "UTC")

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (PeriodKind.day, "16.09.2026"),
            (PeriodKind.week, "Неделя 14.09 — 20.09.2026"),
            (PeriodKind.month, "Сентябрь 2026"),
            (PeriodKind.prev_month, "Август 2026"),
        ],
    )
    def test_labels(self, kind: PeriodKind, expected: str) -> None:
        bounds = period_bounds(kind, "UTC", anchor=ANCHOR)
        assert period_label(bounds) == expected

    def test_month_label(self) -> None:
        assert month_label(date(2026, 1, 1)) == "Январь 2026"
