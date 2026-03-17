"""Tests for provenance module."""

from datetime import datetime, timezone

from s3bloom.metadata.provenance import (
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
