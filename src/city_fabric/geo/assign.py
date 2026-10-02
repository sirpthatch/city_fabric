"""Stage raw datasets into typed tables and attribute each point to geographies.

stg_<dataset>: typed rows with `key` and point `geom` (from the spec's staging SQL).
asg_<dataset>: one row per key with a geo_id column per level (NULL if the point
               falls outside every polygon of that level, e.g. in the water).
"""

from __future__ import annotations

import logging

import duckdb

from city_fabric.collection import raw_relation
from city_fabric.config import DatasetSpec

log = logging.getLogger(__name__)


def stage_dataset(con: duckdb.DuckDBPyConnection, spec: DatasetSpec) -> int:
    sql = spec.staging.replace("{raw}", raw_relation(spec))
    con.execute(f"CREATE OR REPLACE TABLE stg_{spec.name} AS {sql}")
    n = con.execute(f"SELECT count(*) FROM stg_{spec.name}").fetchone()[0]
    log.info("stg_%s: %d rows", spec.name, n)
    return n


def assign_geographies(con: duckdb.DuckDBPyConnection, spec: DatasetSpec) -> None:
    levels = [r[0] for r in con.execute(
        "SELECT level FROM geo_levels ORDER BY ordinal").fetchall()]
    joins, cols = [], []
    for lv in levels:
        # Polygons within a level don't overlap, but slivers on shared edges can
        # match twice; any_value keeps exactly one assignment per point.
        joins.append(f"""
            LEFT JOIN (
              SELECT s.key, any_value(g.geo_id) AS geo_id
              FROM stg_{spec.name} s
              JOIN (SELECT * FROM geo_boundaries WHERE level = '{lv}') g
                ON ST_Intersects(g.geom, s.geom)
              GROUP BY s.key
            ) a_{lv} USING (key)""")
        cols.append(f"a_{lv}.geo_id AS {lv}")
    con.execute(f"""
        CREATE OR REPLACE TABLE asg_{spec.name} AS
        SELECT k.key, {', '.join(cols)}
        FROM (SELECT key FROM stg_{spec.name}) k
        {''.join(joins)}
    """)
    coverage = con.execute(
        f"SELECT {', '.join(f'avg(({lv} IS NOT NULL)::INT)' for lv in levels)} "
        f"FROM asg_{spec.name}"
    ).fetchone()
    log.info("asg_%s coverage: %s", spec.name,
             {lv: round(c or 0, 3) for lv, c in zip(levels, coverage)})
