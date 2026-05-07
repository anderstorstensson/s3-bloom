"""Tests for _winpath.win_path."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from s3bloom._winpath import win_path


class TestWinPath:
    def test_returns_string(self, tmp_path):
        assert isinstance(win_path(tmp_path), str)

    def test_non_windows_returns_resolved_string(self, tmp_path):
        with patch.object(sys, "platform", "linux"):
            result = win_path(tmp_path)
        assert result == str(tmp_path.resolve())
        assert not result.startswith("\\\\?\\")

    def test_windows_local_path_gets_prefix(self, tmp_path):
        with patch.object(sys, "platform", "win32"):
            result = win_path(tmp_path)
        assert result.startswith("\\\\?\\")
        assert not result.startswith("\\\\?\\UNC\\")

    def test_windows_unc_path_gets_unc_prefix(self):
        mock_path = MagicMock(spec=Path)
        mock_path.resolve.return_value = mock_path
        mock_path.__str__ = MagicMock(return_value="\\\\server\\share\\data")

        with patch.object(sys, "platform", "win32"):
            result = win_path(mock_path)

        assert result.startswith("\\\\?\\UNC\\")
        assert "server\\share\\data" in result

    def test_windows_unc_strips_leading_double_backslash(self):
        mock_path = MagicMock(spec=Path)
        mock_path.resolve.return_value = mock_path
        mock_path.__str__ = MagicMock(return_value="\\\\server\\share")

        with patch.object(sys, "platform", "win32"):
            result = win_path(mock_path)

        assert result == "\\\\?\\UNC\\server\\share"
