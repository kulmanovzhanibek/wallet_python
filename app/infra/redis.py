"""Redis: FSM бота, кэш курсов, идемпотентность, лимиты.

Один клиент на процесс — у него внутри свой пул соединений.
`decode_responses=True`: мы храним только строки и JSON, а aiogram
`RedisStorage` умеет работать и со str, и с bytes.
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    # redis-py объявляет from_url как `-> Any`, поэтому фиксируем тип явно.
    client: Redis = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
        health_check_interval=30,
    )
    return client


# ASYNC109: см. комментарий в infra/db.py — таймаут задаёт контракт /ready.
async def redis_healthcheck(redis: Redis, timeout: float = 1.0) -> bool:  # noqa: ASYNC109
    """`PING` с таймаутом — для `/ready`."""
    try:
        async with asyncio.timeout(timeout):
            await redis.ping()
    except Exception:
        return False
    return True
