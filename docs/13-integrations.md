# 13 · Integrations

Two families: **data in** (sources) and **decisions out** (lender systems). Every integration is an adapter module with the same shape, so adding one doesn't touch the engine.

```python
class SourceAdapter(Protocol):
    name: str

    def fetch(self, since: date | None) -> Path: ...  # download / read to data/raw/<name>/<date>/
    def parse(self, path: Path) -> pd.DataFrame: ...
    def validate(self, df: pd.DataFrame) -> pd.DataFrame: ...  # pandera
    def load(self, df: pd.DataFrame, session) -> LoadReport: ...
```

## 1. Data sources

| Source | Gives | Access | Refresh | Licence / notes |
|---|---|---|---|---|
| **"India House Price" listings (`house_prices.csv`)** | 187k scraped listings, 81 cities — P1 training base (≈58k after dedup) | CSV in `data/raw/` | Static | Asking prices; no coordinates (location = city + locality from title); check licence |
| **Synthetic Kaggle urban dataset** | Pipeline/API testing | Download | Static | **Never** for accuracy claims |
| **State IGR / NGDRS guideline rates** | Official ₹/unit by locality | State portal lookup; often manual or per-locality | On notification (usually yearly) | Government data — cite; store `effective_from` |
| **NHB RESIDEX** | City price indices | Download from NHB portal | Quarterly | Market trend feature + time adjustment |
| **OpenStreetMap Nominatim (one-off)** | Locality centroids for ~2,000 localities → `data/geo/locality_coords.csv` | Geocoded once at ≤1 request/s, cleaned by `valuegraph geocode-import` (drops city-level fallbacks, shared points, >25 km outliers) | On new localities | ODbL — "© OpenStreetMap contributors" shown on maps |
| **OpenStreetMap** | POIs, roads, metro | Geofabrik India extract (`.osm.pbf`) → `osmium`/`pyrosm`; avoid hammering public Overpass | Monthly | ODbL — attribute "© OpenStreetMap contributors" |
| **Sentinel-2** | NDVI, built-up | Copernicus Data Space or Google Earth Engine | Yearly composite | Free/open; GEE terms for non-commercial |
| **VIIRS night lights** | Economic activity proxy | Earth Observation Group downloads / GEE | Yearly | Cite source |
| **Infrastructure projects** | Metro lines, ORR, airport links | Manually curated from official announcements + OSM `construction=*` | When announced | Store source URL per row |
| **Lender history (pilot)** | Past valuations, sanctioned values | Secure file drop from HHF | One-off then monthly | Confidential — separate DB schema, restricted role |
| **Registered transactions (pilot)** | True labels | Via lender / licensed provider | Monthly | Biggest accuracy unlock |

### Matching sources together
Join on **geometry first** (point-in-polygon → locality; H3 cell), not on locality names. Keep a locality alias table for name variants ("Gachibowli" / "Gachi Bowli"). Unmatched rows go to a review CSV, not silently dropped.

### Guideline rates — practical approach
Portals often expose rates via search forms, not bulk files. For P1: capture rates for the localities in your dataset (manually or with a permitted, polite script), store the raw row in `guideline_rate.raw`, and document the method. Convert per-sqyd/sqm to per-sqft at load time.

## 2. Lender-side integrations

| Integration | How |
|---|---|
| **Loan Origination System (LOS)** | REST `POST /valuations` with `external_ref`; result via sync response or `valuation.completed` webhook |
| **Batch revaluation** | CSV upload / SFTP drop → batch job → results CSV + webhook |
| **Review outcomes** | `review.resolved` webhook back to LOS |
| **SSO (pilot)** | OIDC with lender IdP |
| **Notifications** | Email for review assignment (SMTP via env config) |
| **AI assistants** | MCP server (doc 12) |

## 3. Supporting services

| Service | Choice | Note |
|---|---|---|
| Maps (dashboard) | Leaflet via `folium` | Use a tile provider whose usage policy fits; OSM's public tiles aren't for heavy use |
| Geocoding | Nominatim (self-host or low volume) → fallback to pin | Respect usage policy; cache results |
| PDF | WeasyPrint | HTML template → PDF |
| Model registry | MLflow | Self-hosted in compose |
| Monitoring | Prometheus + Grafana (pilot) | `/metrics` endpoint |

## 4. Adding a new source — checklist
- [ ] Licence allows our use → row in `data_source` with `allowed_use`.
- [ ] Adapter module + pandera schema + unit test with a 20-row fixture.
- [ ] Raw files saved immutably with capture date.
- [ ] Load report (rows in / rejected / reasons).
- [ ] Feature impact measured (ablation: eval with vs without).
- [ ] This doc updated.
