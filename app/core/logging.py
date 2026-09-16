"""Структурированное логирование.

Формат — JSON в stdout: так логи читает любой сборщик без регэкспов.
В каждой записи есть корреляционный идентификатор: `request_id` для HTTP
и `update_id` для апдейтов Telegram. Токены и полный текст сообщений
пользователей в логи не попадают (см. :func:`scrub`).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Final

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
update_id_var: ContextVar[int | None] = ContextVar("update_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)

SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "api_token",
        "bot_token",
        "api_token_hash",
        "authorization",
        "secret",
        "webhook_secret",
        "fx_api_key",
        "password",
        "text",
        "message_text",
        "note",
        "caption",
    }
)
MASK: Final[str] = "***"


def scrub(
    _logger: Any,
    _method: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Вырезает секреты и пользовательский текст из записи лога.

    Значения не «маскируются частично» — они заменяются целиком: любая
    утечка части токена всё равно утечка. Вместо текста сообщения остаётся
    его длина: её достаточно для отладки парсера.
    """
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            value = event_dict.pop(key)
            if key in {"text", "message_text", "note", "caption"} and value is not None:
                event_dict[f"{key}_len"] = len(str(value))
            else:
                event_dict[key] = MASK
    return event_dict


def add_correlation_ids(
    _logger: Any,
    _method: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Подмешивает request_id / update_id / user_id из контекста задачи."""
    if (request_id := request_id_var.get()) is not None:
        event_dict.setdefault("request_id", request_id)
    if (update_id := update_id_var.get()) is not None:
        event_dict.setdefault("update_id", update_id)
    if (user_id := user_id_var.get()) is not None:
        event_dict.setdefault("user_id", user_id)
    return event_dict


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Настраивает structlog и перехватывает логи stdlib (uvicorn, sqlalchemy)."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_correlation_ids,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
        scrub,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Логи стандартной библиотеки прогоняем через тот же рендерер.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "aiogram"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Логгер с привязкой к модулю."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


@contextmanager
def bind_update(update_id: int | None, user_id: int | None = None) -> Iterator[None]:
    """Привязывает апдейт Telegram к контексту логирования на время обработки."""
    update_token = update_id_var.set(update_id)
    user_token = user_id_var.set(user_id)
    try:
        yield
    finally:
        update_id_var.reset(update_token)
        user_id_var.reset(user_token)
