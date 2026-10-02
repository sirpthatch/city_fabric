"""Helpers for notebooks: load feature marts, profile, correlate, regress, map."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from city_fabric import paths

ID_COLS = ["geo_id", "name", "borough", "area_km2"]


def manifest() -> dict:
    return json.loads((paths.MARTS_DIR / "manifest.json").read_text())


def catalog() -> pd.DataFrame:
    return pd.DataFrame(manifest()["features"]).set_index("feature")


def levels() -> list[str]:
    return [lv["level"] for lv in manifest()["levels"]]


def load_features(level: str = "nta") -> pd.DataFrame:
    """Wide table: one row per geography, one column per feature."""
    df = pd.read_parquet(paths.MARTS_DIR / f"features_{level}.parquet")
    return df.set_index("geo_id")


def feature_ids(kind: str | None = None, dataset: str | None = None,
                pattern: str | None = None) -> list[str]:
    cat = catalog()
    if kind:
        cat = cat[cat["kind"] == kind]
    if dataset:
        cat = cat[cat["dataset"] == dataset]
    if pattern:
        cat = cat[cat.index.str.contains(pattern)]
    return cat.index.tolist()


def profile(df: pd.DataFrame, features: list[str] | None = None) -> pd.DataFrame:
    """Distribution summary per feature, including skew and share of zeros."""
    features = features or [c for c in df.columns if c not in ID_COLS]
    x = df[features].astype(float)
    out = x.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95]).T
    out["nulls"] = x.isna().sum()
    out["zeros"] = (x == 0).mean()
    out["skew"] = x.skew()
    out["cv"] = out["std"] / out["mean"].abs()
    title = catalog()["title"]
    out.insert(0, "title", title.reindex(out.index))
    return out


def corr_matrix(df: pd.DataFrame, features: list[str], method: str = "spearman") -> pd.DataFrame:
    return df[features].astype(float).corr(method=method)


def top_pairs(corr: pd.DataFrame, n: int = 20, exclude_same_base: bool = True) -> pd.DataFrame:
    """Most strongly correlated feature pairs (upper triangle), by |r|."""
    cat = catalog()
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    pairs = corr.where(mask).stack().rename("r").reset_index()
    pairs.columns = ["a", "b", "r"]
    if exclude_same_base:
        base = cat["base_feature"]
        pairs = pairs[base.reindex(pairs["a"]).values != base.reindex(pairs["b"]).values]
    pairs["abs_r"] = pairs["r"].abs()
    return pairs.sort_values("abs_r", ascending=False).head(n).reset_index(drop=True)


def regress(df: pd.DataFrame, y: str, X: list[str], log: list[str] | None = None,
            standardize: bool = False, cov_type: str = "HC3"):
    """OLS of y on X with heteroskedasticity-robust SEs.

    `log` lists variables to transform with log1p; `standardize` z-scores all
    variables so coefficients are comparable.
    """
    import statsmodels.api as sm

    data = df[[y, *X]].astype(float).dropna()
    for col in log or []:
        data[col] = np.log1p(data[col].clip(lower=0))
    if standardize:
        data = (data - data.mean()) / data.std()
    model = sm.OLS(data[y], sm.add_constant(data[X]))
    return model.fit(cov_type=cov_type)


def choropleth(level: str, values: pd.Series, ax=None, cmap: str = "viridis",
               scheme: str = "quantile", k: int = 7, title: str | None = None):
    """Draw a choropleth with matplotlib from the published GeoJSON (no GeoPandas)."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Polygon

    gj = json.loads((paths.MARTS_DIR / f"geo_{level}.geojson").read_text())
    ax = ax or plt.subplots(figsize=(7, 7))[1]
    v = values.astype(float)
    if scheme == "quantile":
        bins = np.unique(np.nanquantile(v, np.linspace(0, 1, k + 1)))
        norm_vals = np.digitize(v, bins[1:-1]) / max(len(bins) - 2, 1)
    else:
        norm_vals = (v - v.min()) / (v.max() - v.min())
    colors = pd.Series(norm_vals, index=v.index)
    cm = plt.get_cmap(cmap)

    patches, facecolors = [], []
    for feat in gj["features"]:
        gid = feat["properties"]["geo_id"]
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        c = colors.get(gid, np.nan)
        for poly in polys:
            patches.append(Polygon(np.asarray(poly[0]), closed=True))
            facecolors.append("#dddddd" if pd.isna(c) or pd.isna(v.get(gid)) else cm(c))
    ax.add_collection(PatchCollection(patches, facecolor=facecolors, edgecolor="white",
                                      linewidth=0.2))
    ax.autoscale_view()
    ax.set_aspect(1 / np.cos(np.radians(40.7)))
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=10, loc="left")
    return ax
