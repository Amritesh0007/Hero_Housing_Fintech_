# PRD — ValueGraph AI

**Product:** ValueGraph AI — an explainable Automated Valuation Model (AVM) for Indian property
**Challenge:** Hero Housing Fintech Challenge · Problem Statement 1 — *AI-Powered Digital Property Valuation Engine for India*
**Status:** Draft v0.1 · Owner: Ankur Sinha · Last updated: 2026-10-27

---

## 1. Problem

Hero Housing Finance (HHF) relies on physical valuations by empanelled valuers for most loans. That is slow (days per file), costly, inconsistent between valuers, and hard to audit. Existing AVMs in India struggle because:

- Recorded transaction prices are sparse, delayed and often under-declared.
- Listing (asking) prices are biased upward and duplicated across portals.
- Government guideline / circle rates lag the market and vary by state.
- Location quality (access, infrastructure, neighbourhood) is poorly captured.

## 2. Goal

Build an engine that, for a given property, returns a **value, the main factors behind it, and a calibrated confidence** — fast enough and trustworthy enough that HHF can skip or shorten physical valuation for low-risk cases and route the rest correctly.

### Objectives (from the problem statement)
| # | PS1 requirement | How ValueGraph answers it | Doc |
|---|---|---|---|
| 1 | Scope the market | Phase 1: residential apartments, one city (default **Hyderabad**). Phase 2: independent houses/plots + 2 more cities. Phase 3: commercial (office/retail) with a separate model. City rollout gated by data-coverage score. | §5, 08 |
| 2 | Combine data sources | Transactions (lender data when available), guideline rates (state IGR/NGDRS), listings, NHB RESIDEX, OSM amenities, infrastructure projects, Sentinel-2 / night-light satellite features. | database.md, 13 |
| 3 | Build the AI/ML model | Comparable-sales engine + CatBoost (monotone constraints) + calibrated ensemble; benchmarked against 3 baseline AVMs. | architecture.md, 10 |
| 4 | Make it explainable | Per-valuation SHAP factors, comparables used, guideline benchmark, conformal prediction interval, reliability grade, full audit trail. | Design.md, API.md |
| 5 | Automate and scale | Async API + batch endpoint, p95 < 3 s single valuation, stateless services, city added via config + data pipeline. | architecture.md |
| 6 | Enable lending decisions | Reliability → routing policy (auto / desktop review / physical inspection), LTV computed on the *lower* bound, risk flags. | §7 |

### Non-goals (v1)
- Certified valuation reports with legal standing (the engine is decision support).
- Rural / agricultural land.
- Consumer-facing price-discovery app.
- Deep-learning on raw images before tabular foundations are solid.

## 3. Users

| Persona | Need | Primary surface |
|---|---|---|
| **Credit underwriter** | A defensible value + LTV in minutes | Lender dashboard, valuation report |
| **Valuation / technical team** | See which cases need site visits and why | Review queue |
| **Risk & model governance** | Audit any valuation, monitor accuracy & drift | Model status page, audit log |
| **LOS / IT integration** | Call the engine from the loan system | REST API, webhooks, MCP |
| **Admin** | Manage users, API keys, cities, data sources | Admin panel |

## 4. Core user stories

1. As an underwriter, I submit property details (address/pin on map, type, area + area type, bedrooms, age, floor) and get a valuation report within seconds.
2. As an underwriter, I see the **low / base / high** value and the reliability grade, and I understand the top 5 factors in plain language.
3. As an underwriter, I see the comparables used (map + table) and the guideline-rate benchmark.
4. As a valuation manager, cases graded *low* or *insufficient evidence* land in my queue with reasons.
5. As a risk officer, I can open any past valuation and see exactly which data, model version and rules produced it (replayable).
6. As an integrator, I can submit 1,000 properties as a batch and receive results via webhook.
7. As a risk officer, I see accuracy metrics by city, segment and price band, refreshed after each model release.

## 5. Scope by phase

| Phase | Property types | Geography | Ground truth |
|---|---|---|---|
| **P1 — Prototype (competition)** | Residential apartments | Hyderabad (configurable) | Listing-price proxy (disclosed) |
| **P2 — Pilot** | + independent houses, plots | + 2 cities with best data coverage | Lender historical valuations + registered transactions |
| **P3 — Scale** | + commercial office / retail (separate model) | Tier-1 → tier-2 cities by coverage score | Transactions, continuous back-testing |

