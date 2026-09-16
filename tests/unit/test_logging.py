"""Логи не должны содержать токенов и текста сообщений пользователей."""

from __future__ import annotations

import pytest

from app.core.logging import MASK, add_correlation_ids, request_id_var, scrub, user_id_var


@pytest.mark.parametrize(
    "key",
    ["token", "bot_token", "api_token_hash", "Authorization", "webhook_secret", "fx_api_key"],
)
def test_secrets_are_masked(key: str) -> None:
    result = scrub(None, "info", {"event": "test", key: "wlt_supersecretvalue"})
    assert result[key] == MASK
    assert "wlt_supersecretvalue" not in str(result)


@pytest.mark.parametrize("key", ["text", "note", "message_text", "caption"])
def test_user_text_is_replaced_with_its_length(key: str) -> None:
    result = scrub(None, "info", {"event": "update", key: "кофе 1500"})
    assert key not in result
    assert result[f"{key}_len"] == 9


def test_other_fields_survive() -> None:
    result = scrub(None, "info", {"event": "tx.created", "user_id": 7, "amount": "1500.00"})
    assert result == {"event": "tx.created", "user_id": 7, "amount": "1500.00"}


def test_correlation_ids_come_from_context() -> None:
    token = request_id_var.set("01J9ZTEST")
    user_token = user_id_var.set(42)
    try:
        result = add_correlation_ids(None, "info", {"event": "x"})
    finally:
        request_id_var.reset(token)
        user_id_var.reset(user_token)
    assert result["request_id"] == "01J9ZTEST"
    assert result["user_id"] == 42


def test_correlation_ids_absent_outside_request() -> None:
    assert add_correlation_ids(None, "info", {"event": "x"}) == {"event": "x"}
