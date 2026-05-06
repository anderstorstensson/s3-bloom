"""Download Sentinel-3 OLCI L2 products from CDSE.

The download flow is:

1. Read CDSE credentials from the environment (``CDSE_USERNAME`` /
   ``CDSE_PASSWORD``, typically loaded from ``.env`` by the CLI).
2. Exchange the credentials for an OAuth2 access token at the Keycloak
   token endpoint. Tokens are managed by :class:`_TokenManager` which
   refreshes them ahead of expiry and on any 403 response.
3. For each product not already on disk, ``GET`` the
   ``Products({id})/$value`` endpoint, follow the redirect to the actual
   download server (re-applying the bearer token because ``requests``
   strips ``Authorization`` on cross-domain redirects), stream the zip
   to disk, extract the ``.SEN3`` directory, and delete the zip.

The pipeline is *idempotent*: a re-run with the same parameters skips
products whose ``.SEN3`` directory already exists with a manifest file.
This makes "re-run on failure" the standard recovery pattern.

Concurrency is intentionally low (sequential) because CDSE rate-limits
aggressive downloaders.
"""

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
    """Issue and refresh CDSE OAuth2 access tokens.

    A single instance is shared by all downloads in one CLI invocation.
    Access via the :attr:`token` property; the manager refreshes
    transparently when the token is missing or about to expire. Use
    :meth:`force_refresh` after a 403 response to recover from a token
    revoked server-side before its advertised expiry.
    """

    def __init__(self) -> None:
        self._token: str = ""
        self._expires_at: float = 0.0

    @property
    def token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if not self._token or self._is_expired():
            self._refresh()
        return self._token

    def force_refresh(self) -> str:
        """Discard the cached token and fetch a new one."""
        self._refresh()
        return self._token

    def _is_expired(self) -> bool:
        # Treat a token as expired slightly before its real deadline so we
        # never send a token that expires mid-request.
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
    """Build a :class:`requests.Session` with exponential-backoff retry.

    Retries cover the ``RETRY_STATUS_CODES`` set (rate-limit + 5xx). The
    backoff factor is intentionally large (~4s) because CDSE 502/503
    spikes typically last tens of seconds.

    Parameters
    ----------
    **headers : str
        Additional default headers (e.g. ``Authorization``) to attach to
        every request issued through the session.
    """
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
    """Download every product in *products* to *raw_dir*, skipping duplicates.

    Idempotent: products whose ``.SEN3`` directory is already present
    (with a ``xfdumanifest.xml`` inside) are not re-downloaded. Failed
    downloads are logged and skipped — the user is told to re-run, which
    will pick up only the missing products.

    Parameters
    ----------
    products : list of ProductInfo
        Catalogue entries from :func:`s3bloom.discovery.search.search_products`.
    raw_dir : pathlib.Path
        Target directory; created if missing.
    config : PipelineConfig
        Currently unused but reserved for future per-run knobs (parallelism,
        bandwidth limits, etc.). Kept on the signature for stability.

    Returns
    -------
    list of pathlib.Path
        Paths to ``.SEN3`` directories that are now on disk (both
        already-present and freshly downloaded).
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
    """Download one product, extract the ``.SEN3`` directory, return its path.

    Implements a small two-attempt loop: if the server returns 403 on
    either the redirect probe or the streamed body, the token is forced
    to refresh and the request is retried once. Subsequent 403s
    propagate as ``HTTPError``.

    Returns ``None`` if the zip turned out not to contain a ``.SEN3``
    directory (a malformed product), which is logged at WARNING level.
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
    """Extract *zip_path* and return the inner ``.SEN3`` directory.

    Performs a path-traversal check against every member before
    extraction (defence against a malicious zip that tries to escape
    *raw_dir* via ``../`` entries).
    """
    resolved_raw = raw_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Defence against zip-slip: every resolved member path must be
        # contained within the target directory.
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
    """Find which products from *products* are already extracted on disk.

    A product counts as "present" when a directory whose name matches the
    product title exists *and* contains an ``xfdumanifest.xml`` file —
    the manifest's presence is treated as a marker that extraction
    completed without truncation.

    Returns
    -------
    dict[str, pathlib.Path]
        Mapping ``product_id -> path-to-.SEN3-dir`` for already-present
        products. Products not on disk are absent from the dict.
    """
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
