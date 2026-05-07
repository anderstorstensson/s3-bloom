"""Tests for discovery.search."""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from s3bloom.config import BoundingBox, TimePeriod
from s3bloom.discovery.search import (
    ProductInfo,
    _bbox_filter,
    _extract_satellite,
    _parse_datetime,
    search_products,
)


class TestExtractSatellite:
    def test_s3a(self):
        assert _extract_satellite("S3A_OL_2_WFR____20240315T091500") == "S3A"

    def test_s3b(self):
        assert _extract_satellite("S3B_OL_2_WFR____20240315T091500") == "S3B"

    def test_unknown_prefix_returns_s3x(self):
        assert _extract_satellite("S3C_OL_2_WFR____20240315T091500") == "S3X"

    def test_empty_string_returns_s3x(self):
        assert _extract_satellite("") == "S3X"


class TestParseDatetime:
    def test_z_suffix(self):
        dt = _parse_datetime("2024-03-15T09:15:00.000Z")
        assert dt == datetime(2024, 3, 15, 9, 15, 0, tzinfo=timezone.utc)

    def test_utc_offset(self):
        dt = _parse_datetime("2024-03-15T09:15:00+00:00")
        assert dt.year == 2024
        assert dt.tzinfo is not None

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty datetime string"):
            _parse_datetime("")


class TestBboxFilter:
    def test_contains_polygon_keyword(self):
        bbox = BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)
        result = _bbox_filter(bbox)
        assert "POLYGON" in result

    def test_contains_odata_intersects(self):
        bbox = BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)
        result = _bbox_filter(bbox)
        assert "OData.CSC.Intersects" in result

    def test_contains_srid(self):
        bbox = BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)
        result = _bbox_filter(bbox)
        assert "SRID=4326" in result

    def test_polygon_is_closed(self):
        bbox = BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)
        result = _bbox_filter(bbox)
        inner = result.split("POLYGON((")[1].split("))")[0]
        vertices = [v.strip() for v in inner.split(",")]
        assert vertices[0] == vertices[-1]

    def test_bbox_coords_present(self):
        bbox = BoundingBox(lon_min=7.5, lat_min=55.0, lon_max=12.5, lat_max=60.0)
        result = _bbox_filter(bbox)
        assert "7.5" in result
        assert "55.0" in result


class TestProductInfoFromOdata:
    def _entry(self, **overrides):
        base = {
            "Id": "abc-123",
            "Name": "S3A_OL_2_WFR____20240315T091500",
            "ContentDate": {"Start": "2024-03-15T09:15:00.000Z"},
            "ContentLength": 524_288_000,  # 500 MB
            "GeoFootprint": {"type": "Polygon", "coordinates": []},
            "Online": True,
        }
        base.update(overrides)
        return base

    def test_product_id(self):
        pi = ProductInfo.from_odata(self._entry())
        assert pi.product_id == "abc-123"

    def test_satellite_extraction(self):
        pi = ProductInfo.from_odata(self._entry())
        assert pi.satellite == "S3A"

    def test_s3b_satellite(self):
        pi = ProductInfo.from_odata(self._entry(Name="S3B_OL_2_WFR____20240315T091500"))
        assert pi.satellite == "S3B"

    def test_size_mb_computed(self):
        pi = ProductInfo.from_odata(self._entry(ContentLength=1_048_576))
        assert pi.size_mb == pytest.approx(1.0, abs=0.01)

    def test_zero_content_length_gives_none(self):
        pi = ProductInfo.from_odata(self._entry(ContentLength=0))
        assert pi.size_mb is None

    def test_empty_footprint_gives_none(self):
        pi = ProductInfo.from_odata(self._entry(GeoFootprint={}))
        assert pi.footprint is None

    def test_online_flag(self):
        assert ProductInfo.from_odata(self._entry(Online=True)).online is True
        assert ProductInfo.from_odata(self._entry(Online=False)).online is False

    def test_sensing_start_parsed(self):
        pi = ProductInfo.from_odata(self._entry())
        assert pi.sensing_start.year == 2024
        assert pi.sensing_start.tzinfo is not None


class TestSearchProducts:
    def _bbox(self):
        return BoundingBox(lon_min=10.0, lat_min=56.5, lon_max=13.0, lat_max=59.0)

    def _period(self):
        return TimePeriod(start_date=date(2024, 3, 15), end_date=date(2024, 3, 15))

    def _entry(self, product_id="abc-123", name="S3A_OL_2_WFR____20240315T091500"):
        return {
            "Id": product_id,
            "Name": name,
            "ContentDate": {"Start": "2024-03-15T09:15:00.000Z"},
            "ContentLength": 0,
            "GeoFootprint": {},
            "Online": True,
        }

    def _mock_session(self, pages):
        mock_session = MagicMock()
        responses = []
        for i, entries in enumerate(pages):
            mock_resp = MagicMock()
            data = {"value": entries}
            if i < len(pages) - 1:
                data["@odata.nextLink"] = "https://next-page"
            mock_resp.json.return_value = data
            mock_resp.raise_for_status = MagicMock()
            responses.append(mock_resp)
        mock_session.get.side_effect = responses
        return mock_session

    def test_empty_response_returns_empty_list(self):
        mock_session = self._mock_session([[]])
        with patch("s3bloom.discovery.download._create_session", return_value=mock_session):
            products = search_products(self._bbox(), self._period())
        assert products == []

    def test_single_page(self):
        mock_session = self._mock_session([[self._entry()]])
        with patch("s3bloom.discovery.download._create_session", return_value=mock_session):
            products = search_products(self._bbox(), self._period())
        assert len(products) == 1
        assert products[0].product_id == "abc-123"

    def test_pagination_follows_next_link(self):
        mock_session = self._mock_session([
            [self._entry("id-1")],
            [self._entry("id-2")],
        ])
        with patch("s3bloom.discovery.download._create_session", return_value=mock_session):
            products = search_products(self._bbox(), self._period())
        assert len(products) == 2
        assert mock_session.get.call_count == 2
