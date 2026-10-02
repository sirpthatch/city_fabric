"""FastAPI app serving published marts and the map frontend.

Reads only data/marts/, so it never contends with the pipeline's DuckDB lock.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from city_fabric import paths

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="City Fabric")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _mart(name: str) -> Path:
    path = paths.MARTS_DIR / name
    if not path.exists():
        raise HTTPException(404, f"{name} not found; run `cf build`")
    return path


@lru_cache(maxsize=32)
def _load_table(level: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(paths.MARTS_DIR / f"features_{level}.parquet")


def table(level: str) -> pd.DataFrame:
    path = _mart(f"features_{level}.parquet")
    return _load_table(level, path.stat().st_mtime)


def manifest() -> dict:
    return json.loads(_mart("manifest.json").read_text())


def _clean(values: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 6) for v in values]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/manifest")
def get_manifest():
    return manifest()


@app.get("/api/geo/{level}")
def get_geo(level: str):
    return Response(_mart(f"geo_{level}.geojson").read_bytes(), media_type="application/geo+json")


@app.get("/api/features/{level}")
def get_features(level: str, f: str = Query(..., description="Comma-separated feature ids")):
    df = table(level)
    ids = [x for x in f.split(",") if x]
    missing = [x for x in ids if x not in df.columns]
    if missing:
        raise HTTPException(404, f"Unknown features: {missing}")
    return {
        "geo_id": df["geo_id"].tolist(),
        "name": df["name"].tolist(),
        "values": {x: _clean(df[x]) for x in ids},
    }


@app.get("/api/correlates/{level}/{feature}")
def get_correlates(level: str, feature: str, method: str = "spearman", limit: int = 15,
                   exclude_same_base: bool = True):
    """Features ranked by absolute correlation with `feature` across geographies."""
    df = table(level)
    if feature not in df.columns:
        raise HTTPException(404, f"Unknown feature {feature}")
    catalog = {c["feature"]: c for c in manifest()["features"]}
    base = catalog.get(feature, {}).get("base_feature")
    cols = [c for c in catalog if c in df.columns and c != feature
            and not (exclude_same_base and catalog[c]["base_feature"] == base)]
    numeric = df[cols].astype(float)
    corr = numeric.corrwith(df[feature].astype(float), method=method)
    n = numeric.notna().mul(df[feature].notna(), axis=0).sum()
    out = pd.DataFrame({"feature": corr.index, "r": corr.values, "n": n[corr.index].values})
    out = out.dropna().assign(abs_r=lambda d: d["r"].abs()).sort_values("abs_r", ascending=False)
    return [
        {"feature": row.feature, "title": catalog[row.feature]["title"],
         "r": round(row.r, 4), "n": int(row.n)}
        for row in out.head(limit).itertuples()
    ]


@app.get("/api/summary/{level}")
def get_summary(level: str):
    """Distribution summary for every feature at a level."""
    df = table(level)
    catalog = manifest()["features"]
    out = []
    for c in catalog:
        if c["feature"] not in df.columns:
            continue
        s = df[c["feature"]].astype(float)
        q = s.quantile([0.25, 0.5, 0.75]) if s.notna().any() else pd.Series([np.nan] * 3)
        out.append({
            "feature": c["feature"], "n": int(s.notna().sum()),
            "mean": s.mean(), "std": s.std(), "min": s.min(),
            "p25": q.iloc[0], "median": q.iloc[1], "p75": q.iloc[2], "max": s.max(),
        })
    return json.loads(pd.DataFrame(out).to_json(orient="records"))
