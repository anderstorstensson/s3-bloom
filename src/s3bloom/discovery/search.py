"""Discover Sentinel-3 OLCI L2 products via the CDSE OData catalogue.

Search is read-only and does *not* require authentication, so this module
can be exercised offline with a network mock. Authentication is required
only for the download step (see :mod:`s3bloom.discovery.download`).

The OData query combines four filters:

* ``contains(Name, 'OL_2_WFR___')`` — Ocean Colour Level-2 Water Full
  Resolution products only.
* ``ContentDate/Start ge/le ...`` — sensing-time range.
* ``OData.CSC.Intersects(area=...)`` — geographic intersection with the
  bounding-box polygon (WGS84).

Results are paginated server-side; this module follows the
``@odata.nextLink`` cursor until exhausted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from s3bloom.config import BoundingBox, TimePeriod

logger = logging.getLogger(__name__)

ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1"
PRODUCT_TYPE = "OL_2_WFR___"
PAGE_SIZE = 100


@dataclass(frozen=True)
class ProductInfo:
    """Catalogue metadata for one Sentinel-3 OLCI L2 product.

    Attributes
    ----------
    product_id : str
        Stable CDSE UUID. Use this to construct download URLs; titles can
        change when products are reprocessed.
    title : str
        Product file-system name (without the trailing ``.zip``), e.g.
        ``S3A_OL_2_WFR____20240315T091500_..._...``.
    sensing_start : datetime
        UTC start of the sensing window.
    satellite : {"S3A", "S3B", "S3X"}
        Spacecraft identifier extracted from ``title``. ``S3X`` is a
        sentinel for unknown / future spacecraft.
    footprint : str or None
        Stringified GeoJSON footprint as returned by CDSE, or ``None`` if
        the catalogue entry omitted it.
    size_mb : float or None
        Reported product size in megabytes, or ``None`` if unknown.
    online : bool
        ``True`` if the product is available for immediate download.
        Offline products require a CDSE retrieval-from-archive request.
    """

    product_id: str
    title: str
    sensing_start: datetime
    satellite: str
    footprint: str | None
    size_mb: float | None
    online: bool

    @classmethod
    def from_odata(cls, entry: dict[str, Any]) -> ProductInfo:
        """Build a :class:`ProductInfo` from one OData catalogue entry."""
        title = entry.get("Name", "")
        satellite = _extract_satellite(title)

        content_date = entry.get("ContentDate", {})
        sensing_start = _parse_datetime(content_date.get("Start", ""))

        size_bytes = entry.get("ContentLength", 0)
        size_mb = size_bytes / (1024 * 1024) if size_bytes else None

        geo = entry.get("GeoFootprint", {})
        footprint = str(geo) if geo else None

        return cls(
            product_id=entry.get("Id", ""),
            title=title,
            sensing_start=sensing_start,
            satellite=satellite,
            footprint=footprint,
            size_mb=size_mb,
            online=entry.get("Online", True),
        )


def search_products(
    bbox: BoundingBox,
    time_period: TimePeriod,
) -> list[ProductInfo]:
    """Query CDSE for OLCI L2 WFR products in *bbox* and *time_period*.

    Parameters
    ----------
    bbox : BoundingBox
        Geographic AOI in WGS84 lon/lat. The query uses an
        ``Intersects`` filter, so partial overlaps are returned.
    time_period : TimePeriod
        Sensing-time range. Inclusive on both ends, treated as UTC.

    Returns
    -------
    list of ProductInfo
        Sorted by sensing start time, ascending. Empty list if nothing
        matches (no exception is raised in that case).

    Notes
    -----
    Pagination is handled transparently via the ``@odata.nextLink``
    cursor. The session reuses the retry-aware :func:`_create_session`
    helper from the download module so transient 5xx responses are
    retried automatically.
    """
    logger.info(
        "Searching CDSE for %s products: %s to %s, bbox=%s",
        PRODUCT_TYPE,
        time_period.start_date,
        time_period.end_date,
        bbox.as_tuple(),
    )

    filter_parts = [
        f"contains(Name,'{PRODUCT_TYPE}')",
        f"ContentDate/Start ge {time_period.start_date.isoformat()}T00:00:00.000Z",
        f"ContentDate/Start le {time_period.end_date.isoformat()}T23:59:59.999Z",
        _bbox_filter(bbox),
    ]
    filter_str = " and ".join(filter_parts)

    products: list[ProductInfo] = []
    url: str | None = (
        f"{ODATA_BASE}/Products"
        f"?$filter={filter_str}"
        f"&$orderby=ContentDate/Start asc"
        f"&$top={PAGE_SIZE}"
    )

    # Imported lazily to avoid a circular import — download.py imports
    # ProductInfo from this module.
    from s3bloom.discovery.download import _create_session

    session = _create_session()

    while url:
        logger.debug("Fetching: %s", url)
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("value", []):
            products.append(ProductInfo.from_odata(entry))

        url = data.get("@odata.nextLink")

    logger.info("Found %d products", len(products))
    return products


def _bbox_filter(bbox: BoundingBox) -> str:
    """Build the OData ``Intersects`` clause for *bbox*.

    Returns the filter substring (without the leading ``and``). The
    polygon is closed by repeating the first vertex, as required by WKT.
    """
    wkt = (
        f"POLYGON(("
        f"{bbox.lon_min} {bbox.lat_min},"
        f"{bbox.lon_max} {bbox.lat_min},"
        f"{bbox.lon_max} {bbox.lat_max},"
        f"{bbox.lon_min} {bbox.lat_max},"
        f"{bbox.lon_min} {bbox.lat_min}"
        f"))"
    )
    return f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')"


def _extract_satellite(title: str) -> str:
    """Return ``"S3A"`` or ``"S3B"`` from a product title.

    Falls back to ``"S3X"`` for unknown prefixes (e.g. future S3C/S3D
    missions) so the caller never has to handle ``None``.
    """
    if title.startswith("S3A"):
        return "S3A"
    if title.startswith("S3B"):
        return "S3B"
    return "S3X"


def _parse_datetime(dt_str: str) -> datetime:
    """Parse an ISO-8601 datetime string from CDSE into a tz-aware datetime."""
    if not dt_str:
        raise ValueError("Empty datetime string in CDSE product metadata")
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)
