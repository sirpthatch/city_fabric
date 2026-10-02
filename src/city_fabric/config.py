"""Load dataset and geography specs from config/*.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from city_fabric import paths


@dataclass
class Source:
    type: str
    domain: str
    id: str
    select: list[str] | None = None
    order: str | None = None
    where: str | None = None


@dataclass
class Refresh:
    strategy: str = "full"  # "full" | "incremental"
    watermark: str | None = None
    backfill_days: int = 90
    overlap_days: int = 0
    min_interval_hours: float = 24


@dataclass
class FeatureSpec:
    id: str
    title: str
    agg: str
    filter: str | None = None
    fill: float | None = None
    unit: str | None = None
    normalize: list[str] = field(default_factory=list)  # "area", "share"
    group_by: str | None = None
    top_n: int = 20
    description: str | None = None


@dataclass
class DatasetSpec:
    name: str
    title: str
    source: Source
    key: str
    staging: str
    refresh: Refresh = field(default_factory=Refresh)
    features: list[FeatureSpec] = field(default_factory=list)
    description: str = ""


@dataclass
class GeoLevel:
    name: str
    title: str
    source: Source
    id: str
    name_expr: str
    borough: str = "NULL"
    exclude: str | None = None


def load_dataset(path: Path) -> DatasetSpec:
    raw = yaml.safe_load(path.read_text())
    return DatasetSpec(
        name=raw["name"],
        title=raw.get("title", raw["name"]),
        description=raw.get("description", ""),
        source=Source(**raw["source"]),
        key=raw["key"],
        staging=raw["staging"],
        refresh=Refresh(**raw.get("refresh", {})),
        features=[FeatureSpec(**f) for f in raw.get("features", [])],
    )


def load_datasets(names: list[str] | None = None) -> list[DatasetSpec]:
    specs = [load_dataset(p) for p in sorted(paths.DATASETS_DIR.glob("*.yaml"))]
    if names:
        unknown = set(names) - {s.name for s in specs}
        if unknown:
            raise ValueError(f"Unknown dataset(s): {', '.join(sorted(unknown))}")
        specs = [s for s in specs if s.name in names]
    return specs


def load_geographies() -> list[GeoLevel]:
    raw = yaml.safe_load((paths.CONFIG_DIR / "geographies.yaml").read_text())
    return [
        GeoLevel(
            name=name,
            title=cfg.get("title", name),
            source=Source(**cfg["source"]),
            id=cfg["id"],
            name_expr=cfg["name"],
            borough=cfg.get("borough", "NULL"),
            exclude=cfg.get("exclude"),
        )
        for name, cfg in raw["levels"].items()
    ]
