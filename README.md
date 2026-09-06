# Airport Frequency Programmer

Turns the FAA's 28-Day NASR aeronautical data into a radio-memory XML file for the
**Yaesu FTA-850L**, loadable through Yaesu's YCE-64 programming software.

Downloading the nationwide NASR dataset yields far more frequencies than the radio's
400-memory limit, so the app is built around filtering it down: pick states, cities, a
geographic radius, frequency categories, facility types — and watch a live counter tell
you whether the current selection fits on the radio before you generate anything.

## What it does

- **Fetches** the current 28-Day NASR subscription directly from the FAA and tells you
  when a new cycle is published.
- **Classifies** every frequency into a usable category (CTAF, Tower, Ground, Clearance,
  Weather, Approach/Departure, VOR, ILS, and a long tail of raw values behind an
  "Advanced" toggle).
- **Filters** by location, radius, frequency category, site type, and facility status,
  with a live entry counter against the radio's 400-memory cap.
- **Preserves your own entries.** Import your current radio export and any memories that
  aren't in the app's six generated groups are held aside and merged back into every
  future export, untouched.
- **Generates** the YCE-64-compatible XML.

## Running it

### As a desktop app

Download a build from the [Actions tab][actions] (artifacts are attached to each run), or
build one yourself — see below. No Python installation needed to run them.

[actions]: https://github.com/ramizzan-1701/airport-freq-programmer/actions

### From a checkout

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -e ".[dev]"
```

Then either:

```bash
afp serve
```

for the browser-based UI (caches data in `./nasr_cache` next to the checkout), or:

```bash
afp-desktop
```

for the same UI in a native window (caches data in the per-user location below).

There's also a CLI for scripted queries:

```bash
afp query --data-dir nasr_cache/2026-08-06 --state CA --freq-category CTAF --output memories.xml
```

## Where your data lives

The desktop app stores downloaded NASR cycles and its small JSON state (your custom
entries, the group-setup acknowledgment) in a per-user directory, deliberately outside
the install location so it survives replacing the app:

| Platform | Path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\afp\AirportFreqProgrammer` |
| macOS | `~/Library/Application Support/AirportFreqProgrammer` |

`afp serve` and `afp query` keep using `./nasr_cache` relative to where you run them,
which is the more convenient behaviour inside a checkout.

## Building the desktop apps

Each platform's bundle must be built on that platform — neither toolchain cross-compiles.
This is why CI builds both on their respective runners.

**Windows** (produces `dist/AirportFreqProgrammer.exe`):

```bash
pip install -e ".[build-windows]"
pyinstaller --noconfirm --clean packaging/afp.spec
```

**macOS** (produces a `.app` in `dist/`):

```bash
pip install -e ".[build-macos]"
python packaging/py2app_setup.py py2app
```

### Platform notes

- **Windows** rendering uses the Edge **WebView2** runtime. It ships with current
  Windows 10 and 11, so this is normally already present; on an older machine it may
  need [installing separately][webview2].
- **macOS** builds are **unsigned** — signing requires a paid Apple Developer ID.
  Gatekeeper will refuse the app on first launch; right-click the app and choose **Open**
  to run it anyway.

[webview2]: https://developer.microsoft.com/microsoft-edge/webview2/

## Development

```bash
pytest -q
```

Architecture notes worth knowing before changing things:

- `src/afp/adapters/` is the **only** layer allowed to know FAA column names. Everything
  downstream works against the normalized vocabulary in `src/afp/classification.py`.
- `src/afp/query/filters.py`'s `FilterState` is the single source of truth for every
  combinable filter dimension; the CLI and the web API both build one and hand it to the
  same query layer.
- `src/afp/export/` handles XML format only. Domain rules about which entries are
  "recognized" versus custom live in `src/afp/custom_entries.py`.
- `packaging/launcher.py` exists because both bundlers run their entry script as
  `__main__`, which breaks the relative imports in `afp.desktop`.
