# 14 · Testing and QA

AVMs fail in two ways: **software bugs** (normal tests catch these) and **silently wrong numbers** (only data and model tests catch these). We test both.

## 1. Test pyramid

| Layer | Folder | Tools | Runs |
|---|---|---|---|
| Unit | `tests/unit` | pytest | Every commit (< 30 s) |
| Data validation | `tests/ml/test_data_*.py` | pandera | Every ingestion + CI on fixtures |
| ML behaviour | `tests/ml` | pytest + saved model | CI on model change |
| Integration (DB) | `tests/integration` | testcontainers PostGIS | CI |
| API contract | `tests/api` | httpx `TestClient`, schemathesis (optional) | CI |
| End-to-end | `tests/e2e` | Playwright on dashboard (P2) | Nightly |
| Load | `tests/load` | Locust | Before demo / release |
| Security | CI | bandit, pip-audit, auth matrix tests | CI |

## 2. Unit tests — what matters most
- `parse_price_inr`, area conversion, currency formatting (₹ lakh/crore).
- Comp weights: closer/newer/more-similar comp gets higher weight.
- Conformal quantile on a tiny known array.
- Reliability rules: table-driven test — each row = inputs → expected grade + reason codes.
- SHAP-to-rupee conversion sums to the value.

## 3. Data tests (pandera)
- Ranges: area 100–20,000 sqft; ₹/sqft within city bounds; lat/lon in city bbox.
- Categories: `area_type`, `property_type`, `price_source` in allowed sets.
- Uniqueness: one row per (`source_id`, `source_ref`).
- Freshness: newest listing ≤ N days old (warn).
- **Leakage guard:** for each training row, all comps used have `listed_on` < row's date and different `dup_group_id`.

## 4. ML behaviour tests
| Test | Idea |
|---|---|
| **Performance floor** | Test-split MdAPE ≤ stored threshold and better than every baseline |
| **No regression** | New model not worse than champion by > 1 pt on any major segment |
| **Calibration** | Interval coverage within ±3 pts of target |
| **Monotonicity** | Same property, +10% area → value ↑; +5 yrs age → value ↓ |
| **Invariance** | Changing `external_ref` or amenity order doesn't change value |
| **Directional** | Moving the pin next to a metro station shouldn't decrease value (soft check) |
| **Determinism** | Same input + same model version → identical output (replay) |
| **OOD handling** | 50,000 sqft apartment → `insufficient_evidence`, not a number |
| **Train/serve parity** | Features computed via training pipeline == via API for the same row |

## 5. Integration tests
- Spin up PostGIS with testcontainers, run migrations, load fixture localities + 200 listings.
- Comp query returns expected ids for a known point; coordinates order correct.
- Valuation persists valuation + comps + factors + audit row atomically (kill mid-way → nothing written).

## 6. API tests
- Every route: happy path, 401, 403 per role, 422 validation.
- Idempotency: same key + body → same id; same key + different body → 409.
- Rate limit returns 429.
- Response matches the schema in `API.md` (snapshot test on a fixed model).

## 7. Load & performance
Locust scenario: 80% `POST /valuations`, 20% GET. Target p95 < 3 s at 20 RPS on demo hardware. Profile the slowest step (usually comps query or SHAP) before optimizing.

## 8. QA before demo / release
- [ ] Fresh clone → setup doc works end to end on a clean machine.
- [ ] 20 hand-picked properties valued; a teammate sanity-checks each against listings.
- [ ] Edge cases: missing age, unknown locality, ground floor, plot, very new building.
- [ ] Report PDF renders; numbers match the API.
- [ ] Evaluation page shows baseline comparison and ground-truth label.
- [ ] No "accuracy" wording that implies transaction validation.

## 9. Coverage & gates
- Engine ≥ 80% line coverage; API ≥ 70%.
- PR cannot merge if: tests fail, coverage drops > 2 pts, or a model change lacks an updated eval report.
