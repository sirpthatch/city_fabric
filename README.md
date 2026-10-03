# City Fabric

A research platform for urban data on New York City. It collects public datasets (mainly NYC OpenData),
attributes them to common geographies, and gives you an interactive map and notebooks for exploring how
features relate to each other.

```
 Collection            Geographic pivot                 Visualization       Analysis
 ───────────           ─────────────────                ─────────────       ────────
 Socrata API  ──►  raw parquet ──► stg_<ds> ──► asg_<ds> ──► features ──► data/marts/ ──► FastAPI + MapLibre
 (freshness-          (typed)    (point→geo)   (SQL aggs)  (parquet,         └──► notebooks
  checked)                                                  GeoJSON, manifest)
```

| Part | Where | What it does |
|---|---|---|
| **Collection** | `config/datasets/*.yaml`, `src/city_fabric/collection/` | Declarative dataset specs. Pulls Socrata data as raw Parquet snapshots. Skips a dataset when its source `rowsUpdatedAt` hasn't changed. Supports `full` and `incremental` (watermark + overlap) refresh. |
| **Geographic pivot** | `config/geographies.yaml`, `src/city_fabric/geo/`, `src/city_fabric/features/` | Loads boundary layers into DuckDB (spatial) and spatially joins every point to each level. Aggregates features declared as SQL in the dataset spec, with optional `/km²` and share normalizations. |
| **Visualization** | `src/city_fabric/viz/` | `cf serve`: a choropleth of any feature at any level, a distribution summary, an X–Y scatter with Pearson/Spearman/OLS, top correlates, and a ranked table. The URL hash holds the full view state, so a link reproduces it. |
| **Analysis** | `notebooks/`, `src/city_fabric/analysis/` | Feature profiling, clustered correlation matrices, strongest pairs, robust OLS, residual maps, and cross-level (MAUP) robustness checks. |

## Geographic levels

