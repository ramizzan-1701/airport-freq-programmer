"""Downloads a NASR category zip and locates a specific CSV inside it.

Archive contents/naming vary slightly release to release (see spec §1), so
extraction never assumes a fixed internal path -- it searches the archive
for a filename match instead.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Callable

import requests

# (received, total_or_None). Taken as a parameter rather than reached
# for globally, so this module stays independent of whatever is watching.
OnBytes = Callable[[int, "int | None"], None]


def download_file(url: str, dest_path: Path, on_bytes: OnBytes | None = None) -> Path:
    """Stream `url` to `dest_path`, optionally reporting as it goes.

    `on_bytes(received, total)` is called after each chunk; `total` is
    None when the server sends no Content-Length. It may raise to abort
    the download -- that is how cancelling works, since this loop is the
    only place a fetch spends long enough to be worth interrupting.

    The timeout is per-read rather than total, so a genuinely slow
    connection is fine as long as bytes keep arriving. Only a stall
    trips it.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, timeout=60, stream=True) as response:
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            total = int(declared) if declared and declared.isdigit() else None
            received = 0
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
                    received += len(chunk)
                    if on_bytes is not None:
                        on_bytes(received, total)
    except BaseException:
        # A partial file is worse than none: it is a truncated zip that
        # would fail confusingly at extraction rather than at download.
        # Nothing reads it before then, so removing it loses nothing --
        # a later attempt re-downloads from the start either way.
        dest_path.unlink(missing_ok=True)
        raise
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
