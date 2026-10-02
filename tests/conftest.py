import json

import pandas as pd
import pytest

from city_fabric import paths
from city_fabric.config import DatasetSpec, FeatureSpec, GeoLevel, Refresh, Source


def square(x0, y0, size=0.01):
    return [[[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolated data dir with two adjacent square 'districts' and a points dataset."""
    for name, rel in [("DATA_DIR", ""), ("RAW_DIR", "raw"), ("BOUNDARIES_DIR", "boundaries"),
                      ("MARTS_DIR", "marts")]:
        monkeypatch.setattr(paths, name, tmp_path / rel)
    monkeypatch.setattr(paths, "WAREHOUSE", tmp_path / "warehouse.duckdb")
    monkeypatch.setattr(paths, "STATE_FILE", tmp_path / "state.json")

    paths.BOUNDARIES_DIR.mkdir()
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": "A", "label": "West"},
         "geometry": {"type": "Polygon", "coordinates": square(-74.00, 40.70)}},
        {"type": "Feature", "properties": {"code": "B", "label": "East"},
         "geometry": {"type": "Polygon", "coordinates": square(-73.99, 40.70)}},
    ]}
    (paths.BOUNDARIES_DIR / "district.geojson").write_text(json.dumps(fc))
    level = GeoLevel(name="district", title="District", source=Source("socrata", "x", "y"),
                     id="code", name_expr="label")

    raw = paths.RAW_DIR / "things"
    raw.mkdir(parents=True)
    # 3 points in A (two 'noise'), 1 in B ('noise'), 1 in the water (outside both).
    pd.DataFrame({
        "id": ["1", "2", "3", "4", "5"],
        "kind": ["noise", "noise", "heat", "noise", "heat"],
        "size": ["1", "3", "5", "10", "7"],
        "lon": ["-73.995", "-73.996", "-73.997", "-73.985", "-73.5"],
        "lat": ["40.705", "40.705", "40.705", "40.705", "40.5"],
        "_ingested_at": ["2026-01-01T00:00:00+00:00"] * 5,
    }).to_parquet(raw / "20260101T000000Z.parquet")

    spec = DatasetSpec(
        name="things", title="Things", key="id",
        source=Source("socrata", "example.org", "abcd-1234"),
        refresh=Refresh(strategy="full"),
        staging="""SELECT CAST(id AS BIGINT) AS key, kind, CAST(size AS DOUBLE) AS size,
                   ST_Point(CAST(lon AS DOUBLE), CAST(lat AS DOUBLE)) AS geom FROM {raw}""",
        features=[
            FeatureSpec(id="things_n", title="Things", agg="count(*)", fill=0,
                        unit="things", normalize=["area"]),
            FeatureSpec(id="things_mean_size", title="Mean size", agg="avg(size)"),
            FeatureSpec(id="things_kind", title="Kind: {value}", agg="count(*)", fill=0,
                        group_by="kind", top_n=5, normalize=["share"]),
        ],
    )
    return {"level": level, "spec": spec}
