"""Downloads a NASR category zip and locates a specific CSV inside it.

Archive contents/naming vary slightly release to release (see spec §1), so
extraction never assumes a fixed internal path -- it searches the archive
for a filename match instead.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import requests


def download_file(url: str, dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=60, stream=True) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest_path


def extract_csv(zip_path: Path, filename: str, dest_dir: Path) -> Path:
    """Find `filename` anywhere inside `zip_path` (case-insensitive,
    ignoring any internal directory structure) and extract it to
    `dest_dir`. Raises FileNotFoundError if no member matches.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = filename.lower()

    with zipfile.ZipFile(zip_path) as zf:
        match = next(
            (name for name in zf.namelist() if Path(name).name.lower() == target),
            None,
        )
        if match is None:
            raise FileNotFoundError(
                f"'{filename}' not found inside {zip_path.name}; "
                f"archive contains: {zf.namelist()}"
            )
        dest_path = dest_dir / filename
        with zf.open(match) as src, open(dest_path, "wb") as dst:
            dst.write(src.read())

    return dest_path
