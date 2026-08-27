"""Scrapes the FAA's 28-Day NASR Subscription index page to find the
currently published cycle and its CSV download links -- never hardcoded
URLs or dates (see spec §1).

The index page (https://www.faa.gov/.../NASR_Subscription/) lists cycles
in three sections -- Preview (not yet effective), Current, and Archives --
but rather than trust those section labels, we parse every "Subscription
effective <date>" link on the page (all three sections use that phrasing)
and pick whichever cycle has the latest effective date that isn't in the
future. That's self-verifying against today's date and doesn't depend on
FAA's section wording staying the same.

Every cycle -- current, preview, or archived -- has its own subpage at
NASR_Subscription/<YYYY-MM-DD> (confirmed by inspecting several), which
links to small per-category CSV zips named like
".../extra/<DD>_<Mon>_<YYYY>_APT_CSV.zip". We match on that href suffix
rather than on link text, since link text ("Airports and Other Landing
Facilities (APT)") is far more likely to change between releases than the
category-code suffix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

INDEX_URL = (
    "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/"
    "NASR_Subscription/"
)

_CYCLE_TEXT_RE = re.compile(r"Subscription effective (\w+ \d{1,2}, \d{4})")
_CATEGORY_ZIP_RE = re.compile(r"_(APT|FRQ|ILS)_CSV\.zip$", re.IGNORECASE)

REQUIRED_CATEGORIES = ("APT", "FRQ", "ILS")


@dataclass(frozen=True)
class Cycle:
    effective_date: date
    subpage_url: str


def fetch(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_index(html: str) -> list[Cycle]:
    """Extract every cycle listed on the index page, sorted oldest first.

    Preview/Current entries put "Subscription effective <date>" inside the
    <a> itself; Archives entries put it in the surrounding <li> with the
    <a> just saying "Download". <li> text covers both. The per-cycle
    subpage URL is always derived from the parsed date rather than each
    entry's own href, since Archives links point straight at a zip, not
    the subpage -- and every cycle (including archived ones) has been
    confirmed to have a subpage at that same predictable path.
    """
    soup = BeautifulSoup(html, "html.parser")
    cycles: dict[date, Cycle] = {}

    for li in soup.find_all("li"):
        match = _CYCLE_TEXT_RE.search(li.get_text(" ", strip=True))
        if not match:
            continue
        effective_date = datetime.strptime(match.group(1), "%B %d, %Y").date()
        subpage_url = urljoin(INDEX_URL, effective_date.isoformat())
        cycles[effective_date] = Cycle(effective_date, subpage_url)

    return sorted(cycles.values(), key=lambda c: c.effective_date)


def current_cycle(cycles: list[Cycle], today: date) -> Cycle:
    """The most recently effective cycle that isn't still in preview."""
    effective = [c for c in cycles if c.effective_date <= today]
    if not effective:
        raise ValueError("no published NASR cycle found on or before today")
    return max(effective, key=lambda c: c.effective_date)


def parse_cycle_csv_links(html: str) -> dict[str, str]:
    """Map category code ("APT", "FRQ", "ILS") to that category's CSV zip
    URL, from a cycle's own subpage.
    """
    soup = BeautifulSoup(html, "html.parser")
    links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        match = _CATEGORY_ZIP_RE.search(a["href"])
        if match:
            links[match.group(1).upper()] = a["href"]
    return links
