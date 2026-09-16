"""Builds the README.txt that ships beside the downloaded executable.

Generated at build time from src/afp/about.py rather than written by
hand. The two say the same things to the same reader, and a second
hand-maintained copy would drift the moment the About copy was edited --
which is exactly what happened to README.md, and why nothing checks that
file against the About screen any more.

Derived is a different arrangement from duplicated: there is still one
source of the words, and this is a rendering of it. Editing about.py
updates the shipped readme on the next build, with nothing to remember.

Adds what the About screen has no reason to carry: what to do about the
warning an unsigned download produces on first open, and where the app
keeps its data. Someone reading this file has downloaded something and
not run it yet, which the About screen's reader has already done.

Run from the repo root:
    python packaging/make_readme.py --platform windows dist/README.txt
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _appinfo  # noqa: E402

ABOUT = _appinfo.ABOUT
WIDTH = 78

# Warnings both platforms produce on an unsigned download, and the exact
# clicks past them. This is the app's single most-asked question -- the
# Windows verdict has flagged real builds -- and a plain answer sitting
# next to the file is the whole reason to ship a readme at all.
FIRST_RUN = {
    "windows": (
        "FIRST TIME YOU RUN IT (WINDOWS)",
        (
            "This app is not code-signed, so Windows does not recognise the "
            "publisher. SmartScreen may show a blue \"Windows protected your "
            "PC\" box, and Defender may occasionally flag the download "
            "outright.",
            "To run it anyway: click \"More info\", then \"Run anyway\".",
            "If Defender quarantines the file, it is a false positive on the "
            "generic launcher that every app built this way shares -- not on "
            "anything specific to this program. Restore it from Virus & "
            "threat protection > Protection history, or write to the address "
            "below and a fresh build can be sent.",
            "Windows 10 and 11 include the Edge WebView2 runtime this app "
            "draws its window with. On an older or stripped-down install it "
            "may need to be installed from Microsoft first.",
        ),
    ),
    "macos": (
        "FIRST TIME YOU RUN IT (MACOS)",
        (
            "This app is not signed with an Apple Developer ID, so Gatekeeper "
            "will refuse to open it on a plain double-click and say it is "
            "from an unidentified developer.",
            "To run it anyway: right-click (or Control-click) the app, choose "
            "Open, then click Open in the box that appears. macOS remembers "
            "the choice, so this is a one-time step.",
            "Unzip the app before opening it. Running it from inside the zip "
            "will fail.",
        ),
    ),
}

DATA_DIR = {
    "windows": r"%LOCALAPPDATA%\afp\AirportFreqProgrammer",
    "macos": "~/Library/Application Support/AirportFreqProgrammer",
}


def para(text: str) -> str:
    return textwrap.fill(text, WIDTH)


def bullet(text: str, marker: str = "- ") -> str:
    """A hanging indent, so wrapped lines line up under the first word
    rather than under the marker."""
    return textwrap.fill(
        text, WIDTH, initial_indent=marker, subsequent_indent=" " * len(marker)
    )


def heading(text: str) -> str:
    return f"{text}\n{'-' * len(text)}"


def strip_bold(text: str) -> str:
    """The workflow steps carry ** markers for the screen's renderer.
    Plain text has no bold, and leaving them in reads as noise."""
    return text.replace("**", "")


# The app's copy uses real typography -- arrows for menu paths, em
# dashes, curly quotes. The screen renders those from a font it ships; a
# .txt is opened by whatever the reader happens to have, on a machine
# whose encoding defaults are not knowable from here. Folded to ASCII so
# the file cannot arrive as mojibake, and ">" is the ordinary way to
# write a menu path in plain text anyway.
ASCII_FOLD = {
    "→": ">",
    "—": "-",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",
}


def ascii_fold(text: str) -> str:
    for source, plain in ASCII_FOLD.items():
        text = text.replace(source, plain)
    return text


def build(platform: str) -> str:
    const = lambda name: _appinfo.read_constant(ABOUT, name)  # noqa: E731

    version = _appinfo.package_version()
    release = _appinfo.read_constant(_appinfo.PACKAGE_INIT, "RELEASE_DATE")

    out: list[str] = [
        _appinfo.APP_DISPLAY_NAME,
        "=" * len(_appinfo.APP_DISPLAY_NAME),
        "",
        f"Version {version}  -  released {release}",
        f"Created by {_appinfo.author()}",
        "",
    ]

    # Page 1 of the About screen, in its two halves.
    out.append(heading("WHAT IT DOES"))
    out.append("")
    for text in const("INTRO_PARAGRAPHS"):
        out.append(para(text))
        out.append("")

    out.append(heading("HOW IT DOES IT"))
    out.append("")
    for lead, rest in const("FEATURES"):
        out.append(bullet(f"{lead} {rest}"))
    out.append("")

    # Page 2.
    out.append(heading("WORKFLOW"))
    out.append("")
    out.append(para(const("WORKFLOW_INTRO")))
    out.append("")
    for i, step in enumerate(const("WORKFLOW_STEPS"), start=1):
        out.append(bullet(strip_bold(step), marker=f"{i:>2}. "))
    out.append("")

    # Not on the About screen: nothing here is about using the app.
    title, paragraphs = FIRST_RUN[platform]
    out.append(heading(title))
    out.append("")
    for text in paragraphs:
        out.append(para(text))
        out.append("")

    out.append(heading("WHERE YOUR DATA IS KEPT"))
    out.append("")
    out.append(
        para(
            "Downloaded FAA cycles and your imported entries live outside the "
            "app, so replacing it with a newer version keeps them:"
        )
    )
    out.append("")
    out.append(f"    {DATA_DIR[platform]}")
    out.append("")

    out.append(heading("QUESTIONS, COMMENTS OR SUGGESTIONS"))
    out.append("")
    out.append(para(const("CONTACT_EMAIL")))
    out.append("")

    # Plain text cannot hyperlink, so the URLs the screen hides behind a
    # phrase have to be spelled out.
    out.append(heading("LINKS"))
    out.append("")
    for phrase, url in const("INTRO_LINKS"):
        out.append(bullet(f"{phrase}:"))
        out.append(f"  {url}")
    out.append("")

    rendered = ascii_fold("\n".join(out).rstrip() + "\n")

    # A character with no fold rule would otherwise ship as whatever the
    # reader's editor guesses it is. Better to break the build than to
    # hear about it from someone already holding the file.
    unmapped = sorted({c for c in rendered if ord(c) > 127})
    if unmapped:
        raise SystemExit(
            "non-ASCII characters with no fold rule: "
            + ", ".join(f"{c!r} (U+{ord(c):04X})" for c in unmapped)
        )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--platform", required=True, choices=sorted(FIRST_RUN))
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # CRLF and no BOM: this is opened in Notepad as often as anywhere,
    # and written from a Linux or macOS runner either way.
    args.output.write_text(build(args.platform), encoding="utf-8", newline="\r\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
