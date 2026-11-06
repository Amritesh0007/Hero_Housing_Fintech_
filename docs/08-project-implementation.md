# 08 · Project Implementation Plan

## 1. Mapping to what the judges score

Judging: **accuracy & feasibility · use of data · explainability · regulatory fit · scalability · clarity of presentation.**

| PS1 point | Deliverable that proves it | Phase |
|---|---|---|
| 1 Scope the market | Scope table + city-readiness score (PRD §5) | M0 |
| 2 Combine data | Source register, ingestion pipelines, merged feature table | M1–M2 |
| 3 AI/ML model | Baselines vs CatBoost vs ensemble on time + geo holdout | M3 |
| 4 Explainable | Report with factors, comps, interval, audit trail | M4 |
| 5 Automate & scale | API + batch + Docker + latency numbers | M5 |
| 6 Lending decisions | Routing policy + LTV-on-low-bound + "% physical valuations avoided" simulation | M5 |

## 2. Milestones (8 weeks, 1–4 people)

| M | Weeks | Goal | Exit criteria |
|---|---|---|---|
| **M0 Foundations** | 0.5 | Repo, Docker, DB, CI, docs | `make up` works; CI green on empty tests |
| **M1 Data v1** | 1–2 | Listings + guideline rates + localities for 1 city | ≥ 5k clean deduped listings; rates matched for ≥ 80% localities; data report |
| **M2 Geo & alt data** | 2–3 | OSM POIs, infra projects, RESIDEX, satellite features | Feature table with all groups; missingness report |
| **M3 Model** | 3–4 | Baselines, comps engine, CatBoost, ensemble, conformal | Eval report; beats all baselines on test; coverage within ±3 pts of target |
| **M4 Explainability** | 4–5 | SHAP → rupee factors, reliability grades, PDF | Every valuation has factors + reason codes |
| **M5 Product** | 5–6 | API, auth, dashboard, review queue, batch | End-to-end demo; p95 < 3 s |
| **M6 Hardening** | 6–7 | Tests, security, monitoring, MCP | Coverage ≥ 70% on engine; security checklist done |
| **M7 Submission** | 7–8 | Deck, demo video, architecture diagram, eval report | Every PS1 point covered with evidence |

## 3. Team split (if 4 people)

| Role | Owns |
|---|---|
| Data engineer | `ingest/`, DB, data quality |
| ML engineer | `ml/`, `engine/` model + uncertainty + explain |
| Backend | `api/`, auth, workers, MCP |
| Frontend + presentation | `dashboard/`, report, deck, demo |

Solo? Follow milestones in order; skip satellite (M2) until M3 baseline works.

## 4. The "accuracy" story (be careful here)

1. **What we can measure now:** error on held-out *asking prices* (time-split + unseen localities).
2. **How we beat existing AVMs:** compare against three baselines anyone could build (guideline × area, locality median, hedonic linear). Show the lift, by segment.
3. **What we need for the real claim:** lender's anonymized historical valuations / registered transactions. Present the **validation protocol** we'd run on it (same metrics, same splits) — judges reward honesty and feasibility.
4. **Simulate lending impact:** on the test set, % of cases graded High (→ physical valuation avoidable) and error within that group.

## 5. Weekly rhythm
- Mon: plan the week's issues (GitHub Projects).
- Daily: small PRs, CI must pass.
- Fri: regenerate eval report; record what changed and why in `docs/changelog.md`.

## 6. Risks to the schedule
| Risk | Fallback |
|---|---|
| Guideline rates not downloadable | Manually capture for top 50 localities; document method |
| Too few geo-tagged listings | Geocode locality names to centroids, flag `GEO_IMPRECISE` |
| Satellite pipeline slow | Precompute per H3 cell once; ship without if late (Should, not Must) |
| Time | Cut React, PDF styling, commercial — never cut evaluation or explainability |
