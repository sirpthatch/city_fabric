"""DuckDB connection helpers."""

from __future__ import annotations

import duckdb
from duckdb.sqltypes import DOUBLE

from city_fabric import paths


def binned_median(counts: list | None, edges: list | None) -> float | None:
    """Median from binned counts by linear interpolation within the median bin.

    `edges` has one more entry than `counts`; a NULL last edge marks an
    open-ended top bin, in which case its lower edge is returned. This is the
    standard way to aggregate medians (e.g. ACS income brackets) across areas.
    """
    if not counts or not edges or len(edges) != len(counts) + 1:
        return None
    counts = [c or 0.0 for c in counts]
    total = sum(counts)
    if total <= 0:
        return None
    half, cum = total / 2, 0.0
    for i, c in enumerate(counts):
        if cum + c >= half and c > 0:
            lo, hi = edges[i], edges[i + 1]
            if hi is None:
                return float(lo)
            return lo + (half - cum) / c * (hi - lo)
        cum += c
    return None


def connect(read_only: bool = False, path=None) -> duckdb.DuckDBPyConnection:
    target = path or paths.WAREHOUSE
    if target != ":memory:":
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target), read_only=read_only)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.create_function("binned_median", binned_median, [duckdb.list_type(DOUBLE)] * 2, DOUBLE,
                        null_handling="special")
    return con
