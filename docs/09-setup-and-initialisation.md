# 09 · Setup and Initialisation

> **Fast path for this repo (v0.1):** `uv sync --extra dashboard --extra mcp` → `cp .env.example .env` → `make data` (or `make synthetic`) → `make train` → `make user EMAIL=… ROLE=underwriter` → `make api` + `make dashboard`. See README. The rest of this guide describes the full target stack (PostGIS, Redis, MLflow, Alembic) for when you grow into it.


Goal: from an empty machine to a running API + dashboard with sample data.

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| uv | latest | `uv --version` (install: `curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| Docker + Compose | 24+ | `docker compose version` |
| Git | any | `git --version` |
| (optional) psql | 16 | for poking at the DB |

## 2. Create the repo

```bash
mkdir valuegraph-ai && cd valuegraph-ai && git init
uv init --package --name valuegraph
mkdir -p src/valuegraph/{api,engine,ingest,ml,db,workers,mcp_server} \
         dashboard migrations config/cities data/{raw,interim,processed,sample} \
         notebooks tests/{unit,integration,ml,api,load} docs
```

### Dependencies
```bash
uv add fastapi "uvicorn[standard]" "pydantic>=2" pydantic-settings \
       "sqlalchemy>=2" psycopg[binary] alembic geoalchemy2 \
       pandas numpy geopandas shapely h3 \
       catboost scikit-learn shap mapie mlflow pandera \
       "pyjwt" "argon2-cffi" redis arq structlog httpx \
       streamlit folium streamlit-folium weasyprint mcp
uv add --dev pytest pytest-cov pytest-asyncio ruff mypy testcontainers locust bandit pip-audit pre-commit
```
> Learning tip: before adding each library, read its README's first section and write one line in `docs/changelog.md` on *why* you need it.

## 3. `.env.example`

```dotenv
APP_ENV=local
DATABASE_URL=postgresql+psycopg://vg:vg@localhost:5432/valuegraph
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me-generate-with-openssl-rand-hex-32
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=7
MLFLOW_TRACKING_URI=http://localhost:5000
MODEL_DIR=./models
DEFAULT_CITY=hyderabad
INTERVAL_LEVEL=0.8
CORS_ORIGINS=http://localhost:8501
```
`.env` is in `.gitignore`. Generate the secret: `openssl rand -hex 32`.

## 4. `docker-compose.yml` (core)

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    environment: { POSTGRES_USER: vg, POSTGRES_PASSWORD: vg, POSTGRES_DB: valuegraph }
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U vg"], interval: 5s, retries: 10 }
  redis:
    image: redis:7
    ports: ["6379:6379"]
  mlflow:
    image: ghcr.io/mlflow/mlflow:latest
    command: mlflow server --host 0.0.0.0 --backend-store-uri sqlite:///mlflow.db --default-artifact-root /mlruns
    ports: ["5000:5000"]
    volumes: [mlruns:/mlruns]
  api:
    build: .
    command: uvicorn valuegraph.api.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    environment: { DATABASE_URL: postgresql+psycopg://vg:vg@db:5432/valuegraph, REDIS_URL: redis://redis:6379/0 }
    depends_on: { db: { condition: service_healthy } }
    ports: ["8000:8000"]
  worker:
    build: .
    command: arq valuegraph.workers.main.WorkerSettings
    env_file: .env
    depends_on: [db, redis]
  dashboard:
    build: .
    command: streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0
    environment: { API_URL: http://api:8000 }
    ports: ["8501:8501"]
volumes: { pgdata: {}, mlruns: {} }
```
> Inside containers, services reach each other by **service name** (`db`, `api`), not `localhost`.

## 5. Initialise the database

```bash
docker compose up -d db redis
uv run alembic init migrations
# in migrations/env.py: import your Base metadata and geoalchemy2
uv run alembic revision -m "init" --autogenerate
# hand-check the migration: add `op.execute("CREATE EXTENSION IF NOT EXISTS postgis")` at the top
uv run alembic upgrade head
```

## 6. Load sample data & train

```bash
uv run python -m valuegraph.ingest.kaggle_listings data/raw/kaggle/house_price_india.csv
uv run python -m valuegraph.ingest.localities config/cities/hyderabad.yaml
uv run python -m valuegraph.ingest.osm_pois --city hyderabad
uv run python -m valuegraph.ml.train --city hyderabad --segment apartment
```

## 7. Tooling

`Makefile`
```make
up:      ; docker compose up -d
down:    ; docker compose down
test:    ; uv run pytest -q
lint:    ; uv run ruff check . && uv run mypy src
fmt:     ; uv run ruff format .
migrate: ; uv run alembic upgrade head
```

`.pre-commit-config.yaml`: ruff, ruff-format, end-of-file-fixer, detect-private-key. Run `uv run pre-commit install`.

`.github/workflows/ci.yml`: checkout → setup uv → `uv sync` → ruff → mypy → pytest (with a `postgis/postgis` service) → bandit → pip-audit.

## 8. Verify
- `curl localhost:8000/health` → `{"status":"ok"}`
- `localhost:8000/docs` shows endpoints
- `localhost:8501` loads the dashboard
- `localhost:5000` shows the MLflow run
