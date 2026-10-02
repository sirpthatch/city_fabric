"""DuckDB connection helpers."""

import duckdb

from city_fabric import paths


def connect(read_only: bool = False, path=None) -> duckdb.DuckDBPyConnection:
    target = path or paths.WAREHOUSE
    if target != ":memory:":
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target), read_only=read_only)
    con.execute("INSTALL spatial; LOAD spatial;")
    return con
