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

# Phrases inside INTRO_PARAGRAPHS that render as links, and where they
# point. Deliberately not markup inside the paragraph: the text above has
# to stay comparable to README.md word for word, and a phrase that stops
# appearing there is a typo worth failing a test over rather than a link
# that silently goes missing.
INTRO_LINKS: tuple[tuple[str, str], ...] = (
    (
        "YCE-46 programming software",
        "https://yaesu.com/product-detail.aspx?Model=FTA-850L&CatName=Portables",
    ),
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

# ---------- page 2: the end-to-end workflow ----------
#
# Not from the README -- this is the one place that says how the app and
# YCE-46 fit together across a whole session. **double asterisks** mark
# the runs that render bold, the same convention the README's bullets
# use; the arrows are the app's usual glyph rather than "->".

WORKFLOW_INTRO = (
    "Proper workflow to use this app in conjunction with the Yaesu YCE-46 "
    "programming software:"
)

WORKFLOW_STEPS: tuple[str, ...] = (
    "Open YCE-46. Go to: **Setup → Memory Group Name.**",
    "Rename 6 of the 9 Groups. (see GROUPS SETUP HELP in this app)",
    "In YCE-46 editor: Go to **File → XML File → Save** to save your existing "
    "frequency list from your radio.",
    "Import this XML file into this app. It will import all your frequencies "
    "saved to custom groups.",
    "Filter for the frequencies you want to keep.",
    "“Generate FTA-850 XML”. It will include the NASR frequencies in their "
    "alphabetized groups AND the custom frequency groups you imported.",
    "Go back to YCE-46 and select **File → XML File → Open.** Select the "
    "generated XML file from this app.",
    "Select **Transfer → Program to Radio** to upload the new frequencies to your "
    "radio!",
)

AUTHOR = "Ryan Ramirez"
CONTACT_EMAIL = "ramizzan@gmail.com"
