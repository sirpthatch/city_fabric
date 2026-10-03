"""Collect datasets into raw parquet snapshots and track source freshness.

Raw layout: data/raw/<dataset>/<run-timestamp>.parquet, every column stored as
string plus an `_ingested_at` timestamp. Typing happens later in staging.

- full:        each run writes a complete snapshot and removes older ones.
- incremental: each run appends a part with rows whose watermark column is at or
               after (last watermark - overlap); readers dedupe on the key,
               keeping the most recently ingested version.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from city_fabric import paths
from city_fabric.collection import census, socrata
from city_fabric.config import DatasetSpec

log = logging.getLogger(__name__)


def _load_state() -> dict:
    if paths.STATE_FILE.exists():
        return json.loads(paths.STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    paths.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _write_pages(spec: DatasetSpec, pages, dest) -> tuple[int, str | None]:
    """Stream pages into one parquet file. Returns (rows, max watermark value)."""
    tmp = dest.with_suffix(".parquet.tmp")
    ingested_at = _now().isoformat()
    writer, rows, max_wm = None, 0, None
    wm_col = spec.refresh.watermark
    try:
        for df in pages:
            df["_ingested_at"] = ingested_at
            table = pa.Table.from_pandas(df, preserve_index=False)
            if writer is None:
                schema = pa.schema([(name, pa.string()) for name in table.column_names])
                writer = pq.ParquetWriter(tmp, schema, compression="zstd")
            writer.write_table(table.cast(schema))
            rows += len(df)
            if wm_col and wm_col in df and df[wm_col].notna().any():
                page_max = df[wm_col].max()
                max_wm = page_max if max_wm is None else max(max_wm, page_max)
    finally:
        if writer is not None:
            writer.close()
    if rows:
        tmp.rename(dest)
    return rows, max_wm


def _metadata(spec: DatasetSpec) -> dict:
    if spec.source.type == "socrata":
        return socrata.metadata(spec.source.domain, spec.source.id)
    if spec.source.type == "census_acs":
        return census.metadata(spec.source)
    raise NotImplementedError(f"Source type {spec.source.type!r} is not supported")


def collect(spec: DatasetSpec, force: bool = False) -> dict:
    """Refresh one dataset if it is due. Returns the updated state entry."""
    state = _load_state()
    entry = state.get(spec.name, {})
    meta = _metadata(spec)

    if not force and entry.get("last_run"):
        since = _now() - datetime.fromisoformat(entry["last_run"])
        if since < timedelta(hours=spec.refresh.min_interval_hours):
            log.info("%s: skipped, last run %s ago", spec.name, since)
            return entry | {"skipped": "min_interval"}
        if meta["rows_updated_at"] and meta["rows_updated_at"] == entry.get("source_updated_at"):
            log.info("%s: skipped, source unchanged since %s", spec.name, meta["rows_updated_at"])
            return entry | {"skipped": "unchanged"}

    out_dir = paths.RAW_DIR / spec.name
    out_dir.mkdir(parents=True, exist_ok=True)
    run_at = _now()
    dest = out_dir / f"{run_at.strftime('%Y%m%dT%H%M%SZ')}.parquet"

    where = spec.source.where
    if spec.refresh.strategy == "incremental" and spec.source.type != "socrata":
        raise ValueError(f"{spec.name}: incremental refresh is only supported for socrata")
    if spec.refresh.strategy == "incremental":
        if not spec.refresh.watermark:
            raise ValueError(f"{spec.name}: incremental refresh requires a watermark column")
        if entry.get("watermark"):
            start = datetime.fromisoformat(entry["watermark"]) - timedelta(
                days=spec.refresh.overlap_days
            )
        else:
            start = run_at.replace(tzinfo=None) - timedelta(days=spec.refresh.backfill_days)
        clause = f"{spec.refresh.watermark} >= '{start.strftime('%Y-%m-%dT%H:%M:%S')}'"
        where = f"({where}) AND {clause}" if where else clause
    elif spec.refresh.strategy != "full":
        raise ValueError(f"{spec.name}: unknown refresh strategy {spec.refresh.strategy!r}")

    if spec.source.type == "census_acs":
        log.info("%s: collecting %s", spec.name, meta["name"])
        pages = census.iter_pages(spec.source)
    else:
        log.info("%s: collecting from %s/%s where %s", spec.name, spec.source.domain,
                 spec.source.id, where or "<all>")
        pages = socrata.iter_pages(
            spec.source.domain, spec.source.id,
            select=spec.source.select, where=where, order=spec.source.order,
        )
    rows, max_wm = _write_pages(spec, pages, dest)

    if spec.refresh.strategy == "full" and rows:
        for old in out_dir.glob("*.parquet"):
            if old != dest:
                old.unlink()

    entry = {
        "last_run": run_at.isoformat(),
        "source_updated_at": meta["rows_updated_at"],
        "rows_last_run": rows,
        "watermark": max_wm or entry.get("watermark"),
        "strategy": spec.refresh.strategy,
    }
    state[spec.name] = entry
    _save_state(state)
    log.info("%s: wrote %d rows to %s", spec.name, rows, dest.name if rows else "<nothing>")
    return entry


def collection_status(specs: list[DatasetSpec]) -> list[dict]:
    """Local state vs. source freshness for each dataset."""
    state = _load_state()
    out = []
    for spec in specs:
        entry = state.get(spec.name, {})
        meta = _metadata(spec)
        files = list((paths.RAW_DIR / spec.name).glob("*.parquet"))
        out.append({
            "dataset": spec.name,
            "strategy": spec.refresh.strategy,
            "last_run": entry.get("last_run"),
            "source_updated_at": meta["rows_updated_at"],
            "stale": meta["rows_updated_at"] != entry.get("source_updated_at"),
            "raw_files": len(files),
            "raw_mb": round(sum(f.stat().st_size for f in files) / 1e6, 1),
        })
    return out


def raw_relation(spec: DatasetSpec) -> str:
    """SQL relation over the current, deduplicated raw rows of a dataset."""
    files = sorted((paths.RAW_DIR / spec.name).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No raw data for {spec.name}; run `cf collect {spec.name}`")
    if spec.refresh.strategy == "full":
        return f"read_parquet('{files[-1]}')"
    glob = paths.RAW_DIR / spec.name / "*.parquet"
    return (
        f"(SELECT * EXCLUDE (_rn) FROM (SELECT *, row_number() OVER ("
        f"PARTITION BY {spec.key} ORDER BY _ingested_at DESC) AS _rn "
        f"FROM read_parquet('{glob}', union_by_name = true)) WHERE _rn = 1)"
    )
