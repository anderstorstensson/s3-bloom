"""CDSE product search via OData API."""

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
    """Metadata for a discovered Sentinel-3 OLCI L2 product."""

    product_id: str
    title: str
    sensing_start: datetime
    satellite: str
    footprint: str | None
    size_mb: float | None
    online: bool

    @classmethod
    def from_odata(cls, entry: dict[str, Any]) -> ProductInfo:
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
    """Search CDSE for Sentinel-3 OLCI L2 WFR products via OData API."""
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
    """Build OData geographic filter from bounding box.

    Uses OData.CSC.Intersects with a WKT polygon.
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
    """Extract satellite identifier (S3A or S3B) from product title."""
    if title.startswith("S3A"):
        return "S3A"
    if title.startswith("S3B"):
        return "S3B"
    return "S3X"


def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string from CDSE."""
    if not dt_str:
        raise ValueError("Empty datetime string in CDSE product metadata")
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)
