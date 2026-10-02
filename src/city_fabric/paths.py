"""Filesystem layout. Override the project root with the CITY_FABRIC_ROOT env var."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("CITY_FABRIC_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
DATASETS_DIR = CONFIG_DIR / "datasets"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # raw parquet snapshots, one folder per dataset
BOUNDARIES_DIR = DATA_DIR / "boundaries"  # source GeoJSON per geographic level
MARTS_DIR = DATA_DIR / "marts"          # published outputs consumed by viz + analysis
WAREHOUSE = DATA_DIR / "warehouse.duckdb"
STATE_FILE = DATA_DIR / "collection_state.json"
