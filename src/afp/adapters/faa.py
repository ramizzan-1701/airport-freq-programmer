"""FAA NASR adapter.

Parses the three NASR CSV extracts (APT_BASE, FRQ, ILS_BASE) into the
normalized schema. This is the only module in the codebase permitted to
know about FAA-specific column names or quirks.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import IO

from ..schema import Airport, Frequency, Ils, NormalizedData
from .base import SourceAdapter
from .faa_classification import (
    classify_facility_status,
    classify_freq_category,
    classify_platform_type,
    classify_weather_subtype,
)

CsvSource = str | Path | IO[str]

# NASR encodes "public use" as FACILITY_USE_CODE == "PU"; anything else
# (PR = private, AR = restricted, etc.) is not public-use.
PUBLIC_USE_CODE = "PU"


def _rows(source: CsvSource) -> list[dict[str, str]]:
    """Return dict rows for a CSV source, accepting a path or an open stream.

    NASR CSVs are sometimes distributed with a UTF-8 BOM; utf-8-sig strips
    it transparently whether present or not.
    """
    if hasattr(source, "read"):
        return list(csv.DictReader(source))
    with open(source, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return float(value)


_FREQ_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<rest>.*)$")


def _split_freq(raw: str) -> tuple[float, str | None]:
    """Split a FREQ cell into (freq_mhz, remark).

    The FAA overloads this column well beyond plain "122.900": a DME
    channel after a slash ("117.3/120X"), a trailing single-letter code
    with no slash ("122.1R" -- receive-only RCO frequencies), and even
    long-range HF frequencies for oceanic FSS service tagged with a
    trailing family name ("8903X", "8903 CARIBBEAN FAMILY A"). Rather than
    special-case each, take the leading numeric run as the frequency and
    preserve whatever follows verbatim as a remark, so nothing is silently
    dropped or misparsed.
    """
    raw = raw.strip()
    match = _FREQ_RE.match(raw)
    if not match:
        raise ValueError(f"unparseable FREQ value: {raw!r}")

    freq_mhz = float(match.group("value"))
    rest = match.group("rest").strip()
    if not rest:
        return freq_mhz, None
    if rest.startswith("/"):
        return freq_mhz, f"DME:{rest[1:].strip()}"
    return freq_mhz, rest


class FAAAdapter(SourceAdapter):
    def __init__(
        self,
        apt_base: CsvSource,
        frq: CsvSource,
        ils_base: CsvSource,
    ) -> None:
        self._apt_base = apt_base
        self._frq = frq
        self._ils_base = ils_base

    def parse(self) -> NormalizedData:
        return NormalizedData(
            airports=self._parse_airports(),
            frequencies=self._parse_frequencies(),
            ils=self._parse_ils(),
        )

    def _parse_airports(self) -> list[Airport]:
        airports = []
        for row in _rows(self._apt_base):
            lat = _float_or_none(row.get("LAT_DECIMAL"))
            lon = _float_or_none(row.get("LONG_DECIMAL"))
            if lat is None or lon is None:
                continue
            airports.append(
                Airport(
                    id=row["ARPT_ID"].strip(),
                    name=row.get("ARPT_NAME", "").strip(),
                    city=row.get("CITY", "").strip(),
                    state=row.get("STATE_CODE", "").strip(),
                    lat=lat,
                    lon=lon,
                    public_use=row.get("FACILITY_USE_CODE", "").strip()
                    == PUBLIC_USE_CODE,
                )
            )
        return airports

    def _parse_frequencies(self) -> list[Frequency]:
        frequencies = []
        for row in _rows(self._frq):
            raw_freq = row.get("FREQ", "").strip()
            if not raw_freq:
                continue
            freq_mhz, freq_remark = _split_freq(raw_freq)
            raw_freq_use = row.get("FREQ_USE", "").strip()
            raw_facility_type = row.get("FACILITY_TYPE", "").strip()
            raw_serviced_site_type = row.get("SERVICED_SITE_TYPE", "").strip()
            frequencies.append(
                Frequency(
                    airport_id=row["SERVICED_FACILITY"].strip(),
                    freq_mhz=freq_mhz,
                    freq_category=classify_freq_category(
                        raw_freq_use, raw_facility_type, raw_serviced_site_type
                    ),
                    platform_type=classify_platform_type(raw_serviced_site_type),
                    facility_status=classify_facility_status(raw_facility_type),
                    weather_subtype=classify_weather_subtype(raw_freq_use, raw_serviced_site_type),
                    primary_approach_radio_call=row.get(
                        "PRIMARY_APPROACH_RADIO_CALL", ""
                    ).strip()
                    or None,
                    tower_hours=row.get("TOWER_HRS", "").strip() or None,
                    raw_freq_use=raw_freq_use,
                    source_remark=freq_remark,
                    lat=_float_or_none(row.get("LAT_DECIMAL")),
                    lon=_float_or_none(row.get("LONG_DECIMAL")),
                    state=row.get("SERVICED_STATE", "").strip() or None,
                    city=row.get("SERVICED_CITY", "").strip() or None,
                    name=row.get("SERVICED_FAC_NAME", "").strip() or None,
                )
            )
        return frequencies

    def _parse_ils(self) -> list[Ils]:
        ils_list = []
        for row in _rows(self._ils_base):
            freq_mhz = _float_or_none(row.get("LOC_FREQ"))
            if freq_mhz is None:
                continue
            ils_list.append(
                Ils(
                    airport_id=row["ARPT_ID"].strip(),
                    runway_end_id=row.get("RWY_END_ID", "").strip(),
                    freq_mhz=freq_mhz,
                    system_type=row.get("SYSTEM_TYPE_CODE", "").strip(),
                    component_status=row.get("COMPONENT_STATUS", "").strip(),
                )
            )
        return ils_list
