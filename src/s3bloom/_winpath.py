r"""Windows extended-length path helper.

Windows limits file paths to 260 characters (MAX_PATH) unless the path
is prefixed with ``\\?\`` (local drive) or ``\\?\UNC\`` (UNC share).
These prefixes instruct the Win32 API to skip the length check.

Use :func:`win_path` when passing a path to a C-backed I/O call (zipfile,
netCDF4, GDAL, os.scandir) that would otherwise fail silently or raise
``FileNotFoundError`` on paths longer than 260 characters.
"""
from __future__ import annotations

import sys
from pathlib import Path


def win_path(p: Path) -> str:
    """Return a string path for I/O that bypasses MAX_PATH on Windows.

    On non-Windows platforms the resolved path string is returned unchanged.
    """
    s = str(p.resolve())
    if sys.platform != "win32":
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s
