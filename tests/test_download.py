"""Tests for discovery.download."""
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from s3bloom.discovery.download import (
    RETRY_TOTAL,
    _TokenManager,
    _create_session,
    _extract_zip,
    _find_existing_products,
    download_products,
)
from s3bloom.discovery.search import ProductInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_product(product_id="abc-123", title="S3A_OL_2_WFR____20240315T091500"):
    return ProductInfo(
        product_id=product_id,
        title=title,
        sensing_start=datetime(2024, 3, 15, 9, 15, 0, tzinfo=timezone.utc),
        satellite="S3A",
        footprint=None,
        size_mb=None,
        online=True,
    )


def _make_sen3_dir(parent: Path, title: str, with_manifest: bool = True) -> Path:
    sen3 = parent / f"{title}.SEN3"
    sen3.mkdir(parents=True)
    if with_manifest:
        (sen3 / "xfdumanifest.xml").write_text("<manifest/>")
    return sen3


# ---------------------------------------------------------------------------
# _create_session
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_returns_requests_session(self):
        session = _create_session()
        assert isinstance(session, requests.Session)

    def test_custom_headers_attached(self):
        session = _create_session(Authorization="Bearer tok123")
        assert "Bearer tok123" in session.headers.get("Authorization", "")

    def test_has_retry_adapter_on_https(self):
        from requests.adapters import HTTPAdapter
        session = _create_session()
        adapter = session.get_adapter("https://example.com")
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries.total == RETRY_TOTAL


# ---------------------------------------------------------------------------
# _TokenManager
# ---------------------------------------------------------------------------

class TestTokenManager:
    def _token_response(self, token="tok123", expires_in=600):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": token, "expires_in": expires_in}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def _patched_session(self, resp):
        mock_session = MagicMock()
        mock_session.post.return_value = resp
        return mock_session

    def test_no_credentials_raises(self):
        mgr = _TokenManager()
        env = {k: v for k, v in os.environ.items() if k not in ("CDSE_USERNAME", "CDSE_PASSWORD")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="CDSE credentials"):
                _ = mgr.token

    def test_fetches_token_with_credentials(self):
        mgr = _TokenManager()
        resp = self._token_response("mytoken")
        with patch.dict(os.environ, {"CDSE_USERNAME": "user", "CDSE_PASSWORD": "pass"}):
            with patch("s3bloom.discovery.download._create_session", return_value=self._patched_session(resp)):
                token = mgr.token
        assert token == "mytoken"

    def test_token_cached_on_second_access(self):
        mgr = _TokenManager()
        resp = self._token_response()
        mock_session = self._patched_session(resp)
        with patch.dict(os.environ, {"CDSE_USERNAME": "user", "CDSE_PASSWORD": "pass"}):
            with patch("s3bloom.discovery.download._create_session", return_value=mock_session):
                _ = mgr.token
                _ = mgr.token
        assert mock_session.post.call_count == 1

    def test_force_refresh_discards_cached_token(self):
        mgr = _TokenManager()
        mgr._token = "old_token"
        mgr._expires_at = float("inf")

        resp = self._token_response("new_token")
        with patch.dict(os.environ, {"CDSE_USERNAME": "user", "CDSE_PASSWORD": "pass"}):
            with patch("s3bloom.discovery.download._create_session", return_value=self._patched_session(resp)):
                new_token = mgr.force_refresh()
        assert new_token == "new_token"

    def test_is_expired_initially(self):
        mgr = _TokenManager()
        assert mgr._is_expired()

    def test_not_expired_after_fresh_fetch(self):
        mgr = _TokenManager()
        resp = self._token_response(expires_in=3600)
        with patch.dict(os.environ, {"CDSE_USERNAME": "user", "CDSE_PASSWORD": "pass"}):
            with patch("s3bloom.discovery.download._create_session", return_value=self._patched_session(resp)):
                _ = mgr.token
        assert not mgr._is_expired()

    def test_missing_access_token_in_response_raises(self):
        mgr = _TokenManager()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        with patch.dict(os.environ, {"CDSE_USERNAME": "user", "CDSE_PASSWORD": "pass"}):
            with patch("s3bloom.discovery.download._create_session", return_value=self._patched_session(mock_resp)):
                with pytest.raises(RuntimeError, match="Failed to obtain access token"):
                    _ = mgr.token


# ---------------------------------------------------------------------------
# _extract_zip
# ---------------------------------------------------------------------------

class TestExtractZip:
    def _make_zip(self, tmp_path: Path, members: dict[str, str]) -> Path:
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return zip_path

    def test_returns_sen3_path(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        zip_path = self._make_zip(tmp_path, {
            f"{title}.SEN3/xfdumanifest.xml": "<manifest/>",
            f"{title}.SEN3/data.nc": "data",
        })
        result = _extract_zip(zip_path, tmp_path)
        assert result is not None
        assert ".SEN3" in str(result)

    def test_no_sen3_returns_none(self, tmp_path):
        zip_path = self._make_zip(tmp_path, {"some_file.txt": "content"})
        result = _extract_zip(zip_path, tmp_path)
        assert result is None

    def test_zip_slip_raises(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        zip_path = self._make_zip(sub, {"../evil.txt": "evil content"})
        with pytest.raises(ValueError, match="outside target directory"):
            _extract_zip(zip_path, sub)


# ---------------------------------------------------------------------------
# _find_existing_products
# ---------------------------------------------------------------------------

class TestFindExistingProducts:
    def test_finds_product_with_manifest(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        _make_sen3_dir(tmp_path, title, with_manifest=True)
        product = _make_product(title=title)

        result = _find_existing_products(tmp_path, [product])
        assert product.product_id in result

    def test_ignores_dir_without_manifest(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        _make_sen3_dir(tmp_path, title, with_manifest=False)
        product = _make_product(title=title)

        result = _find_existing_products(tmp_path, [product])
        assert product.product_id not in result

    def test_empty_products_list(self, tmp_path):
        result = _find_existing_products(tmp_path, [])
        assert result == {}

    def test_nonexistent_raw_dir_returns_empty(self, tmp_path):
        result = _find_existing_products(tmp_path / "nonexistent", [])
        assert result == {}

    def test_returns_correct_path(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        sen3_dir = _make_sen3_dir(tmp_path, title)
        product = _make_product(title=title)

        result = _find_existing_products(tmp_path, [product])
        assert result[product.product_id] == sen3_dir


# ---------------------------------------------------------------------------
# download_products (high-level, skip-logic only)
# ---------------------------------------------------------------------------

class TestDownloadProducts:
    def test_skips_already_downloaded_product(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        _make_sen3_dir(tmp_path, title)
        product = _make_product(title=title)
        config = MagicMock()

        with patch("s3bloom.discovery.download._download_single") as mock_dl:
            result = download_products([product], tmp_path, config)

        mock_dl.assert_not_called()
        assert len(result) == 1

    def test_all_present_returns_paths_without_downloading(self, tmp_path):
        title = "S3A_OL_2_WFR____20240315T091500"
        sen3 = _make_sen3_dir(tmp_path, title)
        product = _make_product(title=title)
        config = MagicMock()

        result = download_products([product], tmp_path, config)
        assert sen3 in result

    def test_failed_download_does_not_raise(self, tmp_path):
        product = _make_product()
        config = MagicMock()

        with patch("s3bloom.discovery.download._download_single", side_effect=RuntimeError("network error")):
            with patch("s3bloom.discovery.download._TokenManager"):
                result = download_products([product], tmp_path, config)

        assert result == []
