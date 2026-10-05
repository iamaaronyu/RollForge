.PHONY: install check test api web infra worker-check scheduler-check
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
install:
	uv sync --all-packages --locked
	npm --prefix apps/hub-web ci
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest
	npm --prefix apps/hub-web run typecheck
	npm --prefix apps/hub-web run build
test:
	uv run pytest
api:
	uv run uvicorn rollforge_api.main:app --reload --host 127.0.0.1 --port 8000
web:
	npm --prefix apps/hub-web run dev
infra:
	docker compose --env-file .env -f infra/docker-compose/compose.yaml up -d
worker-check:
	uv run python -m rollforge_worker --check
scheduler-check:
	uv run python -m rollforge_scheduler --check
