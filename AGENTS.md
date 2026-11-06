# AGENTS.md

Instructions for AI coding assistants working in this repository.

## Project in one paragraph
ValueGraph AI is an explainable property valuation engine (AVM) for Indian housing finance: FastAPI + CatBoost + an in-memory comparables index, SQLAlchemy (SQLite locally, Postgres in Docker), a vanilla-JS web app served by the API, MCP server. Every valuation returns a value **with** a calibrated range, rupee factors, comparables and a reliability grade — or explicitly declines.

## Working style (owner preference)
The owner is learning while building. Explain the concept and plan first, point out *why* a bug happened, and prefer focused changes with `# TODO(you):` markers for new features unless asked to write everything.

## Commands
```bash
uv sync --extra mcp
make test                     # pytest (trains a small model on synthetic data in a temp dir)
make lint                     # ruff check + format check
make train CSV=...            # retrain + regenerate models/eval_report.json
make api                      # API + web app on :8000
```
Run `make lint` and `make test` before declaring a task done.

## Where things go
- HTTP, auth, persistence → `src/valuegraph/api/`. No ML in route functions; call `engine.value_property`.
- Features: **only** `model.build_features` — training and serving share it.
- Web UI → public site in `src/valuegraph/site/`, signed-in app in `src/valuegraph/web/`. Build DOM with the `h()` helper (text only); never assign API data to `innerHTML`. Colours, type and spacing come from `web/tokens.css` only. Read `docs/DESIGN-SYSTEM.md` before any UI change.
- Policy thresholds → `config.py` (env-overridable), never hard-coded.
- New data source → loader in `data.py` (or a new module) + entry in `docs/13-integrations.md`.

## Hard rules
- Never mix price concepts. Listings carry `price_source="asking"`. Never use guideline value as a training label.
- Never report proxy-label metrics as fair-market accuracy. `ground_truth` must stay truthful.
- No leakage: training-row comparables exclude the row and its re-listings (`dup_group`, `exclude_self=True`); splits keep a `dup_group` on one side; blend weight is chosen on validation, intervals on calibration, metrics on test/geo-holdout only. Add a time split when dated data exists.
- Money in integer rupees; area always with its `area_type`.
- Routes that write must `db.commit()` before returning (dependency teardown runs after the response).
- No secrets, real borrower data or raw scraped data in git. `models/` and `data/raw/` are git-ignored.
- Only load model files produced by our own training job (joblib unpickles code).
- Don't weaken validation, auth, throttling or audit logging to make a test pass.

## Definition of done
Tests added/updated · lint clean · `docs/API.md` updated for API changes · eval report regenerated and compared against baselines for model changes.
