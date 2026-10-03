import pandas as pd
import pytest
from fastapi.testclient import TestClient

from city_fabric import db, paths
from city_fabric.features import build_features, publish_marts
from city_fabric.geo import assign_geographies, load_boundaries, stage_dataset


@pytest.fixture
def built(workspace):
    specs = [workspace["spec"], workspace["people"]]
    with db.connect() as con:
        load_boundaries(con, [workspace["level"]])
        for spec in specs:
            stage_dataset(con, spec)
            assign_geographies(con, spec)
        build_features(con, specs)
        publish_marts(con)
    return pd.read_parquet(paths.MARTS_DIR / "features_district.parquet").set_index("geo_id")


def test_boundaries_have_area(workspace):
    with db.connect() as con:
        load_boundaries(con, [workspace["level"]])
        rows = con.execute("SELECT geo_id, name, area_km2 FROM geo_boundaries ORDER BY 1").fetchall()
    assert [r[:2] for r in rows] == [("A", "West"), ("B", "East")]
    # 0.01° x 0.01° at 40.7°N is ~0.84 km x 1.11 km.
    assert rows[0][2] == pytest.approx(0.94, rel=0.05)


def test_points_outside_all_polygons_are_unassigned(workspace):
    with db.connect() as con:
        load_boundaries(con, [workspace["level"]])
        stage_dataset(con, workspace["spec"])
        assign_geographies(con, workspace["spec"])
        assigned = dict(con.execute("SELECT key, district FROM asg_things").fetchall())
    assert assigned == {1: "A", 2: "A", 3: "A", 4: "B", 5: None}


def test_feature_values(built):
    assert built.loc["A", "things_n"] == 3
    assert built.loc["B", "things_n"] == 1
    assert built.loc["A", "things_n_per_km2"] == pytest.approx(3 / built.loc["A", "area_km2"])
    assert built.loc["A", "things_mean_size"] == pytest.approx(3.0)
    assert built.loc["A", "things_kind__noise"] == 2
    assert built.loc["B", "things_kind__heat"] == 0  # filled, not null
    assert built.loc["A", "things_kind__noise_share"] == pytest.approx(2 / 3)


def test_api(built):
    from city_fabric.viz.server import app

    client = TestClient(app)
    manifest = client.get("/api/manifest").json()
    assert {f["feature"] for f in manifest["features"]} >= {"things_n", "things_kind__noise_share"}
    resp = client.get("/api/features/district", params={"f": "things_n,things_mean_size"}).json()
    assert resp["geo_id"] == ["A", "B"]
    assert resp["values"]["things_n"] == [3.0, 1.0]
    assert client.get("/api/geo/district").json()["type"] == "FeatureCollection"
    assert client.get("/api/features/district", params={"f": "nope"}).status_code == 404
    assert client.get("/").status_code == 200


def test_per_capita(built):
    assert built.loc["A", "acs_population"] == 2000
    assert built.loc["A", "things_n_per_1k"] == pytest.approx(1000 * 3 / 2000)
    # B has 100 residents, under the 500 floor: rate suppressed, not 10/1k.
    assert pd.isna(built.loc["B", "things_n_per_1k"])


def test_per_capita_skipped_without_population(workspace):
    with db.connect() as con:
        load_boundaries(con, [workspace["level"]])
        stage_dataset(con, workspace["spec"])
        assign_geographies(con, workspace["spec"])
        build_features(con, [workspace["spec"]])
        feats = {r[0] for r in con.execute("SELECT feature FROM feature_catalog").fetchall()}
    assert "things_n_per_km2" in feats and "things_n_per_1k" not in feats
