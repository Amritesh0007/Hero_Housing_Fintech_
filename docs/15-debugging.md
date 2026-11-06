# 15 · Debugging

## 1. Method (use every time)
1. **Reproduce** with the smallest input — a single valuation request or 10-row CSV.
2. **Locate** the stage: input → normalize → geo → comps → features → model → interval → explain → grade → persist.
3. **Compare** with a known-good case (same locality, valued correctly).
4. **Fix the root cause**, then add a test that would have caught it.

Every valuation stores inputs, feature hash, model version and data snapshot, so you can **replay** it:
```bash
uv run python -m valuegraph.tools.replay VAL-2026-000123 --verbose   # prints each stage's output
```
(Build this tool early — it pays back many times.)

## 2. Observability
- `structlog` JSON logs with `trace_id` on every line; same id returned in error responses.
- Log per stage timing: `stage=comps duration_ms=41 n=12`.
- `/metrics`: request latency, valuations by grade, model load status.
- MLflow: compare runs side by side when metrics move.

## 3. Common problems

### Data & geo
| Symptom | Likely cause | Check |
|---|---|---|
| Comps are hundreds of km away / none found | lat/lon swapped | `ST_MakePoint(lon, lat)`; plot points on a map |
| Distances look like 0.004 | Using `geometry` (degrees) not `geography` (metres) | Cast `::geography` |
| Same flat appears 5× in comps | Dedup not applied or group excluded wrong | `dup_group_id` populated? |
| Prices off by 100,000× | Lakh/crore parsing; ₹ stored in lakhs | Unit test parser; DB holds rupees |
| One locality wildly off | Area type mixed (super built-up treated as carpet) | Group error by `area_type` |
| Guideline rate ~9× too high/low | per-sqyd vs per-sqft (1 sqyd = 9 sqft) | `rate_unit` conversion |

### Model
| Symptom | Likely cause | Check |
|---|---|---|
| Test metrics amazing, demo results bad | **Leakage** — random split, or comps include the row itself / future listings | Time split; leakage test; recompute locality medians from past only |
| `CatBoostError` on categorical | NaN or float in a cat column | Fill with `"__missing__"`, cast to str |
| Predictions all near city median | Features empty at serving (train/serve skew) | Parity test; log feature vector for one request |
| Interval coverage far below target | Calibrated on train residuals, or segments too fine | Use dedicated calibration split |
| Bigger flat valued lower | No monotone constraint / wrong sign | Monotonicity test |
| SHAP slow (> 1 s) | Using KernelExplainer | CatBoost's native `get_feature_importance(type='ShapValues')` or `TreeExplainer` |

### Backend & infra
| Symptom | Likely cause | Check |
|---|---|---|
| API can't reach DB in Docker | `localhost` inside container | Use service name `db` |
| Alembic drops/ignores geometry columns | GeoAlchemy2 not imported in `env.py` | Import it; review autogenerate diff by hand |
| `extension "postgis" does not exist` | Missing `CREATE EXTENSION` | First line of first migration |
| 401 with a valid-looking JWT | Clock skew / wrong `aud`/`iss` / expired | Decode at jwt.io (non-prod tokens only) |
| Batch job "stuck" | Worker not running or Redis URL differs | `docker compose logs worker` |
| Streamlit shows stale results | `st.cache_data` caching API calls | Clear cache; key on inputs |
| Model loads twice, high memory | Loaded per request | Load once in FastAPI lifespan |

## 4. Debugging checklist for "this value looks wrong"
1. Open the report: reliability grade and reason codes — did the engine already warn?
2. Inspect comps on the map: right area? right type? recent?
3. Compare model estimate vs comp estimate — which one is off?
4. Read top SHAP factors — any absurd contribution (e.g. +₹40 L for floor)?
5. Check input: area type, floor, age, pin location.
6. Replay with the previous model version — regression or long-standing?
7. If the data is wrong, fix at ingestion + add a pandera rule; if the model is wrong, add a behaviour test.

## 5. Tools
- `pdb` / `breakpoint()`, VS Code debugger attached to uvicorn (`--reload` off).
- `EXPLAIN ANALYZE` for slow spatial queries; confirm GiST index used.
- `py-spy top --pid <api pid>` for CPU hot spots.
- QGIS or `geopandas.explore()` to eyeball spatial data.
