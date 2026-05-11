# s3bloom processing workflow

This document describes the complete data flow from CLI invocation to final
output files. Each step maps to a specific module in `src/s3bloom/`.

## Overview

```
CLI arguments
  │
  ▼
PipelineConfig (validated)
  │
  ▼
1. Search ──── CDSE OData API ──── list[ProductInfo]
  │
  ▼
2. Download ── OData + token auth ── .SEN3 dirs in data/raw/
  │
  ▼
3. Process (per pass) ──┐
   a. Load scene        │  satpy olci_l2 reader
   b. Build mask        │  WQSF flag bitfield
   c. Apply mask        │  bad pixels → NaN
   d. Resample          │  swath → regular grid (pyresample)
   e. Export            │  GeoTIFF + NetCDF + PNG
                        ▼
               list[PassResult]
  │
  ▼
4. Composite ── nanmean over 3-day window ── composite outputs
```

## Step 1: Product search

**Module:** `discovery/search.py`

The pipeline queries the Copernicus Data Space Ecosystem (CDSE) OData API for
Sentinel-3 OLCI Level-2 Water Full Resolution (OL_2_WFR) products that
intersect the requested bounding box and time period.

**API endpoint:** `https://catalogue.dataspace.copernicus.eu/odata/v1/Products`

The search uses an OData `$filter` with:
- `contains(Name, 'OL_2_WFR___')` — product type filter
- `ContentDate/Start ge/le` — temporal bounds
- `OData.CSC.Intersects(area=...)` — geographic intersection with a WKT polygon
  built from the bounding box

Results are sorted by sensing start time and paginated (100 products per page).

**Output:** A list of `ProductInfo` dataclasses, each containing the product ID,
title, sensing time, satellite (S3A/S3B), and download size.

## Step 2: Product download

**Module:** `discovery/download.py`

### Authentication

CDSE requires an OAuth2 access token for downloads. The pipeline:
1. Reads `CDSE_USERNAME` and `CDSE_PASSWORD` from environment (loaded from `.env`)
2. POSTs to the CDSE identity provider token endpoint
3. Receives a bearer token valid for ~10 minutes

### Download process

For each product:
1. Check if the `.SEN3` directory already exists in `data/raw/` (idempotent)
2. Request the product zip via `Products({id})/$value`
3. Follow the redirect to the download server (auth header re-applied)
4. Stream to a temporary `.zip` file with progress bar
5. Extract the `.SEN3` directory
6. Delete the zip

Products are downloaded sequentially to respect CDSE rate limits.

### Disk space

Each `.SEN3` directory is ~500 MB. By default they are kept in `data/raw/` so
re-runs can skip re-downloading. Pass `--delete-raw` to remove each `.SEN3`
directory immediately after its pass is processed successfully. Failed passes
are never deleted, so they remain available for inspection or retry.

### .SEN3 directory structure

Each downloaded product is a directory containing ~35 NetCDF files:
```
S3A_OL_2_WFR____20240315T091500_....SEN3/
├── xfdumanifest.xml         # Product manifest
├── chl_nn.nc                # Chlorophyll (neural net)
├── chl_oc4me.nc             # Chlorophyll (OC4Me)
├── tsm_nn.nc                # Total suspended matter
├── wqsf.nc                  # Water Quality Science Flags
├── geo_coordinates.nc       # Lat/lon per pixel
├── Oa01_reflectance.nc      # Band reflectances
├── ...                      # ~30 more files
```

## Step 3: Per-pass processing

Each downloaded product goes through four sub-steps. If any step fails for a
product, processing continues with the next product.

### 3a. Scene loading

**Module:** `processing/reader.py`

Uses satpy's `olci_l2` reader to load the requested datasets plus the WQSF
flag layer. The reader handles the multi-file `.SEN3` structure, coordinate
decoding, and metadata extraction.

The `.nc` files are passed directly to satpy (the XML manifest is not used).
Files that satpy doesn't recognise (tie points, instrument data, etc.) produce
harmless warnings.

**Key detail:** The satellite identifier (S3A/S3B) and sensing time are parsed
from the directory name, not from file metadata, since the naming convention is
standardised by EUMETSAT.

### 3b. Quality masking

**Module:** `processing/masking.py`

This is the scientifically critical step. The WQSF (Water Quality Science
Flags) layer is a per-pixel bitfield where each bit indicates a quality
condition.

**How flags are read:**

