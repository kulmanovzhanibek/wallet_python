# Короткие команды для рутины. Все они предполагают venv в ./.venv
.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV := .venv/bin
COMPOSE := docker compose

# Каталоги, которые проверяют ruff и mypy. scripts/ появится на этапе 6 —
# подключаем его только когда в нём есть код, иначе mypy падает на пустом каталоге.
SRC := app tests $(if $(wildcard scripts/*.py),scripts,)

.PHONY: help install up down logs psql redis test lint fmt typecheck check migrate revision
.PHONY: require-uv require-docker require-venv

help:  ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

require-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "✗ uv не найден — это менеджер зависимостей проекта."; \
		echo "  Установите готовый бинарник (Xcode и компилятор не нужны):"; \
		echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		echo "    source ~/.local/bin/env   # или откройте новый терминал"; \
		echo "  Если установщик недоступен: python3 -m pip install --user uv"; \
		exit 1; }

require-docker:
	@docker info >/dev/null 2>&1 || { \
		echo "✗ Docker не отвечает. Запустите Docker Desktop и повторите."; \
		exit 1; }

require-venv:
	@test -x $(VENV)/python || { \
		echo "✗ Окружение не собрано. Выполните: make install"; \
		exit 1; }

install: require-uv  ## Создать venv и установить зависимости
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	$(VENV)/pre-commit install
	@echo "✓ Готово. Дальше: make up && make check"

up: require-docker  ## Поднять dev-окружение (Postgres, Redis)
	$(COMPOSE) up -d --wait

down:  ## Остановить dev-окружение
	$(COMPOSE) down

logs:  ## Логи dev-окружения
	$(COMPOSE) logs -f --tail=100

psql:  ## Консоль psql внутри контейнера
	$(COMPOSE) exec db psql -U koshelek -d koshelek

redis:  ## Консоль redis-cli внутри контейнера
	$(COMPOSE) exec redis redis-cli

test: require-venv  ## Тесты
	$(VENV)/pytest

lint: require-venv  ## ruff + mypy --strict
	$(VENV)/ruff check $(SRC)
	$(VENV)/ruff format --check $(SRC)
	$(VENV)/mypy $(SRC)

fmt: require-venv  ## Отформатировать код
	$(VENV)/ruff format $(SRC)
	$(VENV)/ruff check --fix $(SRC)

typecheck: require-venv  ## Только mypy
	$(VENV)/mypy $(SRC)

check: lint test  ## Всё, что проверяет CI

migrate: require-venv  ## Применить миграции
	$(VENV)/alembic upgrade head

revision: require-venv  ## Новая миграция: make revision m="описание"
	$(VENV)/alembic revision --autogenerate -m "$(m)"
