"""Copy for the About screen.

Kept here rather than in the frontend so there is one copy of it, and so
tests/test_about.py can check it still matches README.md without having
to parse JavaScript. The web layer serves it; app.js only renders it.

These strings are README.md's opening two paragraphs and its "What it
does" list, verbatim apart from the markdown bold markers. Edit them
together -- the drift test fails otherwise.
"""

from __future__ import annotations

INTRO_PARAGRAPHS: tuple[str, ...] = (
    "Turns the FAA's 28-Day NASR aeronautical data into a radio-memory XML file for the "
    "Yaesu FTA-850 (850L and 850AA), loadable through Yaesu's YCE-46 programming software.",
    "Downloading the nationwide NASR dataset yields far more frequencies than the radio's "
    "400-memory limit, so the app is built around filtering it down: pick states, cities, a "
    "geographic radius, frequency categories, facility types — and watch a live counter tell "
    "you whether the current selection fits on the radio before you generate anything.",
)

# (bold lead, the rest of the bullet) -- the split the README's own
# markdown makes, preserved so the screen can render the lead in bold
# rather than shipping markup through the API.
FEATURES: tuple[tuple[str, str], ...] = (
    (
        "Fetches",
        "the current 28-Day NASR subscription directly from the FAA and tells you "
        "when a new cycle is published.",
    ),
    (
        "Classifies",
        "every frequency into a usable category (CTAF, Tower, Ground, Clearance, "
        'Weather, Approach/Departure, VOR, ILS, and a long tail of raw values behind an '
        '"Advanced" toggle).',
    ),
    (
        "Filters",
        "by location, radius, frequency category, site type, and facility status, "
        "with a live entry counter against the radio's 400-memory cap.",
    ),
    (
        "Preserves your own entries.",
        "Import your current radio export and any memories that "
        "aren't in the app's six generated groups are held aside and merged back into every "
        "future export, untouched.",
    ),
    ("Generates", "the YCE-46-compatible XML."),
)

AUTHOR = "Ryan Ramirez"
CONTACT_EMAIL = "ramizzan@gmail.com"