Rather than hardcoding bit positions (which can change between processing
baselines), the pipeline reads `flag_meanings` and `flag_masks` attributes from
the WQSF variable's metadata. This follows EUMETSAT's recommendation for
forward compatibility.

**Masking logic:**

1. Read the flag definitions from the WQSF dataset attributes
2. Resolve the flag list for `(preset, product)` — adds BAC flags for
   `chl_oc4me`, AAC flags (MEGLINT) for the NN products, and the
   per-product algorithm-failure flag
3. Split that list into *cloud-class* flags (`CLOUD`, `CLOUD_AMBIGUOUS`,
   `CLOUD_MARGIN`) and the rest, build a bitmask for each
4. Compute two per-pixel masks: `cloud_mask` and `other_mask`
5. **Spatially dilate `cloud_mask` by `dilation_px`** to buffer
   sub-pixel cloud edges and aerosol haloes
6. Final mask: `cloud_mask | other_mask` (True = bad pixel)

**Masking presets:**

| Preset | # Common flags | chl_nn total | chl_oc4me total | Cloud buffer | Notes |
|--------|---------------:|:------------:|:---------------:|:------------:|-------|
| strict (default) | 10 | 12 | 21 | 3 px | Conservative; quantitative analysis |
| moderate | 7 | 9 | 10 | 1 px | Coverage/quality balance |
| relaxed | 4 | 5 | 5 | 0 px | Maximum coverage; visual only |

The strict preset is the default because for bloom monitoring, false positives
(flagging good pixels) are preferable to false negatives (keeping bad pixels
that corrupt composites).

**Cloud-edge dilation:** Only the cloud-class portion of the mask is dilated.
OLCI's `CLOUD_MARGIN` is only ~1 pixel wide, so undetected sub-pixel cloud
edges and aerosol haloes leak through and inflate `chl_nn` near mask
boundaries. Non-cloud flags (glint, snow, sensor issues) are masked per-pixel
only — those don't have an "edge contamination" failure mode and buffering
them would lose good water for no benefit. Override per-run with
`--mask-dilation N` (or `0` to disable). When a `MaskingConfig` is built with
explicit `custom_flags`, dilation defaults to `0` so explicit lists are
honoured verbatim.

The 3-pixel default for `strict` was chosen quantitatively: on a 2025-06-02
Baltic scene it removed 85% of cloud-edge artifacts (chl_nn > 10 mg/m³
within 5 px of a cloud) while preserving 100% of interior bloom signal.
Independent precedent for cloud-risk buffering in Baltic remote sensing
comes from Hieronymi et al., *ESSD* 18, 1307 (2026).

**Prefix matching:** Flag names like `RWNEG_O2` through `RWNEG_O8` (negative
water-leaving reflectance in bands 2–8) are matched by prefix if the exact
name isn't found in the product metadata.

**Known limitation — CDOM-rich water:** `chl_nn` is documented as unreliable
in optically complex waters with high coloured dissolved organic matter,
notably the Bothnian Sea/Bay and parts of the Gulf of Finland. The neural
network confuses CDOM absorption with chlorophyll, producing inflated values
that no WQSF flag captures and that masking cannot fix. For these regions a
Baltic-tuned algorithm (A4O-ONNS, C2RCC) is the appropriate solution.

### 3c. Mask application

The boolean mask is applied to each requested dataset:
```python
data.where(~mask, other=NaN)
```

Bad pixels become NaN, which propagates correctly through all downstream
operations and is automatically excluded by nanmean compositing.

### 3d. Resampling

**Module:** `processing/resampling.py`

