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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from city_fabric import paths

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="City Fabric")
# GeoJSON and feature payloads compress ~5-10x.
app.add_middleware(GZipMiddleware, minimum_size=1024)
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


def pairwise_corr(X: pd.DataFrame, y: pd.Series, method: str = "spearman"):
    """Correlation of each column of X with y over rows where both are present.

    Spearman ranks within each complete pair (matching the frontend's scatter
    stats) and needs no scipy, unlike pandas' method="spearman".
    """
    if method not in ("spearman", "pearson"):
        raise HTTPException(400, f"Unsupported method {method!r}")
    rs, ns = [], []
    for col in X.columns:
        valid = X[col].notna() & y.notna()
        a, b = X.loc[valid, col], y[valid]
        if method == "spearman":
            a, b = a.rank(), b.rank()
        ns.append(int(valid.sum()))
        rs.append(a.corr(b) if len(a) >= 3 else np.nan)
    return np.array(rs, dtype=float), np.array(ns)


@app.get("/healthz")
def healthz():
    """Liveness + data check for the platform health probe."""
    m = manifest()
    return {"status": "ok", "built_at": m["built_at"], "features": len(m["features"])}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/manifest")
def get_manifest():
    return manifest()


@app.get("/api/geo/{level}")
def get_geo(level: str):
    # Boundaries only change when marts are rebuilt (i.e. on redeploy).
    return Response(_mart(f"geo_{level}.geojson").read_bytes(), media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=3600"})


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
    corr, n = pairwise_corr(df[cols].astype(float), df[feature].astype(float), method)
    out = pd.DataFrame({"feature": cols, "r": corr, "n": n})
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
