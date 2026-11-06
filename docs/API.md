# API — ValueGraph AI

Base URL `/api/v1` · JSON · interactive docs at `/docs` (off when `APP_ENV=prod`). Errors are RFC 7807 `application/problem+json`.

## Conventions

| Topic | Rule |
|---|---|
| Auth | `Authorization: Bearer <JWT>` (people) or `X-API-Key: vg_<prefix>_<secret>` (systems; key inherits its owner's role) |
| Roles | `underwriter`, `reviewer`, `risk`, `admin` (admin passes every role check) |
| Idempotency | Optional `Idempotency-Key` on `POST /valuations`; same key + same body → same valuation, different body → 409 |
| Money | Integer rupees |
| Area | `area_sqft` + required `area_type` (`carpet` / `built_up` / `super_built_up`) |
| Limits | Body ≤ 64 KB; `RATE_LIMIT_PER_MIN` per principal (default 120); login: 5 failed attempts / 15 min per account → 429 |
| Unknown fields | Rejected (422) |

## Endpoints (v0.1 — all implemented)

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/` | anyone | Public website (`src/valuegraph/site/`) |
| GET | `/app/` | anyone | Signed-in lender app (static files; its data goes through the authenticated API) |
| GET | `/health`, `/ready` | anyone | Liveness; readiness reports whether a model is loaded |
| GET | `/api/v1/config` | anyone | `{"maps_api_key","google_client_id"}`: public browser identifiers only |
| GET | `/api/v1/public/cities` | anyone | Covered cities. Throttled: 120/min per client IP (shared by all `public` reads) |
| GET | `/api/v1/public/markets/{city}/localities` | anyone | Same as the signed-in version |
| GET | `/api/v1/public/markets/{city}/summary` | anyone | Same as the signed-in version |
| GET | `/api/v1/public/i18n` | anyone | Website languages: `[{"code","name"}]` (name in its own script) |
| GET | `/api/v1/public/i18n/{lang}` | anyone | Website strings for `lang`. `en` is `site/i18n/en.json`; others are translated from it by Gemini on first request, checked (same keys and `{placeholders}`, ≥80% usable) and cached in `I18N_CACHE_DIR`. 404 unknown language · 503 no `GEMINI_API_KEY` and not cached · 502 Gemini failed · Gemini calls capped at `GEMINI_MAX_CALLS_PER_HOUR` |
| GET | `/api/v1/public/model` | anyone | Headline test metrics only (median error, within ±10%, interval coverage) |
| POST | `/api/v1/public/estimate` | anyone | Same body and model as `POST /valuations`, but **nothing is stored or audited**; `loan_amount_inr`, `guideline_rate_sqft`, `external_ref` are ignored and `route`, `lending`, `guideline` are not returned. Throttled: 20/min per client IP |
| POST | `/api/v1/auth/token` | anyone | `{"email","password"}` → `{"access_token","role"}` |
| POST | `/api/v1/auth/google` | anyone | `{"credential"}` (Google Sign-In ID token) → `{"access_token","role","email"}`. Verifies Google's signature, audience = `GOOGLE_CLIENT_ID`, issuer, expiry and `email_verified`. First sign-in creates the account with `GOOGLE_DEFAULT_ROLE` (default underwriter). 404 when `GOOGLE_CLIENT_ID` is unset. Throttled: 10/min per client IP |
| GET | `/api/v1/me` | any user | Who am I |
| POST | `/api/v1/valuations` | underwriter | Value one property |
| GET | `/api/v1/valuations` | any user | Recent valuations (underwriters see their own) |
| GET | `/api/v1/valuations/{id}` | owner, reviewer, risk | Full result + review status (404 if not yours) |
| GET | `/api/v1/valuations/{id}/audit` | risk | Audit events + `chain_valid` |
| GET | `/api/v1/markets/{city}/localities` | any user | Localities with listing counts (for the locality picker) |
| GET | `/api/v1/markets/{city}/summary` | any user | Median asking ₹/sqft and every locality with 5+ listings (median, count, lat/lon), priciest first |
| GET | `/api/v1/models/status` | any user | Model version, cities, full evaluation report |
| GET | `/api/v1/reviews?status=open` | reviewer, risk | Review queue |
| PATCH | `/api/v1/reviews/{id}` | reviewer | `accept` / `override` (value + reason required) / `site_visit` |

Planned (not built): batch CSV, webhooks, PDF report, refresh tokens.

## POST /api/v1/valuations

```json
{
  "city": "Hyderabad",
  "locality": "Gachibowli",
  "society": "Aparna Sarovar",
  "area_sqft": 1100, "area_type": "built_up",
  "bhk": 2,
  "bathrooms": 2, "balconies": 1,
  "floor": 7, "total_floors": 14,
  "transaction": "Resale",
  "furnishing": "Semi-Furnished",
  "parking": 1, "parking_type": "Covered",
  "facing": "East",
  "ownership": "Freehold",
  "overlooking": ["Garden/Park"],
  "guideline_rate_sqft": 5000,
  "loan_amount_inr": 6000000,
  "external_ref": "LOS-APP-88812"
}
```
Required: `city, locality, area_sqft, area_type, bhk`. Everything else is optional and treated as unknown when absent — more detail gives a sharper estimate. `locality` must be one the model has listings for (see `/markets/{city}/localities`). `society` (building / project name) is optional; when other listings from the same building are on file they become the strongest evidence (matching ignores case and punctuation). `floor` 0 = ground, −1/−2 = basements. `guideline_rate_sqft` is on the same area basis as `area_sqft`.

**201 response** (abridged)
```json
{
  "valuation_id": "VAL-9D4250CFC24A",
  "area": {"value": 1100, "type": "built_up", "listing_basis_sqft": 1320},
  "value": 7510000, "low": 6480000, "high": 8890000, "interval_level": 0.8,
  "rate_per_sqft": 6827, "currency": "INR",
  "base_value": 7020000,
  "factors": [{"label": "Location", "contribution_inr": 610000}, {"label": "Size", "contribution_inr": -120000}],
  "comp_count": 14,
  "comparables": [{"listing_id": 1834, "locality": "Gachibowli", "society": null, "sqft": 1299, "bhk": 2,
                   "floor": 9, "furnishing": "Unfurnished", "price_inr": 13500000, "rate_per_sqft": 10395,
                   "weight": 0.95, "price_source": "asking"}],
  "guideline": {"rate_per_sqft": 5000, "value": 5500000, "deviation_pct": 36.5},
  "reliability": "medium", "route": "desktop_review", "reason_codes": [],
  "location": {"lat": 17.4436, "lon": 78.3520, "precision": "locality centroid (OpenStreetMap)"},
  "neighbourhood": [{"locality": "Kondapur", "lat": 17.47, "lon": 78.36, "distance_km": 2.1,
                     "median_rate_per_sqft": 7400, "listings": 149}],
  "lending": {"ltv_basis": 6480000, "ltv_pct": 61.7, "ltv_cap_pct": 80.0, "within_policy": true},
  "basis": "asking_price_proxy", "model_version": "cb-20261027-kaggle",
  "created_at": "2026-10-27T18:05:11+00:00"
}
```
`location` is `null` (and `neighbourhood` empty) for localities that couldn't be geocoded. `reliability` cut-offs are calibrated per model; see `evaluation.grade_widths` and `evaluation.results.test.by_grade` in `/models/status`.

`base_value` + Σ `factors` = `value`. When `reliability` is `insufficient_evidence`, `value/low/high` are `null` and `route` is `physical_inspection`.

## Reason codes

| Code | Meaning |
|---|---|
| `UNSUPPORTED_CITY` | City not in training data |
| `UNKNOWN_LOCALITY` | No listings on file for that locality in that city |
| `OOD_INPUT` | Area or BHK outside the training range (1st–99th percentile area) |
| `NO_COMPS` / `FEW_COMPS` | 0 / fewer than `MEDIUM_MIN_COMPS` similar listings (size/BHK similarity ≥ 25%) in the locality |
| `WIDE_INTERVAL` | Interval width above `MEDIUM_MAX_WIDTH` |
| `GUIDELINE_DEVIATION` | Value differs from guideline value by more than 40% |
| `LTV_ABOVE_CAP` | Loan / low bound above the tier cap → never routed `auto` |

## Errors

```json
{"type": "about:blank", "title": "Error", "status": 409, "detail": "Idempotency-Key reused with a different request",
 "instance": "/api/v1/valuations"}
```
400 bad Content-Length · 401 credentials · 403 role · 404 not found / not yours · 409 idempotency or review already decided · 413 too large · 422 validation · 429 throttled · 503 no model loaded.
