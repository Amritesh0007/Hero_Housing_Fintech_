# 10 · Feature Development

Build in this order — each feature unblocks the next. Each has a **concept to learn**, a **build outline**, and **acceptance criteria**. Code here is skeletons; the `TODO(you)` parts are where the learning is.

---

## F1 · Data ingestion & cleaning
**Concept:** garbage in, garbage out. Most AVM error comes from bad data, not bad models.

Outline:
1. One module per source: `read_raw() → validate() → normalize() → load()`.
2. Validate with a `pandera` schema (types, ranges, allowed categories).
3. Normalize: price strings ("85 Lac", "1.2 Cr") → rupees; area units (sqyd/sqm → sqft); area type.
4. Dedup: same `dup_group_id` if distance < 50 m, area within 3%, price within 5%.
5. Flag outliers per locality (₹/sqft outside median ± 3·MAD) — flag, don't delete.

Acceptance: re-running ingestion is idempotent; data-quality report shows counts in/out per step.

```python
def parse_price_inr(text: str) -> int | None:
    """'85 Lac' -> 8_500_000, '1.2 Cr' -> 12_000_000."""
    # TODO(you): handle Lac/Lakh/L, Cr/Crore, commas, '₹', blanks
```
Write the test first with 8–10 messy real strings from the dataset.

## F2 · Area normalization
**Concept:** carpet < built-up < super built-up. Mixing them is a silent 20–30% error.

Store original value + type; derive `area_builtup_sqft` with configurable loading factors per city (e.g. super→built-up ≈ ÷1.25). Make the factors config so a valuer can change them.

## F3 · Geo features
**Concept:** location is most of property value. Turn "where" into numbers.

Features: distance to nearest metro / IT park / school / hospital / major road; POI counts within 1 km; H3 cell id; locality median ₹/sqft (computed only from *past* data).

Acceptance: all distances in metres from PostGIS; feature fn has the same output in training and API.

## F4 · Satellite & infrastructure features (Should)
**Concept:** satellites see development before listings do.

- Sentinel-2 → NDVI and built-up fraction per H3 cell (yearly composite).
- VIIRS night lights → radiance + 3-year growth (proxy for economic activity).
- Infra projects → distance to nearest *announced / under-construction* metro or road, with status as a categorical.

Precompute into `locality_feature`; join at request time by H3 cell.

## F5 · Comparable engine
**Concept:** what valuers do by hand — find similar nearby sales, adjust, average.

```
w_i = w_dist(d_i) · w_sim(Δarea, Δage, Δbeds) · w_time(age_of_listing) · w_quality
V_comp = A × Σ w_i·p_i / Σ w_i
```
Start with simple kernels: `w_dist = exp(-d/800m)`, `w_time = exp(-months/12)`. **TODO(you):** tune on validation, not on test.

Acceptance: returns top-k with weights; excludes the subject's own dup group; works with 0 comps (returns `None` + reason).

## F6 · Baselines
Three, in `ml/baselines.py`: guideline × area; locality median ₹/sqft × area; linear regression on log ₹/sqft. You cannot claim "better than existing AVMs" without these.

## F7 · CatBoost model
**Concept:** gradient-boosted trees learn non-linear interactions (e.g. metro distance matters more in some localities).

- Target: `log(price / area_builtup_sqft)`.
- `cat_features`: locality, property_type, h3_r7, furnishing.
- `monotone_constraints`: area ↑ (on total value), age ↓, dist_metro ↓ (check sign carefully).
- Time split; early stopping on validation; log everything to MLflow.

Acceptance: beats every baseline on MdAPE and ±10% hit-rate on the test split **and** the geo holdout.

## F8 · Ensemble
Blend comp estimate and model estimate. Learn the weight on the validation split (simple linear stacking or weight by comp count). Never hand-tune on test.

## F9 · Uncertainty (conformal prediction)
**Concept:** a range that is right X% of the time, by construction.

Split-conformal: on the calibration split compute absolute residuals in log space; the q-th quantile (q = level, with finite-sample correction) gives the half-width. Do it **per segment** (comp-count bucket) so thin-data areas get wider ranges. `mapie` can do this; implementing it once by hand (≈15 lines) is worth it.

Acceptance: empirical coverage on test within ±3 pts of target level.

## F10 · Explainability
SHAP values from CatBoost are in log-₹/sqft space. Convert to rupees: apply contributions cumulatively and take differences, so they add up to the final value. Map feature names → plain labels in `engine/labels.py`.

Acceptance: factors sum (with base) to the value within ₹1; top 5 shown.

## F11 · Reliability & routing
Rules in `config/policy.yaml` (thresholds from PRD §7). Add an OOD check (e.g. inputs outside training 1st–99th percentile, or isolation forest). Output grade + reason codes + route.

## F12 · API, persistence, audit
Routes call `engine.value()`; persist valuation, comps, factors, audit row in one transaction. Idempotency key handling.

## F13 · Dashboard
Streamlit pages = screens S1–S5 in `Design.md`. Dashboard **only** talks to the API (never the DB) — so it exercises the same path as the LOS.

## F14 · Review queue & overrides
Reviews table + endpoints + page. Overrides require reason; exported monthly as labelled data.

## F15 · PDF report, batch, webhooks
WeasyPrint HTML→PDF; arq jobs for batch and webhooks.

## F16 · MCP server
See `12-mcp-connection.md`.

---

## Workflow per feature
1. Create issue with acceptance criteria copied from here.
2. Branch `feat/<short-name>`.
3. Write the test that fails.
4. Implement smallest version that passes.
5. PR with: what, why, screenshots/metrics. CI must pass.
6. Update the relevant doc.
