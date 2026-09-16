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

## Что нужно установить

**uv** — менеджер зависимостей и Python. Python ставить отдельно не нужно:
`uv venv --python 3.12` скачает нужную версию сам.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env      # или откройте новый терминал
uv --version                 # проверка
```

Установщик кладёт готовый бинарник в `~/.local/bin` — ни компилятор, ни
Xcode Command Line Tools ему не нужны.

<details>
<summary>Если установщик недоступен</summary>

`uv` публикует готовые колёса под macOS (Apple Silicon и Intel) и Linux и
требует всего лишь Python ≥ 3.8, так что системного `python3` достаточно:

```bash
python3 -m pip install --user uv
export PATH="$HOME/.local/bin:$PATH"
```

`brew install uv` на macOS тоже работает, но на старых версиях системы
Homebrew может посчитать конфигурацию неподдерживаемой (Tier 3), полезть
собирать `uv` из исходников и упасть на устаревших Command Line Tools.
В этом случае проще взять готовый бинарник одним из способов выше, а brew
починить отдельно: `sudo rm -rf /Library/Developer/CommandLineTools &&
sudo xcode-select --install`.

</details>

**Docker** — Postgres и Redis локально: Docker Desktop на macOS,
`docker` + `docker compose` на Linux. На этапе 1 он не обязателен:
`make check` проходит без него, потому что слой `domain` тестируется без БД.

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
