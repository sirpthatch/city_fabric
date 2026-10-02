"""`cf` command line: collect -> boundaries -> build -> serve."""

from __future__ import annotations

import logging
from typing import Annotated, Optional

import typer

from city_fabric import config, db

app = typer.Typer(help="City Fabric: NYC urban data pipeline.", no_args_is_help=True)

Datasets = Annotated[Optional[list[str]], typer.Argument(help="Dataset names (default: all)")]


@app.callback()
def main(verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def boundaries(force: Annotated[bool, typer.Option(help="Re-download")] = False):
    """Download geographic boundary layers and load them into the warehouse."""
    from city_fabric.geo import download_boundaries, load_boundaries

    levels = config.load_geographies()
    download_boundaries(levels, force=force)
    with db.connect() as con:
        load_boundaries(con, levels)


@app.command()
def collect(names: Datasets = None,
            force: Annotated[bool, typer.Option(help="Ignore freshness checks")] = False):
    """Pull new data for datasets whose source has changed."""
    from city_fabric.collection import collect as collect_one

    for spec in config.load_datasets(names):
        entry = collect_one(spec, force=force)
        typer.echo(f"{spec.name}: {entry.get('skipped') or f'{entry['rows_last_run']} rows'}")


@app.command()
def status(names: Datasets = None):
    """Show local collection state vs. source freshness."""
    from city_fabric.collection import collection_status

    for row in collection_status(config.load_datasets(names)):
        flag = "STALE" if row["stale"] else "ok"
        typer.echo(f"{row['dataset']:<24} {flag:<6} last_run={row['last_run']} "
                   f"source_updated={row['source_updated_at']} files={row['raw_files']} "
                   f"size={row['raw_mb']}MB")


@app.command()
def build(names: Datasets = None):
    """Stage raw data, attribute points to geographies, build features, publish marts."""
    from city_fabric.features import build_features, publish_marts
    from city_fabric.geo import assign_geographies, stage_dataset

    specs = config.load_datasets()
    targets = config.load_datasets(names) if names else specs
    with db.connect() as con:
        for spec in targets:
            stage_dataset(con, spec)
            assign_geographies(con, spec)
        # Features are rebuilt for every dataset that has been staged.
        staged = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'asg_%'"
        ).fetchall()}
        build_features(con, [s for s in specs if f"asg_{s.name}" in staged])
        publish_marts(con)


@app.command()
def run(force: Annotated[bool, typer.Option()] = False):
    """Full pipeline: boundaries (if missing), collect, build."""
    boundaries(force=False)
    collect(names=None, force=force)
    build(names=None)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000,
          reload: Annotated[bool, typer.Option()] = False):
    """Serve the interactive map."""
    import uvicorn

    uvicorn.run("city_fabric.viz.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
