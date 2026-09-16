# Кошелёк

Telegram-бот учёта личных финансов + REST API. Асинхронный бэкенд на
FastAPI + aiogram 3 + SQLAlchemy 2.0 (asyncpg), PostgreSQL 16, Redis, arq.

> Проект собирается по этапам. Текущее состояние и план — в
> [`docs/ROADMAP.md`](docs/ROADMAP.md). Сейчас готов **этап 1: каркас**.

## Что уже работает

- конфигурация из переменных окружения с падением на старте, если
  обязательная переменная не задана;
- JSON-логи с корреляционными идентификаторами, без секретов и текста
  сообщений пользователей;
- доменный слой: деньги на `Decimal` с банковским округлением и границы
  периодов в часовом поясе пользователя;
- `docker compose` с Postgres 16 и Redis 7;
- `ruff`, `mypy --strict`, `pytest` — зелёные, всё это же гоняет CI.

## Запуск за 5 минут

```bash
git clone https://github.com/kulmanovzhanibek/wallet_python.git
cd wallet_python

cp .env.example .env         # BOT_TOKEN понадобится на этапе 3
make install                 # venv + зависимости + pre-commit
make up                      # Postgres и Redis в docker compose
make check                   # ruff + mypy --strict + pytest
```

Без Docker: подойдут локальные Postgres 16 и Redis, достаточно поправить
`DATABASE_URL` и `REDIS_URL` в `.env`.

## Команды

```
make install   # venv и зависимости
make up/down   # dev-окружение (Postgres, Redis)
make test      # pytest
make lint      # ruff + mypy --strict
make check     # всё, что проверяет CI
make psql      # консоль psql
```

## Структура

```
app/
  core/      конфиг и логирование
  domain/    чистая логика: деньги, периоды, парсер, сущности
  infra/     движок БД, Redis, внешние клиенты
  ...        (services, repositories, api, bot, workers — по мере этапов)
tests/unit/  тесты без БД
docs/        этапы, ADR, замеры
```

### Правила слоёв

- `domain` не импортирует фреймворки и БД — только стандартная библиотека;
- `services` содержат бизнес-логику и управляют транзакциями БД;
- `repositories` только читают и пишут, без бизнес-правил;
- хэндлеры бота и роутеры API тонкие: валидация → сервис → форматирование.

## Стек и почему так

| Решение | Причина |
|---------|---------|
| `Decimal` для денег | `0.1 + 0.2 != 0.3`; на сумме тысячи операций расхождение видно пользователю |
| `ROUND_HALF_EVEN` | ошибка округления не накапливается в одну сторону |
| суммы в JSON строками | JS-клиент не превратит `1500.10` в double |
| `TIMESTAMPTZ` в UTC | «месяц» в Алматы и в Москве — разные интервалы UTC |
| один engine и Redis на процесс | пул соединений живёт вместе с процессом, а не с запросом |
