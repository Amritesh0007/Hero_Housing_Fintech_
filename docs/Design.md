# Design — ValueGraph AI

Covers **what the user sees** (UX, screens, report layout) and **key design decisions** (why things are the way they are). System internals live in `architecture.md`.

---

## 1. Design principles

1. **Evidence before number.** Never show a value without the interval, reliability grade and "why". A bare number invites false trust.
2. **Honest uncertainty.** If evidence is insufficient, say so and don't show a value. Silence beats a confident wrong answer.
3. **Underwriter speed.** One screen, one decision. Everything needed to approve or route is above the fold.
4. **Auditable by default.** Every screen links to the exact model version, data snapshot and rules used.
5. **Plain language.** "Metro station 600 m away: +₹3.1 L" — not "feat_dist_metro SHAP=0.042".

## 2. Screens (P1 = Streamlit, P2+ = Next.js)

| # | Screen | Who | Purpose |
|---|---|---|---|
| S1 | **New valuation** | Underwriter | Form + map pin → submit |
| S2 | **Valuation report** | Underwriter, risk | Value, interval, factors, comps, routing |
| S3 | **Review queue** | Valuation team | Cases needing desktop / physical review |
| S4 | **Market view** | All | Locality ₹/sqft heat map, trend (RESIDEX) |
| S5 | **Model status & evaluation** | Risk / governance | Metrics by segment, drift, version history |
| S6 | **Admin** | Admin | Users, API keys, cities, data sources |

### S1 — New valuation
- Address search **or** drop a pin (map). Show resolved locality + guideline rate as soon as the pin lands.
- Fields: property type, area **+ area type** (carpet / built-up / super built-up / plot — required, no default), bedrooms, age, floor / total floors, optional amenities.
- Inline validation (e.g. 3 BHK in 250 sqft → warning, not block).
- Batch tab: CSV upload with a downloadable template.

### S2 — Valuation report (the most important screen)

```
┌─────────────────────────────────────────────────────────────┐
│ VAL-2026-000123 · Gachibowli, Hyderabad · Apartment 2BHK    │
│ Model cb-hyd-res-1.3.0 · Data snapshot 2026-10-20           │
├──────────────────────────┬──────────────────────────────────┤
│ Indicative value         │ Reliability:  ● MEDIUM           │
│ ₹ 71.8 L                 │ Route: Desktop review            │
│ Range (80%) ₹66.0–77.5 L │ LTV basis (low): ₹66.0 L         │
│ ₹ 5,983 / sqft (built-up)│ Guideline value: ₹54.0 L (+33%)  │
├──────────────────────────┴──────────────────────────────────┤
│ Why this value (top factors)                                │
│  ▲ Locality price level             +₹9.4 L                 │
│  ▲ Metro station 600 m              +₹3.1 L                 │
│  ▼ Building age 14 yrs              −₹2.7 L                 │
│  ▼ Ground floor                     −₹1.2 L                 │
│  ▲ Near IT corridor                 +₹1.0 L                 │
├─────────────────────────────────────────────────────────────┤
│ Comparables (6)   [map]  [table: dist, area, ₹/sqft, date]  │
├─────────────────────────────────────────────────────────────┤
│ Flags: ⚠ only 6 comps within 12 months                      │
│ [Download PDF]  [Send to review]  [View audit trail]        │
└─────────────────────────────────────────────────────────────┘
```

- Waterfall chart for factors (base → each factor → final).
- Value basis badge: **"Asking-price proxy model — not transaction-validated"** while in P1.

### S3 — Review queue
Table: case, reliability, reason codes, age of case, assignee. Actions: assign, accept value, override (value + mandatory reason), request site visit. Overrides feed back into training data.

### S5 — Model evaluation
- Metric cards: MdAPE, within ±10 %, within ±20 %, interval coverage — **each with its baseline next to it**.
- Breakdown table by locality, price band, property type, comp-count bucket.
- Residual map (where is the model wrong?).
- Clear label on what the ground truth is.

## 3. Visual language

| Token | Use |
|---|---|
| Green `High` | Auto-eligible |
| Amber `Medium` | Desktop review |
| Red `Low` | Physical inspection |
| Grey `Insufficient` | No value issued |

- Never rely on colour alone — grade text always shown.
- Currency in Indian format (₹71,80,000 / ₹71.8 L / ₹1.2 Cr). One formatter used everywhere.
- Area always shown with its type: "1,200 sqft (built-up)".

## 4. Valuation report PDF
Sections: summary box · property details · value + interval · factors · comparables table + static map · guideline benchmark · reliability & routing · data sources with dates · model version & disclaimer · audit hash.

## 5. Key design decisions (ADR log)

| ID | Decision | Why | Alternative rejected |
|---|---|---|---|
| D1 | CatBoost as main model | Native categoricals, strong on tabular, monotone constraints, fast SHAP | Deep nets (data too small), XGBoost (more categorical prep) |
| D2 | Comparable engine + ML ensemble | Comps give evidence lenders already trust; ML adds non-linear signal | ML only (hard to audit) |
| D3 | Conformal prediction for intervals | Distribution-free, coverage guarantee if data is exchangeable | Fixed ±10 % (dishonest), quantile regression alone (often miscalibrated) |
| D4 | Predict **log(₹/sqft)**, then × area | Stabilizes variance across price bands; area handled explicitly | Predict total price (heteroscedastic) |
| D5 | PostGIS for spatial | Indexed radius search inside the DB | Python haversine loops (slow) |
| D6 | LTV on lower bound | Conservative; aligns risk with uncertainty | LTV on point estimate |
| D7 | Streamlit for P1 UI | Fastest to demo; same Python team | React upfront (time) |
| D8 | Store `price_source` on every price | Never mix asking / guideline / transaction | Implicit source |
| D9 | Behaviour tests instead of monotone constraints (bigger flat → higher value) | Target is log ₹/sqft, so a size constraint on total value can't be expressed directly | Hard constraints on the wrong quantity |

## 6. Accessibility & UX checks
- Keyboard-navigable forms, labelled inputs, contrast ≥ 4.5:1.
- Loading state < 3 s; skeleton on report.
- Empty / error states written for underwriters, not engineers ("We couldn't find enough similar properties nearby — sent for site visit.").
