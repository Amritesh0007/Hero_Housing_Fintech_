.PHONY: install synthetic train test lint api up down user

install:   ; uv sync --extra mcp
synthetic: ; uv run valuegraph synthetic
train:     ; uv run valuegraph train --csv $(or $(CSV),data/raw/house_prices.csv)
test:      ; uv run pytest -q
lint:      ; uv run ruff check src tests && uv run ruff format --check src tests
api:       ; uv run uvicorn valuegraph.api.main:app --reload --port 8000   # web app at http://localhost:8000
user:      ; uv run valuegraph create-user $(EMAIL) $(ROLE)
up:        ; docker compose up -d --build
down:      ; docker compose down
