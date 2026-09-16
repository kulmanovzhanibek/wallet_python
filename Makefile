# Короткие команды для рутины. Все они предполагают venv в ./.venv
.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV := .venv/bin
COMPOSE := docker compose

.PHONY: help install up down logs psql redis test lint fmt typecheck check migrate revision

help:  ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Создать venv и установить зависимости
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	$(VENV)/pre-commit install

up:  ## Поднять dev-окружение (Postgres, Redis)
	$(COMPOSE) up -d --wait

down:  ## Остановить dev-окружение
	$(COMPOSE) down

logs:  ## Логи dev-окружения
	$(COMPOSE) logs -f --tail=100

psql:  ## Консоль psql внутри контейнера
	$(COMPOSE) exec db psql -U koshelek -d koshelek

redis:  ## Консоль redis-cli внутри контейнера
	$(COMPOSE) exec redis redis-cli

test:  ## Тесты
	$(VENV)/pytest

lint:  ## ruff + mypy --strict
	$(VENV)/ruff check app tests scripts
	$(VENV)/ruff format --check app tests scripts
	$(VENV)/mypy app tests scripts

fmt:  ## Отформатировать код
	$(VENV)/ruff format app tests scripts
	$(VENV)/ruff check --fix app tests scripts

typecheck:  ## Только mypy
	$(VENV)/mypy app tests scripts

check: lint test  ## Всё, что проверяет CI

migrate:  ## Применить миграции
	$(VENV)/alembic upgrade head

revision:  ## Новая миграция: make revision m="описание"
	$(VENV)/alembic revision --autogenerate -m "$(m)"
