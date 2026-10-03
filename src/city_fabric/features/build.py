"""Turn feature specs into per-geography values.

Each FeatureSpec is an aggregate over a staged dataset joined to its geography
assignments. Results land in:

  features_long(level, geo_id, feature, value)
  feature_catalog(feature, title, dataset, unit, kind, base_feature, description)

and are published to data/marts/ for the visualization and notebooks.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import duckdb
import pandas as pd

from city_fabric import paths
from city_fabric.config import DatasetSpec, FeatureSpec

log = logging.getLogger(__name__)

# Denominator for the "capita" normalization (per 1,000 residents).
POPULATION_FEATURE = "acs_population"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _levels(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT level FROM geo_levels ORDER BY ordinal").fetchall()]


def _long_assignments(spec: DatasetSpec, levels: list[str]) -> str:
    """Relation of (key, level, geo_id) rows: each point once per level it falls in."""
    return (f"(UNPIVOT asg_{spec.name} ON {', '.join(levels)} "
            f"INTO NAME level VALUE geo_id)")


def _aggregate(con, spec: DatasetSpec, feat: FeatureSpec, levels: list[str],
               group_values: list[str] | None = None) -> pd.DataFrame:
    """Aggregate a feature to every geography, filling geos with no points."""
    where = [f"({feat.filter})"] if feat.filter else []
    group_sel = group_on = ""
    if group_values is not None:
        quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in group_values)
        where.append(f"{feat.group_by} IN ({quoted})")
        group_sel, group_on = f", {feat.group_by} AS grp", ", grp"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    fill = "NULL" if feat.fill is None else feat.fill

    # Cross geographies with group values so empty combinations get the fill value.
    grid = "geo_boundaries g"
    if group_values is not None:
        grid += f" CROSS JOIN (SELECT unnest([{quoted}]) AS grp) gv"
    return con.execute(f"""
        WITH agg AS (
          SELECT a.level, a.geo_id{group_sel}, {feat.agg} AS value
          FROM stg_{spec.name} s
          JOIN {_long_assignments(spec, levels)} a USING (key)
          {where_sql}
          GROUP BY ALL
        )
        SELECT g.level, g.geo_id, g.area_km2{', gv.grp' if group_values is not None else ''},
               COALESCE(agg.value, {fill})::DOUBLE AS value
        FROM {grid}
        LEFT JOIN agg ON agg.level = g.level AND agg.geo_id = g.geo_id
                     {'AND agg.grp = gv.grp' if group_values is not None else ''}
    """).df()


def _top_values(con, spec: DatasetSpec, feat: FeatureSpec) -> list[str]:
    where = f"WHERE {feat.filter}" if feat.filter else ""
    rows = con.execute(f"""
        SELECT {feat.group_by} FROM stg_{spec.name} {where}
        GROUP BY 1 HAVING {feat.group_by} IS NOT NULL
        ORDER BY count(*) DESC LIMIT {feat.top_n}
    """).fetchall()
    return [r[0] for r in rows]


def _emit(frames, catalog, df, fid, title, spec, feat, kind="raw", base=None, unit=None):
    frames.append(df[["level", "geo_id"]].assign(feature=fid, value=df["value"]))
    catalog.append({
        "feature": fid, "title": title, "dataset": spec.name, "unit": unit or feat.unit,
        "kind": kind, "base_feature": base or fid, "description": feat.description,
    })


def _emit_with_normalizations(frames, catalog, df, fid, title, spec, feat, totals=None,
                              per_capita=None):
    _emit(frames, catalog, df, fid, title, spec, feat)
    for norm in feat.normalize:
        if norm == "capita" and per_capita is not None:
            # Deferred until every dataset is built, since population comes from ACS.
            per_capita.append((df, fid, title, spec, feat))
        if norm == "area":
            out = df.assign(value=df["value"] / df["area_km2"])
            _emit(frames, catalog, out, f"{fid}_per_km2", f"{title} per km²", spec, feat,
                  kind="per_km2", base=fid, unit=f"{feat.unit or 'count'}/km²")
        elif norm == "share" and totals is not None:
            merged = df.merge(totals, on=["level", "geo_id"], suffixes=("", "_total"))
            out = merged.assign(value=merged["value"] / merged["value_total"].where(
                merged["value_total"] > 0))
            _emit(frames, catalog, out, f"{fid}_share", f"{title} (share)", spec, feat,
                  kind="share", base=fid, unit="fraction")


def _emit_per_capita(frames, catalog, pending, min_population: float = 500) -> None:
    """Per-1,000-resident rates. Geographies under `min_population` get NULL, since
    rates over near-empty areas (parks, airports, industrial zones) are noise."""
    if not pending:
        return
    pop = next((f for f in frames if (f["feature"] == POPULATION_FEATURE).any()), None)
    if pop is None:
        log.warning("Skipping per-capita features: %s not built (collect acs_tracts)",
                    POPULATION_FEATURE)
        return
    pop = pop[["level", "geo_id", "value"]].rename(columns={"value": "population"})
    for df, fid, title, spec, feat in pending:
        m = df.merge(pop, on=["level", "geo_id"], how="left")
        rate = 1000 * m["value"] / m["population"].where(m["population"] >= min_population)
        _emit(frames, catalog, m.assign(value=rate), f"{fid}_per_1k", f"{title} per 1k residents",
              spec, feat, kind="per_capita", base=fid, unit=f"{feat.unit or 'count'}/1k residents")


def build_features(con: duckdb.DuckDBPyConnection, specs: list[DatasetSpec]) -> int:
    levels = _levels(con)
    frames: list[pd.DataFrame] = []
    catalog: list[dict] = []
    per_capita: list[tuple] = []
    for spec in specs:
        for feat in spec.features:
            if feat.group_by is None:
                df = _aggregate(con, spec, feat, levels)
                _emit_with_normalizations(frames, catalog, df, feat.id, feat.title, spec, feat,
                                          per_capita=per_capita)
                continue
            values = _top_values(con, spec, feat)
            totals = _aggregate(con, spec, feat, levels)[["level", "geo_id", "value"]]
            grouped = _aggregate(con, spec, feat, levels, group_values=values)
            for value, df in grouped.groupby("grp", sort=False):
                fid = f"{feat.id}__{slugify(value)}"
                title = feat.title.format(value=value)
                _emit_with_normalizations(frames, catalog, df, fid, title, spec, feat, totals,
                                          per_capita=per_capita)
        log.info("%s: features built", spec.name)

    _emit_per_capita(frames, catalog, per_capita)
    long = pd.concat(frames, ignore_index=True)
    cat = pd.DataFrame(catalog)
    con.register("_long", long)
    con.register("_cat", cat)
    con.execute("CREATE OR REPLACE TABLE features_long AS SELECT * FROM _long")
    con.execute("CREATE OR REPLACE TABLE feature_catalog AS SELECT * FROM _cat")
    con.unregister("_long")
    con.unregister("_cat")
    log.info("Built %d features across %d levels", len(cat), len(levels))
    return len(cat)


def publish_marts(con: duckdb.DuckDBPyConnection, simplify_tolerance: float = 0.0001) -> None:
    """Write wide feature tables, simplified GeoJSON and the catalog to data/marts/."""
    paths.MARTS_DIR.mkdir(parents=True, exist_ok=True)
    levels = con.execute("SELECT level, title FROM geo_levels ORDER BY ordinal").fetchall()
    features = [r[0] for r in con.execute(
        "SELECT feature FROM feature_catalog ORDER BY dataset, feature").fetchall()]
    for level, _ in levels:
        con.execute(f"""
            COPY (
              SELECT g.geo_id, g.name, g.borough, g.area_km2, f.* EXCLUDE (geo_id)
              FROM (SELECT level, geo_id, name, borough, area_km2 FROM geo_boundaries
                    WHERE level = '{level}') g
              LEFT JOIN (
                PIVOT (SELECT geo_id, feature, value FROM features_long WHERE level = '{level}')
                ON feature IN ({', '.join("'" + f + "'" for f in features)})
                USING first(value) GROUP BY geo_id
              ) f USING (geo_id)
              ORDER BY g.geo_id
            ) TO '{paths.MARTS_DIR / f"features_{level}.parquet"}' (FORMAT parquet)
        """)
        rows = con.execute(f"""
            SELECT geo_id, name, borough, area_km2,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, {simplify_tolerance}))
            FROM geo_boundaries WHERE level = '{level}'
        """).fetchall()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": i, "geometry": json.loads(gj),
             "properties": {"geo_id": gid, "name": name, "borough": boro,
                            "area_km2": round(area, 4)}}
            for i, (gid, name, boro, area, gj) in enumerate(rows)
        ]}
        (paths.MARTS_DIR / f"geo_{level}.geojson").write_text(json.dumps(fc))

    catalog = con.execute("SELECT * FROM feature_catalog ORDER BY dataset, feature").df()
    datasets = con.execute("SELECT DISTINCT dataset FROM feature_catalog").fetchall()
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "levels": [{"level": lv, "title": t} for lv, t in levels],
        "datasets": [d[0] for d in datasets],
        "features": json.loads(catalog.to_json(orient="records")),
    }
    (paths.MARTS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Published marts for %d levels to %s", len(levels), paths.MARTS_DIR)