| Level | ID | Source |
|---|---|---|
| `borough` | borocode | [gthc-hcne](https://data.cityofnewyork.us/d/gthc-hcne) |
| `community_district` | boro_cd (59 districts; parks/airport JIAs excluded) | [5crt-au7u](https://data.cityofnewyork.us/d/5crt-au7u) |
| `nta` | 2020 NTA code (residential NTAs only) | [9nt8-h7nd](https://data.cityofnewyork.us/d/9nt8-h7nd) |
| `census_tract` | 11-digit FIPS GEOID | [63ge-mke6](https://data.cityofnewyork.us/d/63ge-mke6) |
| `zcta` | Modified ZCTA | [pri4-ifjk](https://data.cityofnewyork.us/d/pri4-ifjk) |

## Datasets

| Dataset | Source | Refresh | Features |
|---|---|---|---|
| `acs_tracts` | Census ACS 5-year 2020–2024, tables B01003, B19001, B08303, B08013, B08301, B25070 | full, per vintage | population (+ density), median household income, income tails, mean commute, 60+ min commutes, commute mode shares, work from home, rent burden |
| `forestry_trees` | Forestry Tree Points [hn5i-inap](https://data.cityofnewyork.us/d/hn5i-inap) | full | live trees, planted in last 5y, stumps, mean DBH, % poor, species richness |
| `service_requests_311` | 311 Service Requests [erm2-nwe9](https://data.cityofnewyork.us/d/erm2-nwe9) | incremental on `created_date` (90-day backfill, 7-day overlap) | total, median days to close, % open, top 30 complaint types |
| `restaurant_inspections` | DOHMH Restaurant Inspections [43nn-pn8j](https://data.cityofnewyork.us/d/43nn-pn8j) | full | restaurants, % graded A, mean latest score, critical violations per restaurant, cuisine richness, top 15 cuisine shares |
| `public_restrooms` | Public Restrooms [i7jb-7jku](https://data.cityofnewyork.us/d/i7jb-7jku) | full | operational restrooms, library restrooms, % with changing station |
| `wifi_hotspots` | NYC Wi-Fi Hotspot Locations [yjub-udmw](https://data.cityofnewyork.us/d/yjub-udmw) (LinkNYC excluded) | full | free hotspots, all hotspots |
| `linknyc_kiosks` | LinkNYC Kiosk Locations [s4kf-3yrf](https://data.cityofnewyork.us/d/s4kf-3yrf) | full | live kiosks, Link5G towers |

Count features can be normalized by `area` (`_per_km2`), `capita` (`_per_1k`, per 1,000 ACS residents)
and `share` (within-geography share for `group_by` features). Per-capita rates are left NULL for
geographies with fewer than 500 residents (parks, airports, industrial areas), where rates would be noise.

**Density vs. per-capita:** per-km² features largely measure population density, because denser places
have more of everything. Use `_per_1k` rates, or control for `acs_population_per_km2`, before you read
meaning into a correlation. `02_correlation_regression.ipynb` shows the effect.

### ACS methodology

- **Source:** Census table-based summary files (keyless bulk download, filtered to NYC tracts), not the
  Census API, which now requires a key. To move to a new vintage, change `vintage` in `acs_tracts.yaml`.
- **Suppressed estimates:** negative annotation codes such as `-666666666` are stored as NULL. Ratio
  features restrict their denominators to tracts where the numerator is reported.
- **Rolling up tracts:** each tract is assigned to coarser levels by its interior point (`ST_PointOnSurface`).
  2020 NTAs and boroughs nest exactly, community districts nearly, and MODZCTAs approximately. Population
  totals are 8.48M at every level except NTA (−0.1%, residents of non-residential NTAs).
- **Medians:** these are interpolated from the 16 income brackets with `binned_median()`, so they are
  consistent across levels. Borough results come within 1% of the official B19013 values. The top bracket
  is open-ended ($200k+), so the median is capped at $200,000 (70 of 2,231 tracts).

## Quickstart

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[analysis,dev]"

cf run            # boundaries → collect → build   (first run: ~12 min, mostly the API pull)
cf serve          # http://127.0.0.1:8000
jupyter lab notebooks/
```

Individual steps:

```bash
cf boundaries [--force]         # download + load geographies
cf collect [DATASET...] [--force]   # pull datasets whose source changed
cf status                       # local state vs. source freshness
cf build [DATASET...]           # stage → assign geographies → features → publish marts
```

To keep monitored datasets current, schedule `cf collect && cf build` (cron or launchd). Freshness
checks make a run cheap when nothing upstream has changed. Set `SOCRATA_APP_TOKEN` to get higher API
rate limits.

## Adding a dataset

Create `config/datasets/<name>.yaml`:

```yaml
name: public_restrooms
title: Public Restrooms
source: {type: socrata, domain: data.cityofnewyork.us, id: xxxx-xxxx,
         select: [facility_id, status, latitude, longitude]}
refresh: {strategy: full, min_interval_hours: 168}
key: facility_id
staging: |                       # {raw} = deduplicated raw rows (all strings)
  SELECT facility_id AS key, status,
         ST_Point(CAST(longitude AS DOUBLE), CAST(latitude AS DOUBLE)) AS geom
  FROM {raw} WHERE latitude IS NOT NULL
features:
  - id: restrooms_open
    title: Open public restrooms
    agg: count(*)
    filter: status = 'Open'
    fill: 0                      # geographies with no points get 0, not NULL
    unit: restrooms
    normalize: [area]            # also emit restrooms_open_per_km2
  - id: restrooms_by_type        # group_by expands to one feature per top-N value
    title: "Restrooms: {value}"
    group_by: status
    top_n: 5
    agg: count(*)
    normalize: [share]
```

Then run `cf collect public_restrooms && cf build`. Staging SQL can use any DuckDB function. A feature's
`agg` and `filter` refer to the staged columns.

## Data layout

```
data/                         (gitignored)
  raw/<dataset>/*.parquet     raw snapshots (strings + _ingested_at)
  boundaries/<level>.geojson  source boundary layers
  warehouse.duckdb            geo_boundaries, stg_*, asg_*, features_long, feature_catalog
  marts/                      features_<level>.parquet, geo_<level>.geojson, manifest.json
  collection_state.json       last run, source timestamp, watermark per dataset
```

## Roadmap

- Time-sliced features (monthly 311 volumes, planting by year) and change-over-time views.
- Areal-weighted crosswalks for ACS → ZCTA and community districts, instead of interior-point assignment.
- ACS margins of error, so features can carry reliability flags.
- Feature derivations across datasets (e.g. combined Wi-Fi access points = hotspots + LinkNYC).
- Points layer and bivariate choropleth in the map.
