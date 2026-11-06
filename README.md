# KSHETRA — Property Intelligence

*Every land has a story. Every property has a value.* (Codebase and Python package are still named `valuegraph`.)

**Explainable Automated Valuation Model (AVM) for Indian residential property** — Hero Housing Fintech Challenge, Problem Statement 1.

For any apartment, ValueGraph returns:
- an **indicative value** and ₹/sqft,
- a **calibrated low / high range** (split-conformal, 80% by default),
- the **factors** behind the value, in rupees, that add up exactly to the value,
- the **comparable listings** used, and a **guideline-rate benchmark** if you supply the rate,
- a **reliability grade** and **route** (auto / desktop review / physical inspection),
- **LTV on the low bound** against tiered caps,
- a **hash-chained audit trail**.

> **Status: prototype.** The model is trained on *asking* prices. Every metric it reports is asking-price prediction performance, not fair-market accuracy — the API, web app and eval report all say so. Validate on registered transactions or lender valuation history before any lending use.

## Quick start

```bash
uv sync --extra mcp
cp .env.example .env
# Put the listings file at data/raw/house_prices.csv (India House Price dataset, ~187k rows)
make train                                      # cleans, trains, evaluates vs baselines → models/
# no data handy? make synthetic && make train CSV=data/sample/synthetic_train.csv   (dev only; never quote its metrics)
make user EMAIL=you@hhf.test ROLE=underwriter   # prompts for a password (≥12 chars)
make api                                        # public site at http://localhost:8000, lender app at /app/, API docs at /docs
make test
```
On a Mac you can instead double-click `run-demo.command`, which does all of the above with demo logins.

## Layout

```
src/valuegraph/
  config.py        settings + valuation policy thresholds (env-overridable)
  data.py          listings loader & cleaning (dedup, price/area/floor parsing, locality resolution), synthetic sample
  model.py         locality comparables, shared feature builder, CatBoost, blend, conformal, SHAP→₹
  engine.py        value → grade → route → LTV
  train.py         splits (unseen-locality holdout + relisting-safe random), baselines, evaluation report
  cli.py           synthetic | train | create-user | create-api-key
  mcp_server.py    MCP tools for AI assistants (thin client of the REST API)
  api/             FastAPI app, auth (JWT + API keys, RBAC, throttling), DB models, audit chain
  web/             the lender web app (vanilla HTML/CSS/JS, served at /app)
tests/             data, model behaviour, policy, API, auth, audit, web serving
docs/              PRD, design, architecture, API, guides 08–15
```

## How it works (one paragraph)
Clean the listings (66% of the raw rows are repeats) → resolve each title to a city + locality → for every property find the most similar listings in the same locality by size and BHK and compute a weighted local ₹/sqft, plus the same for other listings in the same society (building) → CatBoost predicts log ₹/sqft from locality, size, floor, furnishing, parking, views and those comparable features → blend with the comparable estimate (weight learned on validation) → split-conformal interval per comparable-density bucket → SHAP values converted to rupees → policy rules grade and route the case.

## Current results (India House Price listings, asking prices, with locality locations)

| | ValueGraph | Similar listings only | Linear model | City average × area |
|---|---|---|---|---|
| Median error, held-out listings | **12.4%** | 16.0% | 20.8% | 22.7% |
| Within ±10% | **42%** | 34% | 25% | 24% |
| Median error, unseen localities | **17.7%** | 22.4% | 21.8% | 23.8% |

**Reliability grades are measured promises.** Training sets each grade's cut-off on a calibration split (High: median error ≤10%, Medium: ≤15%); the table checks them on held-out listings:

| Grade | Share | Median error | Within ±10% | Inside 80% range | Route |
|---|---|---|---|---|---|
| High | 47% | 9.3% | 53% | 80% | Auto-eligible (if LTV within cap) |
| Medium | 21% | 14.4% | 37% | 83% | Desktop review |
| Low | 31% | 17.9% | 30% | 79% | Site inspection |

80% ranges cover 80% of held-out prices. The biggest single gain came from same-building evidence: other listings in the same society (building / project) are a feature, which moved 26% of cases from Medium to High at the same accuracy. Where a near-identical flat in the same building is on file (same BHK and sale type, size within ~3.5%), the estimate leans almost entirely on it (weight learned on validation): on those flats the model alone was 7.2% off while the twin's asking rate was 5.4% off. Asking prices of such twins differ by ~5.5% at the median, which is the practical floor for this data. Adding locality locations mainly helps unseen localities (19.1% → 18.1%); known localities are unchanged because their own listings already carry the location signal. Unseen localities are always graded Low. These are asking-price metrics, not fair-market accuracy.

## Docs
[PRD](docs/PRD.md) · [Design](docs/Design.md) · [Design system](docs/design-system.md) · [Architecture](docs/architecture.md) · [Database](docs/database.md) · [API](docs/API.md) · [AGENTS](AGENTS.md) · guides [08](docs/08-project-implementation.md) [09](docs/09-setup-and-initialisation.md) [10](docs/10-feature-development.md) [11](docs/11-authentication-and-security.md) [12](docs/12-mcp-connection.md) [13](docs/13-integrations.md) [14](docs/14-testing-and-qa.md) [15](docs/15-debugging.md)

## Data & citation
Listings: "India House Price" dataset (Kaggle, `house_prices.csv`, scraped portal listings, asking prices). Check its licence before commercial use and cite it in your submission. It has no coordinates; locality centroids for 1,400 of ~2,100 localities come from OpenStreetMap Nominatim (`data/geo/locality_coords.csv`, © OpenStreetMap contributors, ODbL), which drive the maps and the neighbourhood-price features. Guideline rates are an optional input per request.

## Disclaimer
Indicative estimates for decision support. Not a certified valuation.
