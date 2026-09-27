"""Copy for the About screen.

Kept here rather than inline in app.js for the same reason the category
labels and status names are: content lives in Python, and the web layer
serves it. Editing the screen means editing this file, with no markup to
pick through.

Page 1 started as README.md's opening paragraphs and its "What it does"
list. The two are free to diverge -- nothing checks them against each
other, and the README speaks to someone deciding whether to install the
app while this speaks to someone already looking at it.
"""

from __future__ import annotations

INTRO_PARAGRAPHS: tuple[str, ...] = (
    "This app fetches and displays the FAA's 28-Day NASR frequency data into a "
    "radio-memory XML file for the Yaesu FTA-850 (850L and 850AA), loadable through "
    "Yaesu's YCE-46 programming software.",
    "Since the nationwide NASR dataset yields far more frequencies than the radio's "
    "400-memory limit, the app is built around filtering it down. Pick states, cities, "
    "a geographic radius, frequency categories, facility types — and watch a live "
    "counter tell you when it will fit on the radio.",
)

# Phrases that render as links, and where they point. Applied to the
# intro paragraphs above and to WORKFLOW_INTRO on page 2 -- the same
# phrase should reach the same page wherever a reader meets it.
#
# Kept out of the copy so the prose stays plain -- no markup to read
# around when editing it. A phrase that stops appearing in the text fails
# a test rather than silently rendering no link.
INTRO_LINKS: tuple[tuple[str, str], ...] = (
    (
        "YCE-46 programming software",
        "https://yaesu.com/product-detail.aspx?Model=FTA-850L&CatName=Portables",
    ),
)

# (bold lead, the rest of the bullet) -- split here rather than shipping
# markdown through the API, so the screen can render the lead in bold
# without parsing anything.
FEATURES: tuple[tuple[str, str], ...] = (
    (
        "Fetches",
        "the current 28-Day NASR subscription directly from the FAA and tells you "
        "when a new cycle is published.",
    ),
    (
        "Classifies",
        "every frequency into a usable category (CTAF, Tower, Ground, Clearance, "
        "Weather, Approach/Departure, VOR, etc).",
    ),
    (
        "Filters",
        "by location, radius, frequency category, site type, and facility status, "
        "with a live entry counter against the radio's 400-memory cap.",
    ),
    (
        "Preserves your own radio entries.",
        "Import your current radio export and they will be "
        "merged back into the app's export, untouched.",
    ),
    ("Generates", "the YCE-46-compatible XML."),
)

# The one place that says how the app and YCE-46 fit together across a
# whole session. **double asterisks** mark the runs that render bold;
# the arrows are the app's usual glyph rather than "->".

WORKFLOW_INTRO = (
    "Proper workflow to use this app in conjunction with the Yaesu YCE-46 "
    "programming software:"
)

WORKFLOW_STEPS: tuple[str, ...] = (
    "Open YCE-46. **Select: Transfer → Read From Radio.**",
    "In YCE-46 Software: Go to **File → XML File → Save** to save your "
    "existing frequency list from your radio.",
    "Import the saved XML file into this app. It will import all your "
    "frequencies saved to custom groups.",
    "Filter for the frequencies you want to keep.",
    "Select **Generate FTA-850 XML.** It will include your filtered frequencies "
    "AND the custom frequency groups you imported.",
    "Go back to YCE-46 and select **File → XML File → Open.** Select "
    "the generated XML file from this app.",
    "Select **Transfer → Program to Radio** to upload the new frequencies "
    "to your radio!",
)

# Phrases in WORKFLOW_STEPS that open something in this app rather than
# describing a menu in YCE-46. The second element names an action the
# frontend knows how to run -- the copy can't hold a function, and a URL
# would be wrong for something that never leaves the page.
#
# Empty since the app began writing the group names into the export
# itself. The only action there had ever been opened the group-setup
# instructions, and there is no longer any setup to do: the file defines
# the six group names on import, so nothing has to be renamed by hand in
# YCE-46 first. The mechanism stays because the copy is likely to want
# it again.
WORKFLOW_ACTIONS: tuple[tuple[str, str], ...] = ()

AUTHOR = "Ryan Ramirez"
CONTACT_EMAIL = "ramizzan@gmail.com"
