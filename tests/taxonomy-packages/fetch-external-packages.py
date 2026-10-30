#!/usr/bin/env python3
"""Download the external taxonomy packages listed in the *.txt files beside this script,
each list into a directory of its own here, named after it, so the tests can run
Arelle offline (tests/offline_arelle.py).

File format per non-comment line:
  <package-url> [<sha256-url>]

If EXTERNAL_TAXONOMY_PACKAGES_TOKEN is set, private GitHub release assets are
resolved through the Releases API before downloading.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

certifi: ModuleType | None = None
try:
    import certifi as _certifi
except ImportError:  # pragma: no cover - certifi is present in CI and the local venv.
    pass
else:
    certifi = _certifi


RELEASE_DOWNLOAD_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/(?P<tag>[^/]+)/(?P<asset>[^/]+)$"
)


def read_packages_list(path: Path) -> list[tuple[str, str | None]]:
    packages: list[tuple[str, str | None]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        package_url = parts[0]
        checksum_url = parts[1] if len(parts) > 1 else None
        packages.append((package_url, checksum_url))
    return packages


def build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def http_request(
    url: str, *, token: str | None = None, accept: str | None = None
) -> bytes:
    headers = {
        "User-Agent": "converter-fetcher",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept

    request = Request(url, headers=headers)
    with urlopen(request, context=build_ssl_context()) as response:
        return response.read()


def download_file(url: str, output_path: Path, token: str | None) -> None:
    resolved_url = url
    resolved_accept = None

    match = RELEASE_DOWNLOAD_RE.match(url)
    if token and match:
        release_url = (
            f"https://api.github.com/repos/{match.group('owner')}/"
            f"{match.group('repo')}/releases/tags/{match.group('tag')}"
        )
        release_json = http_request(
            release_url,
            token=token,
            accept="application/vnd.github+json",
        )
        release = json.loads(release_json.decode("utf-8"))
        asset_name = match.group("asset")

        for asset in release.get("assets", []):
            if asset.get("name") == asset_name:
                resolved_url = asset["url"]
                resolved_accept = "application/octet-stream"
                break
        else:
            raise RuntimeError(f"asset not found in release: {asset_name}")

    attempts = 3
    delay = 1.0
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            data = http_request(resolved_url, token=token, accept=resolved_accept)
            output_path.write_bytes(data)
            return
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            last_error = error
            if attempt == attempts:
                break
            time.sleep(delay)
            delay *= 2

    assert last_error is not None
    raise last_error


def verify_checksum(package_path: Path, checksum_path: Path) -> None:
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if digest != expected:
        raise RuntimeError(
            f"checksum mismatch for {package_path.name}: expected {expected}, got {digest}"
        )


def describe_token(token: str | None) -> str:
    if not token:
        return "No EXTERNAL_TAXONOMY_PACKAGES_TOKEN provided"
    if token.startswith("github_pat_"):
        return "Found EXTERNAL_TAXONOMY_PACKAGES_TOKEN (looks like a GitHub fine-grained PAT)"
    return "Found EXTERNAL_TAXONOMY_PACKAGES_TOKEN (format does not look like a GitHub fine-grained PAT)"


def main() -> int:
    packages_root = Path(__file__).resolve().parent
    packages_lists = sorted(packages_root.glob("*.txt"))

    token = os.environ.get("EXTERNAL_TAXONOMY_PACKAGES_TOKEN") or None
    print(describe_token(token))

    # One directory of packages per list, a set Arelle loads together.
    for packages_list in packages_lists:
        output_dir = packages_root / packages_list.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        for package_url, checksum_url in read_packages_list(packages_list):
            package_path = output_dir / Path(urlparse(package_url).path).name
            print(f"Fetching {package_url}")
            download_file(package_url, package_path, token)

            if checksum_url:
                checksum_path = output_dir / Path(urlparse(checksum_url).path).name
                print(f"Fetching checksum {checksum_url}")
                download_file(checksum_url, checksum_path, token)

                print(f"Verifying {package_path.name}")
                verify_checksum(package_path, checksum_path)

    for path in sorted(packages_root.rglob("*.zip")):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
