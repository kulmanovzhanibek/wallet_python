"""Границы периодов в часовом поясе пользователя.

Все метки времени в БД — UTC (`TIMESTAMPTZ`). Но «месяц» у пользователя в
Алматы и у пользователя в Москве — это разные интервалы UTC, поэтому
границы всегда считаются от локальной даты и только потом переводятся в UTC.
Интервалы полуоткрытые: [start, end). Так операция, попавшая ровно в
полночь, учитывается один раз и ни один день не теряется.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.models import PeriodBounds, PeriodKind

MONTHS_RU: Final[tuple[str, ...]] = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

COMMON_TIMEZONES: Final[tuple[str, ...]] = ("Asia/Almaty", "Europe/Moscow", "UTC")

DECEMBER: Final = 12


class TimezoneError(ValueError):
    """Неизвестный часовой пояс."""


def load_timezone(name: str) -> ZoneInfo:
    """Загружает IANA-зону, понятно ругаясь на опечатки."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimezoneError(f"неизвестный часовой пояс: {name!r}") from exc


def is_valid_timezone(name: str) -> bool:
    """Проверка имени зоны — для ввода «Другой» в боте."""
    try:
        load_timezone(name)
    except TimezoneError:
        return False
    return True


def local_now(timezone: str, now_utc: datetime | None = None) -> datetime:
    """Текущее локальное время пользователя."""
    moment = now_utc or datetime.now(UTC)
    return moment.astimezone(load_timezone(timezone))


def local_today(timezone: str, now_utc: datetime | None = None) -> date:
    """Сегодняшняя дата в часовом поясе пользователя."""
    return local_now(timezone, now_utc).date()


def to_utc(local_date: date, timezone: str) -> datetime:
    """Локальная полночь → момент в UTC.

    Если в этот день местная полночь не существует (перевод часов ровно в
    00:00, так бывает, например, в Сантьяго), `zoneinfo` подставляет
    смещение, действующее после перехода, — интервал остаётся непрерывным.
    """
    tz = load_timezone(timezone)
    return datetime.combine(local_date, time.min, tzinfo=tz).astimezone(UTC)


def month_start(local_date: date) -> date:
    """Первое число месяца — ключ периода бюджетов."""
    return local_date.replace(day=1)


def next_month_start(local_date: date) -> date:
    """Первое число следующего месяца."""
    if local_date.month == DECEMBER:
        return date(local_date.year + 1, 1, 1)
    return date(local_date.year, local_date.month + 1, 1)


def prev_month_start(local_date: date) -> date:
    """Первое число предыдущего месяца."""
    first = month_start(local_date)
    return month_start(first - timedelta(days=1))


def week_start(local_date: date) -> date:
    """Понедельник недели, в которую попадает дата."""
    return local_date - timedelta(days=local_date.weekday())


def period_bounds(
    kind: PeriodKind,
    timezone: str,
    *,
    anchor: date | None = None,
    now_utc: datetime | None = None,
) -> PeriodBounds:
    """Границы периода вокруг `anchor` (по умолчанию — сегодня у пользователя)."""
    local_date = anchor or local_today(timezone, now_utc)

    match kind:
        case PeriodKind.day:
            start, end = local_date, local_date + timedelta(days=1)
        case PeriodKind.week:
            start = week_start(local_date)
            end = start + timedelta(days=7)
        case PeriodKind.month:
            start = month_start(local_date)
            end = next_month_start(local_date)
        case PeriodKind.prev_month:
            start = prev_month_start(local_date)
            end = month_start(local_date)

    return PeriodBounds(
        start_utc=to_utc(start, timezone),
        end_utc=to_utc(end, timezone),
        start_local=start,
        end_local=end - timedelta(days=1),
        timezone=timezone,
        kind=kind,
    )


def custom_bounds(from_local: date, to_local: date, timezone: str) -> PeriodBounds:
    """Произвольный интервал по локальным датам, `to_local` включительно."""
    if to_local < from_local:
        raise ValueError("конец периода раньше начала")
    return PeriodBounds(
        start_utc=to_utc(from_local, timezone),
        end_utc=to_utc(to_local + timedelta(days=1), timezone),
        start_local=from_local,
        end_local=to_local,
        timezone=timezone,
        kind=PeriodKind.day,
    )


def month_label(local_date: date) -> str:
    """`Сентябрь 2026`."""
    return f"{MONTHS_RU[local_date.month - 1]} {local_date.year}"


def period_label(bounds: PeriodBounds) -> str:
    """Заголовок отчёта, понятный без пояснений."""
    match bounds.kind:
        case PeriodKind.day:
            if bounds.start_local == bounds.end_local:
                return bounds.start_local.strftime("%d.%m.%Y")
            return (
                f"{bounds.start_local.strftime('%d.%m.%Y')}"
                f" — {bounds.end_local.strftime('%d.%m.%Y')}"
            )
        case PeriodKind.week:
            return (
                f"Неделя {bounds.start_local.strftime('%d.%m')}"
                f" — {bounds.end_local.strftime('%d.%m.%Y')}"
            )
        case PeriodKind.month | PeriodKind.prev_month:
            return month_label(bounds.start_local)
