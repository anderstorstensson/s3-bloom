# s3bloom

Sentinel-3 OLCI Level-2 phytoplankton bloom pipeline. Downloads ocean color
data from the Copernicus Data Space Ecosystem (CDSE), applies quality masking,
reprojects to a regular grid, and produces GeoTIFF, NetCDF, and PNG outputs
with multi-day composites.

Designed for monitoring phytoplankton blooms in the Baltic Sea, Kattegat, and
Skagerrak, but works for any region covered by Sentinel-3 OLCI.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A free [Copernicus Data Space](https://dataspace.copernicus.eu/) account

## Installation

```bash
git clone https://github.com/anderstorstensson/s3-bloom.git && cd s3-bloom

uv venv
uv sync

# Activate
source .venv/bin/activate
```

## Configuration

Create a `.env` file in the project root:

```
CDSE_USERNAME=your_copernicus_username
CDSE_PASSWORD=your_copernicus_password
```

## Quick start

```bash
# Swedish west coast, one week in March 2024
s3bloom run --start-date 2024-03-15 --end-date 2024-03-17 --bbox swedish_west_coast

# Custom bounding box, moderate masking, multiple datasets
s3bloom run \
  --start-date 2024-03-01 --end-date 2024-03-31 \
  --bbox "7.0,57.0,12.0,59.5" \
  --masking moderate \
  --datasets "chl_nn,chl_oc4me,tsm_nn"
```

## CLI reference

### `s3bloom run`

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--start-date` | `-s` | *required* | Start date (YYYY-MM-DD) |
| `--end-date` | `-e` | *required* | End date (YYYY-MM-DD) |
| `--bbox` | `-b` | *required* | Bounding box preset or `lon_min,lat_min,lon_max,lat_max` |
| `--masking` | `-m` | `strict` | Masking strictness: `strict`, `moderate`, `relaxed` |
| `--datasets` | `-d` | `chl_nn` | Comma-separated: `chl_nn`, `chl_oc4me`, `tsm_nn` |
| `--output-dir` | `-o` | `data` | Base output directory |
| `--projection` | | `EPSG:3035` | Target CRS (ETRS89-LAEA, good for Baltic/Nordic) |
| `--resolution` | | `300` | Grid resolution in metres |
| `--composite-window` | | `3` | Rolling composite window in days |
| `--formats` | | `geotiff,netcdf,png` | Output formats |
| `--verbose` | `-v` | off | Debug logging |

### `s3bloom list-presets`

Shows available bounding box and masking presets.

## Bounding box presets

| Name | Region | Lon | Lat |
|------|--------|-----|-----|
| `swedish_west_coast` | Bohuslän to Halland | 10.0–13.0 | 56.5–59.0 |
| `kattegat` | Kattegat strait | 10.0–13.0 | 55.5–58.0 |
| `skagerrak` | Skagerrak basin | 7.0–12.0 | 57.0–59.5 |
| `kattegat_skagerrak` | Combined region | 7.0–13.5 | 55.5–59.5 |
| `baltic_proper` | Central Baltic | 13.0–30.0 | 54.0–66.0 |
| `baltic_all` | Full Baltic + straits | 7.0–30.0 | 54.0–66.0 |

## Output structure

```
data/
├── raw/                # Downloaded .SEN3 products
├── processed/          # Per-pass outputs
│   ├── geotiff/        #   CRS-tagged rasters
│   ├── netcdf/         #   CF-compliant with full metadata
│   └── png/            #   Maps with colorbar (cmocean algae cmap)
└── composites/         # 3-day rolling nanmean composites
    ├── geotiff/
    ├── netcdf/
    └── png/
```

File naming:
```
s3bloom_chl_nn_pass_20240315T091500_S3A.tif          # single pass
s3bloom_chl_nn_composite3d_20240315_S3A-S3B.tif      # 3-day composite
```

## Masking

Quality masking uses the WQSF (Water Quality and Science Flags) bitfield from
each product. Flag bit positions are read from product metadata at runtime, not
hardcoded, following EUMETSAT guidance for cross-collection compatibility.

| Preset | Flags | Use case |
|--------|-------|----------|
| **strict** (default) | 20 flags incl. CLOUD_MARGIN, SUSPECT, HIGHGLINT, ADJAC, RWNEG | Conservative; best for quantitative analysis |
| **moderate** | 9 core flags | Balance between coverage and quality |
| **relaxed** | 4 flags (CLOUD, INVALID, SATURATED, SNOW_ICE) | Maximum coverage; visual inspection only |

## Datasets

| Name | Description | Units (stored) | Units (real) |
|------|-------------|----------------|--------------|
| `chl_nn` | Chlorophyll-a, neural network algorithm | log10(mg m⁻³) | mg m⁻³ |
| `chl_oc4me` | Chlorophyll-a, OC4Me band-ratio algorithm | log10(mg m⁻³) | mg m⁻³ |
| `tsm_nn` | Total suspended matter, neural network | log10(g m⁻³) | g m⁻³ |

`chl_nn` is the default. It uses a neural network trained for Case-2 (coastal)
waters, making it better suited for the optically complex Baltic/Kattegat than
the open-ocean OC4Me algorithm.

## Detailed workflow

See [docs/workflow.md](docs/workflow.md) for a step-by-step description of the
processing pipeline, data flow, and design decisions.

## Development

```bash
uv sync
uv run pytest tests/ -v
```

## License

MIT