Satellite swath data has an irregular geometry (each pixel has its own lat/lon
from the satellite's viewing angle). This step reprojects the data onto a
regular rectangular grid.

**Target grid construction:**

1. The bounding box (WGS84 lon/lat) is transformed to the target projection
   using pyproj
2. A pyresample `AreaDefinition` is created with the specified resolution
3. For the default settings (swedish_west_coast, EPSG:3035, 300m), this
   produces a ~576 x 939 pixel grid

**Resampling method:** Nearest-neighbour with a 1500m radius of influence.
This is appropriate because:
- Ocean color products are discrete measurements, not continuous fields
- Interpolation would create artificial values at cloud edges
- The 300m output grid matches the native OLCI resolution

**Coordinate assignment:** After resampling, x/y coordinate arrays in the
target projection are attached to the DataArray, along with CRS metadata.

### 3e. Export

Each resampled dataset is exported in all configured formats:

**GeoTIFF** (`export/geotiff.py`):
- Written via rioxarray with deflate compression
- CRS embedded in the file
- Provenance stored as TIFF tags
- Directly openable in QGIS, ArcGIS, Google Earth Engine

**NetCDF** (`export/netcdf.py`):
- CF-1.8 compliant with standard_name, units, valid_min/max
- Provenance in global attributes (prefixed `s3bloom_`)
- zlib compression level 4
- Non-serializable coordinates (CRS objects from rioxarray) are stripped
  before writing; CRS stored as a WKT string attribute instead

**PNG** (`export/png.py`):
- Data converted from log10(mg/m³) to linear mg/m³ for display
- cmocean "algae" colormap (oceanographic standard for chlorophyll)
- Log-scaled colorbar with range 0.5–30 mg/m³ (appropriate for coastal
  Baltic/Kattegat)
- Title with satellite, timestamp, and masking preset
- Masked/land pixels shown in gray

## Step 4: Temporal compositing

**Module:** `compositing/temporal.py`

Individual satellite passes have large gaps due to cloud cover (often 60–90%
masked with strict settings). Compositing combines multiple passes to fill
gaps.

### Method

For each date in the time range:
1. Collect all passes within a centered N-day window (default: 3 days)
2. Stack the grids along a new `pass_idx` dimension
3. Compute `nanmean` — the arithmetic mean ignoring NaN values
4. Export the composite in all configured formats

### Why nanmean

- **Simple and transparent**: easy to understand and reproduce
- **Cloud-robust**: NaN-masked cloud pixels are automatically excluded
- **No weighting artefacts**: treats all valid observations equally
- **Handles overlap**: multiple S3A + S3B passes on the same day are combined
  naturally

### Composite dates

A composite is produced for every date between the first and last pass,
regardless of whether that specific date has data. This gives continuous
daily coverage when the window captures passes from adjacent days.

### File naming

```
s3bloom_chl_nn_composite3d_20240316_S3A-S3B.tif
         │         │          │        │
         │         │          │        └─ satellites contributing
         │         │          └────────── center date
         │         └───────────────────── 3-day window
         └─────────────────────────────── dataset
```

## Data units

The OLCI L2 `chl_nn`, `chl_oc4me`, and `tsm_nn` products store values as
**log10** of the concentration. This is important to understand:

| Stored value | Real concentration |
|--------------|-------------------|
| -1.0 | 0.1 mg m⁻³ |
| 0.0 | 1.0 mg m⁻³ |
| 0.5 | 3.2 mg m⁻³ |
| 1.0 | 10 mg m⁻³ |
| 1.5 | 31.6 mg m⁻³ |
| 2.0 | 100 mg m⁻³ |

The GeoTIFF and NetCDF outputs preserve the original log10-encoded values
(this is correct for quantitative analysis, since averaging in log-space is
standard practice for chlorophyll). The PNG maps convert to linear mg/m³ for
visual interpretation.

## Provenance tracking

**Module:** `metadata/provenance.py`

Every output file carries an immutable provenance record containing:
- Source product name and satellite
- Sensing time
- Dataset name
- Masking preset and exact flags applied
- Target projection and resolution
- Pipeline version
- Creation timestamp
- For composites: list of source products, window size, pass count

Provenance is stored as:
- TIFF tags in GeoTIFF files
- Global attributes (prefixed `s3bloom_`) in NetCDF files
- Title metadata in PNG files

## Projection

The default projection is **EPSG:3035** (ETRS89 / LAEA Europe):
- Equal-area (preserves area measurements)
- Centred on Europe (low distortion for Baltic/Nordic region)
- Standard for EU environmental reporting (INSPIRE directive)
- Units in metres (intuitive resolution specification)

Alternative projections can be specified via `--projection`, e.g.
`EPSG:4326` (WGS84 lat/lon) or `EPSG:3006` (SWEREF99 TM, Swedish national grid).

## Memory and performance

- Products are processed **one at a time** and written to disk before the next
  product is loaded, keeping memory usage bounded
- Compositing loads only the resampled grids (small: ~576 x 939 x float32 per
  pass), not the full swath data
- dask-backed lazy loading is used by satpy for the initial scene, but data is
  computed before export
- Downloads are sequential (max 2 concurrent) to respect CDSE rate limits
