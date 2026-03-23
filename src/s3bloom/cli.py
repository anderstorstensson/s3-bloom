"""Typer CLI for s3bloom pipeline."""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from s3bloom import __version__
from s3bloom.config import (
    BoundingBox,
    MaskingConfig,
    OutputConfig,
    PipelineConfig,
    TimePeriod,
)
from s3bloom.defaults import (
    BBOX_PRESETS,
    DEFAULT_DATASETS,
    DEFAULT_MASKING,
    MASKING_PRESETS,
    PRODUCT_FLAGS,
    get_masking_flags,
)

app = typer.Typer(
    name="s3bloom",
    help="Sentinel-3 OLCI L2 phytoplankton bloom pipeline.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"s3bloom {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Sentinel-3 OLCI L2 phytoplankton bloom pipeline."""


@app.command()
def run(
    start_date: str = typer.Option(
        ...,
        "--start-date",
        "-s",
        help="Start date (YYYY-MM-DD).",
    ),
    end_date: str = typer.Option(
        ...,
        "--end-date",
        "-e",
        help="End date (YYYY-MM-DD).",
    ),
    bbox: str = typer.Option(
        ...,
        "--bbox",
        "-b",
        help=(
            "Bounding box: preset name or 'lon_min,lat_min,lon_max,lat_max'. "
            f"Presets: {', '.join(BBOX_PRESETS)}."
        ),
    ),
    masking: str = typer.Option(
        DEFAULT_MASKING,
        "--masking",
        "-m",
        help=f"Masking strictness: {', '.join(MASKING_PRESETS)}.",
    ),
    datasets: str = typer.Option(
        ",".join(DEFAULT_DATASETS),
        "--datasets",
        "-d",
        help="Comma-separated dataset names (e.g. chl_nn,chl_oc4me,tsm_nn).",
    ),
    output_dir: Path = typer.Option(
        Path("data"),
        "--output-dir",
        "-o",
        help="Base output directory.",
    ),
    projection: str = typer.Option(
        "EPSG:3035",
        "--projection",
        help="Target projection (EPSG code).",
    ),
    resolution: int = typer.Option(
        300,
        "--resolution",
        help="Target resolution in metres.",
    ),
    composite_window: int = typer.Option(
        3,
        "--composite-window",
        help="Composite window size in days.",
    ),
    formats: str = typer.Option(
        "geotiff,netcdf,png",
        "--formats",
        help="Comma-separated output formats.",
    ),
    no_composites: bool = typer.Option(
        False,
        "--no-composites",
        help="Skip composite generation.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging.",
    ),
) -> None:
    """Run the full bloom processing pipeline."""
    _setup_logging(verbose)

    try:
        config = _build_config(
            start_date=start_date,
            end_date=end_date,
            bbox_str=bbox,
            masking=masking,
            datasets=datasets,
            output_dir=output_dir,
            projection=projection,
            resolution=resolution,
            composite_window=composite_window,
            formats=formats,
        )
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_config_summary(config)
    config.ensure_directories()

    from s3bloom.compositing.temporal import create_composites
    from s3bloom.discovery.download import download_products
    from s3bloom.discovery.search import search_products
    from s3bloom.processing.pipeline import process_single_pass

    console.print("\n[bold blue]Step 1/4: Searching for products...[/bold blue]")
    products = search_products(config.bbox, config.time_period)

    if not products:
        console.print("[yellow]No products found for the given parameters.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Found [green]{len(products)}[/green] products.")

    console.print("\n[bold blue]Step 2/4: Downloading products...[/bold blue]")
    product_paths = download_products(products, config.output.raw_dir, config)

    if not product_paths:
        console.print("[red]No products were downloaded successfully.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold blue]Step 3/4: Processing {len(product_paths)} passes...[/bold blue]"
    )
    pass_results = []
    for i, product_path in enumerate(product_paths, 1):
        console.print(
            f"  [{i}/{len(product_paths)}] {product_path.name}"
        )
        try:
            result = process_single_pass(product_path, config)
            pass_results.append(result)
        except Exception as exc:
            console.print(f"  [red]Failed: {exc}[/red]")
            logging.getLogger(__name__).exception(
                "Failed to process %s", product_path.name
            )

    if not pass_results:
        console.print("[red]No passes were processed successfully.[/red]")
        raise typer.Exit(code=1)

    composite_files: list[Path] = []
    if no_composites:
        console.print("\n[dim]Skipping composite generation (--no-composites)[/dim]")
    else:
        console.print(
            f"\n[bold blue]Step 4/4: Creating {config.output.composite_window_days}-day "
            f"composites...[/bold blue]"
        )
        composite_files = create_composites(pass_results, config)

    _print_summary(pass_results, composite_files, config)


@app.command()
def list_presets() -> None:
    """List available bounding box and masking presets."""
    table = Table(title="Bounding Box Presets")
    table.add_column("Name", style="green")
    table.add_column("lon_min")
    table.add_column("lat_min")
    table.add_column("lon_max")
    table.add_column("lat_max")

    for name, coords in sorted(BBOX_PRESETS.items()):
        table.add_row(name, *[str(c) for c in coords])

    console.print(table)
    console.print()

    table2 = Table(title="Masking Presets (product-aware)")
    table2.add_column("Preset", style="green")
    table2.add_column("Product", style="cyan")
    table2.add_column("Flags")

    for preset in MASKING_PRESETS:
        for product in sorted(PRODUCT_FLAGS):
            flags = get_masking_flags(preset, product)
            table2.add_row(preset, product, ", ".join(flags))

    console.print(table2)


def _build_config(
    *,
    start_date: str,
    end_date: str,
    bbox_str: str,
    masking: str,
    datasets: str,
    output_dir: Path,
    projection: str,
    resolution: int,
    composite_window: int,
    formats: str,
) -> PipelineConfig:
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    return PipelineConfig(
        bbox=BoundingBox.from_string(bbox_str),
        time_period=TimePeriod(start_date=sd, end_date=ed),
        datasets=[d.strip() for d in datasets.split(",")],
        masking=MaskingConfig(preset=masking),
        output=OutputConfig(
            base_dir=output_dir,
            formats=[f.strip() for f in formats.split(",")],
            projection=projection,
            resolution_m=resolution,
            composite_window_days=composite_window,
        ),
    )


def _print_config_summary(config: PipelineConfig) -> None:
    console.print("\n[bold]Pipeline Configuration[/bold]")
    console.print(f"  Period: {config.time_period.start_date} to {config.time_period.end_date}")
    console.print(
        f"  BBox: ({config.bbox.lon_min}, {config.bbox.lat_min}) to "
        f"({config.bbox.lon_max}, {config.bbox.lat_max})"
    )
    console.print(f"  Datasets: {', '.join(config.datasets)}")
    for ds in config.datasets:
        ds_flags = config.masking.flags_for_product(ds)
        console.print(f"  Masking ({ds}): {config.masking.preset} ({len(ds_flags)} flags)")
    console.print(f"  Projection: {config.output.projection}")
    console.print(f"  Resolution: {config.output.resolution_m}m")
    console.print(f"  Composites: {config.output.composite_window_days}-day window")
    console.print(f"  Output: {config.output.base_dir}")


def _print_summary(
    pass_results: list,
    composite_files: list[Path],
    config: PipelineConfig,
) -> None:
    total_pass_files = sum(len(pr.output_files) for pr in pass_results)
    console.print("\n[bold green]Pipeline complete![/bold green]")
    console.print(f"  Passes processed: {len(pass_results)}")
    console.print(f"  Pass output files: {total_pass_files}")
    console.print(f"  Composite files: {len(composite_files)}")
    console.print(f"  Output directory: {config.output.base_dir}")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    if not verbose:
        logging.getLogger("satpy").setLevel(logging.WARNING)
        logging.getLogger("pyresample").setLevel(logging.WARNING)
        logging.getLogger("trollsift").setLevel(logging.WARNING)
