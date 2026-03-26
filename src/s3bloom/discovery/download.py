"""CDSE product download via OData API with idempotency and progress."""

from __future__ import annotations

import logging
import os
import time
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RetryError
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)

from s3bloom.config import PipelineConfig
from s3bloom.discovery.search import ODATA_BASE, ProductInfo

logger = logging.getLogger(__name__)
console = Console()

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
RETRY_TOTAL = 5
RETRY_BACKOFF = 4.0

# Refresh token when less than this many seconds remain before expiry.
_TOKEN_REFRESH_MARGIN_S = 60
# Default token lifetime assumed when the server doesn't report one.
_TOKEN_DEFAULT_LIFETIME_S = 300


class _TokenManager:
    """Manages CDSE access tokens with automatic refresh before expiry."""

    def __init__(self) -> None:
        self._token: str = ""
        self._expires_at: float = 0.0

    @property
    def token(self) -> str:
        if not self._token or self._is_expired():
            self._refresh()
        return self._token

    def force_refresh(self) -> str:
        self._refresh()
        return self._token

    def _is_expired(self) -> bool:
        return time.monotonic() >= (self._expires_at - _TOKEN_REFRESH_MARGIN_S)

    def _refresh(self) -> None:
        logger.debug("Refreshing CDSE access token")
        username = os.environ.get("CDSE_USERNAME", "")
        password = os.environ.get("CDSE_PASSWORD", "")

        if not username or not password:
            raise RuntimeError(
                "CDSE credentials required. Set CDSE_USERNAME and CDSE_PASSWORD "
                "environment variables."
            )

        session = _create_session()
        resp = session.post(
            TOKEN_URL,
            data={
                "client_id": "cdse-public",
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise RuntimeError("Failed to obtain access token from CDSE")

        expires_in = int(body.get("expires_in", _TOKEN_DEFAULT_LIFETIME_S))
        self._token = token
        self._expires_at = time.monotonic() + expires_in
        logger.info("CDSE token refreshed (expires in %ds)", expires_in)


def _create_session(**headers: str) -> requests.Session:
    """Create a requests session with retry logic for transient errors."""
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    if headers:
        session.headers.update(headers)
    return session


def download_products(
    products: list[ProductInfo],
    raw_dir: Path,
    config: PipelineConfig,
) -> list[Path]:
    """Download products to raw_dir. Skips already-downloaded products.

    Returns list of paths to downloaded .SEN3 directories.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    already_present = _find_existing_products(raw_dir, products)
    to_download = [p for p in products if p.product_id not in already_present]

    if already_present:
        console.print(
            f"[green]Skipping {len(already_present)} already-downloaded "
            f"products[/green]"
        )
        for path in already_present.values():
            downloaded.append(path)

    if not to_download:
        console.print("[green]All products already downloaded.[/green]")
        return downloaded

    console.print(
        f"[blue]Downloading {len(to_download)} products to {raw_dir}[/blue]"
    )

    token_mgr = _TokenManager()
    failed: list[str] = []

    for i, product in enumerate(to_download, 1):
        console.print(
            f"  [{i}/{len(to_download)}] {product.title} "
            f"({product.size_mb:.0f} MB)" if product.size_mb else
            f"  [{i}/{len(to_download)}] {product.title}"
        )
        try:
            path = _download_single(product, raw_dir, token_mgr)
            if path:
                downloaded.append(path)
        except RetryError:
            failed.append(product.title)
            console.print(
                f"  [red]Failed: CDSE server unavailable after "
                f"{RETRY_TOTAL} retries (502 Bad Gateway). "
                f"This is a server-side issue.[/red]"
            )
            logger.warning("CDSE server error for %s", product.title)
        except Exception as exc:
            failed.append(product.title)
            console.print(f"  [red]Failed: {exc}[/red]")
            logger.exception("Failed to download %s", product.title)

    if failed:
        console.print(
            f"\n[yellow]{len(failed)} downloads failed due to CDSE server "
            f"errors. Re-run to retry — already-downloaded products will "
            f"be skipped.[/yellow]"
        )

    console.print(
        f"[green]Download complete. {len(downloaded)} products available.[/green]"
    )
    return downloaded


def _download_single(
    product: ProductInfo,
    raw_dir: Path,
    token_mgr: _TokenManager,
) -> Path | None:
    """Download a single product via OData and extract the .SEN3 directory.

    If the server returns 403 (expired token), the token is refreshed
    and the download is retried once.
    """
    zip_path = raw_dir / f"{product.title}.zip"

    for attempt in range(2):
        token = token_mgr.token
        url = f"{ODATA_BASE}/Products({product.product_id})/$value"
        headers = {"Authorization": f"Bearer {token}"}

        # First request without streaming to follow redirects,
        # then re-apply auth header on the final URL since requests
        # strips Authorization on cross-domain redirects.
        session = _create_session(**headers)
        initial = session.get(url, allow_redirects=False, timeout=30)

        if initial.status_code == 403 and attempt == 0:
            logger.info("Got 403 on redirect check, refreshing token")
            token_mgr.force_refresh()
            continue

        if initial.is_redirect or initial.status_code in (301, 302, 303, 307, 308):
            url = initial.headers["Location"]

        resp = session.get(url, stream=True, timeout=300)

        if resp.status_code == 403 and attempt == 0:
            resp.close()
            logger.info("Got 403 on download, refreshing token")
            token_mgr.force_refresh()
            continue

        with resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))

            with Progress(
                TextColumn("    "),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("downloading", total=total)

                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

        sen3_path = _extract_zip(zip_path, raw_dir)
        zip_path.unlink()
        return sen3_path

    return None


def _extract_zip(zip_path: Path, raw_dir: Path) -> Path | None:
    """Extract .zip and return path to the .SEN3 directory inside."""
    resolved_raw = raw_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            member_path = (raw_dir / member).resolve()
            if not str(member_path).startswith(str(resolved_raw)):
                raise ValueError(
                    f"Zip entry {member!r} would extract outside target directory"
                )

        zf.extractall(raw_dir)

        for name in zf.namelist():
            if ".SEN3/" in name:
                sen3_name = name.split(".SEN3/")[0] + ".SEN3"
                return raw_dir / sen3_name

    logger.warning("No .SEN3 directory found in %s", zip_path.name)
    return None


def _find_existing_products(
    raw_dir: Path,
    products: list[ProductInfo],
) -> dict[str, Path]:
    """Check which products are already downloaded."""
    existing: dict[str, Path] = {}
    if not raw_dir.exists():
        return existing

    sen3_dirs = {d.name: d for d in raw_dir.iterdir() if d.is_dir()}

    for product in products:
        safe_title = product.title.rstrip(".")
        for dir_name, dir_path in sen3_dirs.items():
            if safe_title in dir_name or dir_name.startswith(safe_title):
                xfdumanifest = dir_path / "xfdumanifest.xml"
                if xfdumanifest.exists():
                    existing[product.product_id] = dir_path
                    break

    return existing
