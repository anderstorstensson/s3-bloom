"""Tests for provenance module."""

import pytest
from datetime import datetime, timezone

from s3bloom.metadata.provenance import (
    Provenance,
    create_composite_provenance,
    create_pass_provenance,
)


class TestCreatePassProvenance:
    def test_basic(self):
        prov = create_pass_provenance(
            source_product="S3A_OL_2_WFR____20240315T091500.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, 9, 15, 0, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD", "INVALID"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        assert prov.satellite == "S3A"
        assert prov.dataset == "chl_nn"
        assert prov.masking_flags == ("CLOUD", "INVALID")
        assert prov.composite_window_days is None

    def test_to_dict(self):
        prov = create_pass_provenance(
            source_product="test.SEN3",
            satellite="S3B",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="moderate",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        d = prov.to_dict()
        assert d["satellite"] == "S3B"
        assert d["masking_flags"] == "CLOUD"


class TestCreateCompositeProvenance:
    def test_basic(self):
        prov = create_composite_provenance(
            source_products=["prod1.SEN3", "prod2.SEN3"],
            satellites=["S3A", "S3B"],
            center_date=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
            composite_window_days=3,
        )
        assert prov.satellite == "S3A-S3B"
        assert prov.pass_count == 2
        assert prov.composite_window_days == 3
        assert prov.source_products == ("prod1.SEN3", "prod2.SEN3")

    def test_deduplicates_satellites(self):
        prov = create_composite_provenance(
            source_products=["p1.SEN3", "p2.SEN3", "p3.SEN3"],
            satellites=["S3A", "S3A", "S3B"],
            center_date=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
            composite_window_days=7,
        )
        assert prov.satellite == "S3A-S3B"
        assert prov.pass_count == 3


class TestToNetcdfAttrs:
    def test_prefixes_all_keys(self):
        prov = create_pass_provenance(
            source_product="test.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        attrs = prov.to_netcdf_attrs()
        for key in attrs:
            assert key.startswith("s3bloom_")

    def test_skips_empty_fields(self):
        prov = create_pass_provenance(
            source_product="test.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        attrs = prov.to_netcdf_attrs()
        # composite_window_days and pass_count are None for a pass → should be absent
        assert "s3bloom_composite_window_days" not in attrs
        assert "s3bloom_pass_count" not in attrs

    def test_all_values_are_strings(self):
        prov = create_pass_provenance(
            source_product="test.SEN3",
            satellite="S3B",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="tsm_nn",
            masking_preset="moderate",
            masking_flags=["CLOUD", "INVALID"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        attrs = prov.to_netcdf_attrs()
        for v in attrs.values():
            assert isinstance(v, str)

    def test_composite_includes_window_and_count(self):
        prov = create_composite_provenance(
            source_products=["p1.SEN3", "p2.SEN3"],
            satellites=["S3A", "S3B"],
            center_date=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
            composite_window_days=3,
        )
        attrs = prov.to_netcdf_attrs()
        assert "s3bloom_composite_window_days" in attrs
        assert attrs["s3bloom_composite_window_days"] == "3"
        assert "s3bloom_pass_count" in attrs


class TestProvenanceFrozen:
    def test_immutable(self):
        prov = create_pass_provenance(
            source_product="test.SEN3",
            satellite="S3A",
            sensing_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
            dataset="chl_nn",
            masking_preset="strict",
            masking_flags=["CLOUD"],
            projection="EPSG:3035",
            resolution_m=300,
        )
        with pytest.raises(Exception):
            prov.satellite = "S3B"  # type: ignore[misc]
