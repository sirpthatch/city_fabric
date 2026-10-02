"""Minimal Socrata (SODA 2) client: dataset metadata + paged CSV export."""

from __future__ import annotations

import io
import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

PAGE_SIZE = 50_000


def _session() -> requests.Session:
    s = requests.Session()
    # An app token raises rate limits; optional for modest volumes.
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        s.headers["X-App-Token"] = token
    return s


def _get(session: requests.Session, url: str, params: dict | None = None, retries: int = 5):
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=300)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} from {url}", response=resp)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            if attempt == retries - 1:
                raise
            wait = 2**attempt
            log.warning("Request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def metadata(domain: str, dataset_id: str) -> dict:
    """Dataset metadata; `rows_updated_at` is when the source data last changed."""
    resp = _get(_session(), f"https://{domain}/api/views/{dataset_id}.json")
    meta = resp.json()
    updated = meta.get("rowsUpdatedAt")
    return {
        "name": meta.get("name"),
        "rows_updated_at": (
            datetime.fromtimestamp(updated, tz=timezone.utc).isoformat() if updated else None
        ),
        "columns": [c["fieldName"] for c in meta.get("columns", [])],
    }


def iter_pages(
    domain: str,
    dataset_id: str,
    select: list[str] | None = None,
    where: str | None = None,
    order: str | None = None,
    page_size: int = PAGE_SIZE,
) -> Iterator[pd.DataFrame]:
    """Yield the query result as string-typed DataFrames, one per page."""
    session = _session()
    url = f"https://{domain}/resource/{dataset_id}.csv"
    # :id is a stable tiebreaker so offset paging never skips or repeats rows.
    order_by = f"{order}, :id" if order else ":id"
    offset = 0
    while True:
        params = {"$limit": page_size, "$offset": offset, "$order": order_by}
        if select:
            params["$select"] = ", ".join(select)
        if where:
            params["$where"] = where
        resp = _get(session, url, params)
        df = pd.read_csv(io.StringIO(resp.text), dtype=str, keep_default_na=False, na_values=[""])
        if df.empty:
            return
        yield df
        log.info("%s: fetched %d rows (offset %d)", dataset_id, len(df), offset)
        if len(df) < page_size:
            return
        offset += page_size
