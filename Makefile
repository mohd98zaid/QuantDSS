.PHONY: up down logs migrate seed history test test-unit lint shell-be shell-db

up:
	docker-compose up -d

up-dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

migrate:
	docker-compose exec backend alembic upgrade head

seed:
	docker-compose exec backend python -m scripts.seed_defaults

history:
	docker-compose exec backend python -m scripts.download_history

test:
	docker-compose exec backend pytest tests/ -v

test-unit:
	docker-compose exec backend pytest tests/unit/ -v

lint:
	docker-compose exec backend ruff check . && docker-compose exec backend mypy .

shell-be:
	docker-compose exec backend bash

shell-db:
	docker-compose exec postgres psql -U quant quantdb
