"""Download geographic boundary layers and load them into `geo_boundaries`.

geo_boundaries(level, geo_id, name, borough, area_km2, geom) holds every polygon
for every level, in EPSG:4326. Areas are computed in NY State Plane (EPSG:2263).
"""

from __future__ import annotations

import logging

import duckdb
import requests

from city_fabric import paths
from city_fabric.config import GeoLevel

log = logging.getLogger(__name__)

SQFT_TO_KM2 = 0.09290304 / 1e6


def download_boundaries(levels: list[GeoLevel], force: bool = False) -> None:
    paths.BOUNDARIES_DIR.mkdir(parents=True, exist_ok=True)
    for level in levels:
        dest = paths.BOUNDARIES_DIR / f"{level.name}.geojson"
        if dest.exists() and not force:
            continue
        url = f"https://{level.source.domain}/resource/{level.source.id}.geojson"
        log.info("Downloading %s boundaries from %s", level.name, url)
        resp = requests.get(url, params={"$limit": 100_000}, timeout=300)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def load_boundaries(con: duckdb.DuckDBPyConnection, levels: list[GeoLevel]) -> None:
    selects = []
    for level in levels:
        src = paths.BOUNDARIES_DIR / f"{level.name}.geojson"
        if not src.exists():
            raise FileNotFoundError(f"Missing {src}; run `cf boundaries` first")
        where = f"WHERE NOT ({level.exclude})" if level.exclude else ""
        selects.append(f"""
            SELECT
              '{level.name}'                  AS level,
              CAST({level.id} AS VARCHAR)     AS geo_id,
              CAST({level.name_expr} AS VARCHAR) AS name,
              CAST({level.borough} AS VARCHAR)   AS borough,
              ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:2263', always_xy := true))
                * {SQFT_TO_KM2}               AS area_km2,
              ST_MakeValid(geom)::GEOMETRY    AS geom
            FROM ST_Read('{src}')
            {where}""")
    con.execute("CREATE OR REPLACE TABLE geo_boundaries AS " + " UNION ALL ".join(selects))
    con.execute("""
        CREATE OR REPLACE TABLE geo_levels AS
        SELECT * FROM (VALUES {}) t(level, title, ordinal)
    """.format(", ".join(f"('{lv.name}', '{lv.title}', {i})" for i, lv in enumerate(levels))))
    counts = con.execute(
        "SELECT level, count(*) FROM geo_boundaries GROUP BY level ORDER BY level"
    ).fetchall()
    log.info("Loaded boundaries: %s", dict(counts))
