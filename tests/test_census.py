
import pytest

from city_fabric.collection import census
from city_fabric.db import binned_median

@pytest.mark.parametrize("counts, edges, expected", [
    ([10, 20, 30], [0, 10, 20, None], 20.0),        # median at a bin edge
    ([1, 1, 8], [0, 10, 20, 30], 23.75),            # interpolated inside a bin
    ([1, 1, 8], [0, 10, 20, None], 20.0),           # open top bin -> its lower edge
    ([0, 0], [0, 1, 2], None),                      # no observations
    ([None, 4], [0, 1, 2], 1.5),                    # NULL counts treated as 0
])
def test_binned_median(counts, edges, expected):
    assert binned_median(counts, edges) == (pytest.approx(expected) if expected else None)

class FakeResponse:
    def __init__(self, text):
        self._lines = text.splitlines()
        self.encoding = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

def test_fetch_table_filters_tracts_and_nulls_sentinels(monkeypatch):
    text = "\n".join([
        "GEO_ID|B08013_E001|B08013_M001",
        "0500000US36061|1000|10",                 # county row: dropped
        "1400000US36061000100|-666666666|-222222222",
        "1400000US36061000201|5400|300",
        "1400000US06037000100|999|1",             # other state: dropped
    ])
    monkeypatch.setattr(census.requests, "get", lambda *a, **k: FakeResponse(text))
    df = census._fetch_table(2024, "B08013", ["36061"])
    assert list(df.columns) == ["b08013_e001"]    # margins of error dropped
    assert df.index.tolist() == ["1400000US36061000100", "1400000US36061000201"]
    assert df["b08013_e001"].isna().tolist() == [True, False]
