"""ACS 5-year estimates from the Census Bureau's table-based summary files.

The summary files need no API key: one pipe-delimited file per table covering
every US geography. They are streamed and filtered to the configured counties'
tracts, keeping only estimate columns (named like `b19001_e002`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pandas as pd
import requests

from city_fabric.config import Source

log = logging.getLogger(__name__)

BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/{v}/table-based-SF/data/5YRData"
TRACT_PREFIX = "1400000US"


def _table_url(vintage: int, table: str) -> str:
    return f"{BASE.format(v=vintage)}/acsdt5y{vintage}-{table.lower()}.dat"


def metadata(source: Source) -> dict:
    # Vintages are immutable once published, so the vintage is the version.
    return {"name": f"ACS 5-year {source.vintage}", "rows_updated_at": f"acs5-{source.vintage}"}


def _fetch_table(vintage: int, table: str, counties: list[str]) -> pd.DataFrame:
    prefixes = tuple(TRACT_PREFIX + c for c in counties)
    url = _table_url(vintage, table)
    log.info("Streaming %s", url)
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        resp.encoding = "utf-8"  # unset by the server; needed for decode_unicode
        lines = resp.iter_lines(decode_unicode=True)
        header = next(lines).split("|")
        rows = [line.split("|") for line in lines if line.startswith(prefixes)]
    df = pd.DataFrame(rows, columns=header)
    keep = [c for c in df.columns if "_E" in c]
    df = df[["GEO_ID", *keep]].rename(columns=str.lower).set_index("geo_id")
    # Estimates are never negative; negative values are annotation codes
    # (-666666666 = not computable, -999999999 = suppressed, ...).
    return df.mask(df.apply(lambda col: col.str.startswith("-")))


def iter_pages(source: Source) -> Iterator[pd.DataFrame]:
    """Yield a single frame: one row per tract, estimate columns from all tables."""
    if not (source.vintage and source.tables and source.counties):
        raise ValueError("census_acs sources need vintage, tables and counties")
    frames = [_fetch_table(source.vintage, t, source.counties) for t in source.tables]
    df = pd.concat(frames, axis=1, join="outer").reset_index()
    df.insert(0, "geoid", df.pop("geo_id").str.removeprefix(TRACT_PREFIX))
    log.info("ACS %s: %d tracts × %d columns", source.vintage, len(df), df.shape[1] - 1)
    yield df
