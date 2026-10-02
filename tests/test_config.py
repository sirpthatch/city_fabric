from city_fabric import config


def test_repo_configs_load():
    specs = config.load_datasets()
    assert {s.name for s in specs} >= {"forestry_trees", "service_requests_311"}
    for spec in specs:
        assert "{raw}" in spec.staging
        assert spec.features
    levels = config.load_geographies()
    assert [lv.name for lv in levels][:2] == ["borough", "community_district"]