**Expansion rule:** a city goes live only when it has ≥ N clean comparables per locality (target N=30), guideline-rate coverage ≥ 80% of localities, and back-test hit-rate within targets.

## 6. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| F1 | Accept property input via form, API and CSV batch | Must |
| F2 | Geocode address / pin to lat-lon and locality | Must |
| F3 | Normalize area type (carpet / built-up / super built-up / plot) | Must |
| F4 | Retrieve weighted comparables (distance, similarity, recency, quality) | Must |
| F5 | Predict value with CatBoost + comparable ensemble | Must |
| F6 | Return calibrated low/base/high interval | Must |
| F7 | Return top factors (SHAP) in plain language | Must |
| F8 | Compute reliability grade + routing recommendation | Must |
| F9 | Guideline-rate benchmark and deviation flag | Must |
| F10 | Persist every valuation with inputs, model version, data snapshot | Must |
| F11 | Downloadable PDF report | Should |
| F12 | Review queue with assign / resolve / override + reason | Should |
| F13 | Model evaluation dashboard | Should |
| F14 | Satellite & infrastructure features | Should |
| F15 | MCP server so AI assistants can query valuations | Could |
| F16 | Commercial property model | P3 |

## 7. Lending-decision policy (configurable)

| Reliability | Conditions (indicative) | Action |
|---|---|---|
| **High** | ≥ 8 similar listings in the locality and 80% range no wider than the calibrated High cut-off (cases at or under it had median error ≤ 10% on the calibration split), input not out-of-distribution | Auto-accept for LTV (loan below configured ticket size); physical valuation waived or desktop-only |
| **Medium** | ≥ 4 similar listings and range within the calibrated Medium cut-off (median error ≤ 15%) | Desktop review by technical team |
| **Low** | Fewer comps or wider interval; guideline deviation > 40% caps the grade at Medium | Physical inspection |
| **Insufficient evidence** | No comps / unsupported type / OOD | Physical inspection, no value issued |

- **LTV uses the low bound**, not the base value — conservative by design. A loan above the LTV cap is never routed `auto`.
- Grade cut-offs are learned at training from accuracy targets (`grade_targets`), stored in the model and reported with per-grade accuracy on held-out listings. A lender can override them with `HIGH_MAX_WIDTH` / `MEDIUM_MAX_WIDTH`.
- LTV caps are config, not code. Default to the RBI tiered caps for housing loans and **verify current RBI/NHB directions** before the pilot.
- Every override by a human is logged and becomes training signal.

## 8. Non-functional requirements

| Area | Target |
|---|---|
| Latency | p95 < 3 s single valuation; batch 1,000 in < 10 min |
| Availability | 99.5% (pilot) |
| Explainability | 100% of valuations have factors + interval + comps (or reason why not) |
| Auditability | Any valuation reproducible from stored snapshot + model version |
| Security | RBAC, encrypted at rest/in transit, append-only audit log |
| Privacy | No borrower PII required for valuation; DPDP Act-aligned handling if present |
| Portability | Docker; no hard cloud lock-in |

## 9. Success metrics

**Model (measured on held-out, time-split data):**
- MdAPE, hit-rate within ±10% / ±20%, interval coverage at stated level (e.g. 80% interval covers ~80%).
- Must beat all three baselines: (a) guideline rate × area, (b) locality median ₹/sqft × area, (c) linear hedonic model.

> Until verified transactions are available, report these as **asking-price prediction** metrics. Never present them as fair-market accuracy.

**Business (pilot):** % files with physical valuation waived, turnaround time per file, override rate, post-disbursal valuation variance.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| No true transaction labels | Proxy target clearly labelled; partner with HHF for anonymized history |
| Listing duplicates inflate data | Fuzzy dedup on (geo, area, price, text) |
| Data licence / ToS violations | Source register with licence column; no scraping where ToS forbids |
| Overconfident model | Conformal intervals + reliability gates + OOD check |
| Model drift | Monthly back-test, drift alerts, champion/challenger |
| Regulatory misfit | Position as decision support; keep human sign-off where regulation requires |

## 11. Open questions
- Which city does HHF most want first? (Default Hyderabad.)
- Can HHF share anonymized past valuation reports for validation?
- Ticket-size threshold below which auto-accept is allowed?
- Required output format for their LOS (JSON schema / PDF)?
