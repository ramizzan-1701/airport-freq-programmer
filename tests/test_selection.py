from afp.export.fta850l import FTA_850L
from afp.export.xml_writer import build_xml, validate
from afp.schema import Airport, Frequency, Ils, NormalizedData
from afp.selection import (
    MAX_TUNABLE_MHZ,
    _abbreviate_facility_id,
    default_group_for,
    orphan_tag_ids,
    select_entries,
)


# ---------- what the radio can actually tune ----------


def test_frequencies_above_the_airband_never_become_entries():
    """NASR lists the military UHF assignments in the same table as the
    VHF ones -- 12,354 of 40,388 rows in the 2026-09-03 cycle, 31% of the
    file. The radio has no receiver up there, so every one of those was a
    memory slot spent on a frequency nobody could select.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 121.4, "GROUND", raw_freq_use="GND/P"),
            _freq("XXX", 251.05, "GROUND", raw_freq_use="GND/P"),
            _freq("XXX", 21964.0, "OTHER"),
        ],
        ils=[],
    )
    for mode in ("smart", "raw"):
        freqs = {e.freq_mhz for e in select_entries(data, mode=mode)}
        assert freqs == {121.4}, mode


def test_the_band_limit_applies_to_raw_mode_too():
    """Raw means every registered frequency, not every row in the file.
    Which of two frequencies to publish is an interpretation choice;
    whether the radio can tune one at all is not.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[_freq("XXX", 243.0, "EMERGENCY")],
        ils=[],
    )
    assert select_entries(data, mode="raw") == []


def test_the_top_of_the_airband_is_kept():
    """136.975 is the last usable channel -- an off-by-one here would
    silently drop the top of the band.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[_freq("XXX", 136.975, "APCH_DEP", raw_freq_use="APCH/S")],
        ils=[],
    )
    assert [e.freq_mhz for e in select_entries(data, mode="smart")] == [136.975]
    assert 136.975 < MAX_TUNABLE_MHZ


def test_nav_band_frequencies_below_the_airband_are_kept():
    """The limit is one-sided on purpose: VOR and ILS localizers sit
    below the airband (108.0-117.95) and the radio tunes them.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[_freq("XXX", 114.9, "VOR", raw_freq_use="BIG VORTAC")],
        ils=[],
    )
    assert [e.freq_mhz for e in select_entries(data, mode="smart")] == [114.9]


def test_a_facility_with_nothing_tunable_produces_no_entries():
    """Rather than an airport that survives into the results with an
    empty frequency list behind it.
    """
    data = NormalizedData(
        airports=[_airport("UHF")],
        frequencies=[
            _freq("UHF", 257.8, "TOWER", raw_freq_use="LCL/P"),
            _freq("UHF", 322.5, "RCAG"),
        ],
        ils=[],
    )
    assert select_entries(data, mode="smart") == []


def test_a_uhf_tower_row_cannot_take_the_primary_comm_slot():
    """658 TOWER rows in the cycle are UHF. Before the band limit one of
    those could be the first TOWER row for an airport and win the slot,
    pushing the VHF tower out of the export entirely.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 257.8, "TOWER", raw_freq_use="LCL/P"),
            _freq("XXX", 120.2, "TOWER", raw_freq_use="LCL/P"),
        ],
        ils=[],
    )
    assert [e.freq_mhz for e in select_entries(data, mode="smart")] == [120.2]


def _airport(airport_id: str, public_use: bool = True) -> Airport:
    return Airport(id=airport_id, name=airport_id, city="X", state="CA", lat=1.0, lon=-1.0, public_use=public_use)


def _freq(airport_id: str, freq_mhz: float, freq_category: str, raw_freq_use: str = "", weather_subtype: str | None = None) -> Frequency:
    return Frequency(
        airport_id=airport_id, freq_mhz=freq_mhz, freq_category=freq_category,
        raw_freq_use=raw_freq_use, weather_subtype=weather_subtype,
    )


def test_smart_mode_produces_expected_tags(faa_adapter):
    data = faa_adapter.parse()
    entries = select_entries(data, mode="smart")
    tags = {e.tag_name for e in entries}

    assert tags == {
        "SNS-ATIS", "SNS-CT", "SNS-VOR", "SNS-LOC31", "WVI-CTAF", "WVI-AWOS",
        "PXN-VOR",  # standalone VORTAC, no matching airport row
    }


def test_smart_mode_excludes_private_use_airport_by_default(faa_adapter):
    data = faa_adapter.parse()
    entries = select_entries(data, mode="smart")
    assert all(not e.tag_name.startswith("PVT1") for e in entries)


def test_public_private_are_independently_toggleable_filters():
    data = NormalizedData(
        airports=[_airport("PUB1", public_use=True), _airport("PVT1", public_use=False)],
        frequencies=[
            _freq("PUB1", 122.8, "CTAF"),
            _freq("PVT1", 122.9, "CTAF"),
        ],
        ils=[],
    )

    # default: public only (preserves the app's traditional scope)
    default_tags = {e.tag_name for e in select_entries(data)}
    assert default_tags == {"PUB1-CTAF"}

    # private only
    private_only_tags = {
        e.tag_name
        for e in select_entries(data, include_public=False, include_private=True)
    }
    assert private_only_tags == {"PVT1-CTAF"}

    # both
    both_tags = {
        e.tag_name
        for e in select_entries(data, include_public=True, include_private=True)
    }
    assert both_tags == {"PUB1-CTAF", "PVT1-CTAF"}

    # neither
    assert select_entries(data, include_public=False, include_private=False) == []


def test_standalone_facility_always_passes_through_public_private():
    """A frequency whose airport_id matches no Airport at all (a
    standalone VOR/RCAG/ARTCC/...) has no public/private concept -- it
    must appear regardless of include_public/include_private, including
    the "neither" combination that would normally yield nothing.
    """
    data = NormalizedData(
        airports=[_airport("PUB1", public_use=True)],
        frequencies=[
            _freq("PUB1", 122.8, "CTAF"),
            Frequency(
                airport_id="AVE", freq_mhz=117.1, freq_category="VOR",
                raw_freq_use="AVE VOR/DME", state="CA", city="AVENAL", name="AVENAL",
                lat=35.647, lon=-119.979,
            ),
        ],
        ils=[],
    )

    default = {e.tag_name for e in select_entries(data)}
    assert default == {"PUB1-CTAF", "AVE-VOR"}

    neither = {e.tag_name for e in select_entries(data, include_public=False, include_private=False)}
    assert neither == {"AVE-VOR"}  # PUB1 drops out, AVE-VOR still shows


def test_standalone_facility_uses_its_own_position_and_group():
    data = NormalizedData(
        airports=[],
        frequencies=[
            Frequency(
                airport_id="AVE", freq_mhz=117.1, freq_category="VOR",
                raw_freq_use="AVE VOR/DME", state="CA", city="AVENAL", name="AVENAL",
                lat=35.647, lon=-119.979,
            ),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)

    assert len(entries) == 1
    assert entries[0].tag_name == "AVE-VOR"
    assert entries[0].lat == 35.647
    assert entries[0].lon == -119.979
    assert entries[0].group == default_group_for("AVE")


def test_tower_beats_ctaf_in_smart_mode_on_a_shared_frequency():
    """The case the rule is actually for: a towered field whose CTAF is
    the tower frequency, listed twice. One channel, so one entry.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 118.0, "TOWER", raw_freq_use="LCL/P"),
            _freq("XXX", 118.0, "CTAF"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    comm_entries = [e for e in entries if e.tag_name in ("XXX-CT", "XXX-CTAF")]
    assert len(comm_entries) == 1
    assert comm_entries[0].tag_name == "XXX-CT"
    assert comm_entries[0].freq_mhz == 118.0


def test_a_ctaf_on_its_own_frequency_survives_smart_mode():
    """Smart mode suppressed CTAF whenever a tower existed at all, which
    threw away a real, separately-tuned channel: AKN tower 118.3 against
    CTAF 121.9, BIG 119.8 against 122.9 -- 18 airports in the 2026-09-03
    cycle. Only a shared frequency is a duplicate.
    """
    data = NormalizedData(
        airports=[_airport("AKN")],
        frequencies=[
            _freq("AKN", 118.3, "TOWER", raw_freq_use="LCL/P"),
            _freq("AKN", 121.9, "CTAF"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    assert {e.freq_mhz for e in entries} == {118.3, 121.9}


def test_the_primary_tower_channel_wins_not_whichever_is_listed_first():
    """NASR lists a tower's primary (LCL/P) and secondary (LCL/S) as
    separate rows in no guaranteed order, and smart mode took the first.
    At HWD that published the secondary on 118.9 and dropped the primary
    on 120.2 -- which also dropped the CTAF that shared it.

    Both channels are kept (they are two real frequencies), but the
    primary takes the unsuffixed tag.
    """
    data = NormalizedData(
        airports=[_airport("HWD")],
        frequencies=[
            _freq("HWD", 118.9, "TOWER", raw_freq_use="LCL/S"),
            _freq("HWD", 120.2, "CTAF"),
            _freq("HWD", 120.2, "TOWER", raw_freq_use="LCL/P"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    by_tag = {e.tag_name: e.freq_mhz for e in entries}

    assert by_tag == {"HWD-CT": 120.2, "HWD-CT2": 118.9}
    # The CTAF shares the primary's frequency, so it is still suppressed.
    assert "HWD-CTAF" not in by_tag


def test_two_tower_channels_are_not_collapsed_into_one():
    data = NormalizedData(
        airports=[_airport("NGP")],
        frequencies=[
            _freq("NGP", 125.525, "TOWER", raw_freq_use="LCL/P"),
            _freq("NGP", 134.85, "TOWER", raw_freq_use="LCL/P"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    assert {e.freq_mhz for e in entries} == {125.525, 134.85}


def test_a_tower_frequency_listed_twice_yields_one_entry():
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 118.0, "TOWER", raw_freq_use="LCL/P"),
            _freq("XXX", 118.0, "TOWER", raw_freq_use="LCL/P IC"),
        ],
        ils=[],
    )
    assert len(select_entries(data, mode="smart")) == 1


def test_unicom_dropped_in_smart_mode_when_it_matches_ctaf():
    """Real NASR data: 87% of airports with both a CTAF and a UNICOM row
    (2,502 of 2,877, 2026-08-06 cycle) carry the identical frequency.
    """
    data = NormalizedData(
        airports=[_airport("0Q9")],
        frequencies=[
            _freq("0Q9", 122.8, "CTAF"),
            _freq("0Q9", 122.8, "UNICOM"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    tags = {e.tag_name for e in entries}
    assert tags == {"0Q9-CTAF"}


def test_unicom_kept_in_smart_mode_when_it_differs_from_ctaf():
    data = NormalizedData(
        airports=[_airport("ZZZ")],
        frequencies=[
            _freq("ZZZ", 122.9, "CTAF"),
            _freq("ZZZ", 122.8, "UNICOM"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    tags = {e.tag_name for e in entries}
    assert tags == {"ZZZ-CTAF", "ZZZ-UNICOM"}
    unicom = next(e for e in entries if e.tag_name == "ZZZ-UNICOM")
    assert unicom.freq_mhz == 122.8


def test_unicom_kept_in_smart_mode_when_there_is_no_ctaf_at_all():
    data = NormalizedData(
        airports=[_airport("TWR")],
        frequencies=[
            _freq("TWR", 118.0, "TOWER", raw_freq_use="LCL/P"),
            _freq("TWR", 122.95, "UNICOM"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    tags = {e.tag_name for e in entries}
    assert tags == {"TWR-CT", "TWR-UNICOM"}


def test_unicom_kept_in_raw_mode_even_when_it_matches_ctaf():
    """Raw mode is documented as literal, no priority collapsing -- the
    CTAF/UNICOM consolidation is a smart-mode-only heuristic.
    """
    data = NormalizedData(
        airports=[_airport("0Q9")],
        frequencies=[
            _freq("0Q9", 122.8, "CTAF"),
            _freq("0Q9", 122.8, "UNICOM"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="raw")
    tags = {e.tag_name for e in entries}
    assert tags == {"0Q9-CTAF", "0Q9-UNICOM"}


def test_atis_beats_asos_beats_awos_in_smart_mode():
    data = NormalizedData(
        airports=[_airport("YYY")],
        frequencies=[
            _freq("YYY", 135.0, "WEATHER_STATION", raw_freq_use="YYY AWOS-3", weather_subtype="AWOS"),
            _freq("YYY", 135.5, "WEATHER_STATION", raw_freq_use="YYY ASOS", weather_subtype="ASOS"),
            _freq("YYY", 126.0, "WEATHER_STATION", raw_freq_use="ATIS", weather_subtype="ATIS"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    weather = [e for e in entries if e.tag_name == "YYY-ATIS"]
    assert len(weather) == 1
    assert weather[0].freq_mhz == 126.0
    assert not any(e.tag_name in ("YYY-ASOS", "YYY-AWOS") for e in entries)


def test_weather_tag_suffix_comes_from_subtype_not_raw_text():
    """Tag naming for Weather Station entries is driven by the normalized
    weather_subtype field (set by the adapter's classifier), not by
    sanitizing the raw FREQ_USE text -- which is a mess in real data
    ("CVH AWOS-3", "0J4 ASOS", ...).
    """
    data = NormalizedData(
        airports=[_airport("CVH")],
        frequencies=[_freq("CVH", 120.425, "WEATHER_STATION", raw_freq_use="CVH AWOS-3", weather_subtype="AWOS")],
        ils=[],
    )
    entries = select_entries(data, mode="smart")

    assert len(entries) == 1
    assert entries[0].tag_name == "CVH-AWOS"
    assert entries[0].freq_mhz == 120.425


def test_raw_mode_keeps_every_row_literally():
    data = NormalizedData(
        airports=[_airport("YYY")],
        frequencies=[
            _freq("YYY", 135.0, "WEATHER_STATION", raw_freq_use="YYY AWOS-3", weather_subtype="AWOS"),
            _freq("YYY", 126.0, "WEATHER_STATION", raw_freq_use="ATIS", weather_subtype="ATIS"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="raw")
    tags = {e.tag_name for e in entries}
    assert tags == {"YYY-AWOS", "YYY-ATIS"}


def test_one_loc_entry_per_runway_end_in_smart_mode_prefers_operational_over_restricted():
    data = NormalizedData(
        airports=[_airport("SNS")],
        frequencies=[],
        ils=[
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=108.5, system_type="LC", component_status="OPERATIONAL RESTRICTED"),
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=109.9, system_type="LC", component_status="OPERATIONAL IFR"),
        ],
    )
    entries = select_entries(data, mode="smart")
    assert len(entries) == 1
    assert entries[0].tag_name == "SNS-LOC31"
    assert entries[0].freq_mhz == 109.9


def test_one_loc_entry_per_runway_end_in_smart_mode_prefers_richer_system_type():
    """Real FAA SYSTEM_TYPE_CODE values (from the NASR "ILS Data Layout"
    doc, see afp.classification.ILS_SYSTEM_TYPE_LABELS) -- "LS" (full ILS)
    should win over "LC" (localizer-only) when component_status ties.
    """
    data = NormalizedData(
        airports=[_airport("SNS")],
        frequencies=[],
        ils=[
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=108.5, system_type="LC", component_status="OPERATIONAL IFR"),
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=109.9, system_type="LS", component_status="OPERATIONAL IFR"),
        ],
    )
    entries = select_entries(data, mode="smart")
    assert len(entries) == 1
    assert entries[0].tag_name == "SNS-LOC31"
    assert entries[0].freq_mhz == 109.9


def test_raw_mode_keeps_all_ils_rows_per_runway_end():
    data = NormalizedData(
        airports=[_airport("SNS")],
        frequencies=[],
        ils=[
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=108.5, system_type="LC", component_status="OPERATIONAL RESTRICTED"),
            Ils(airport_id="SNS", runway_end_id="31", freq_mhz=109.9, system_type="LS", component_status="OPERATIONAL IFR"),
        ],
    )
    entries = select_entries(data, mode="raw")
    assert len(entries) == 2
    assert all(e.tag_name == "SNS-LOC31" for e in entries)


def test_airspace_info_never_produces_an_entry():
    """CLASS B/C/TRSA rows classify as AIRSPACE_INFO -- spec: 'informational
    metadata, not a selectable filter'. They must never become a standalone
    entry, in either mode, even when nothing else competes for their slot.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[_freq("SFO", 120.9, "AIRSPACE_INFO", raw_freq_use="CLASS B")],
        ils=[],
    )
    assert select_entries(data, mode="smart") == []
    assert select_entries(data, mode="raw") == []


def test_multiple_distinct_frequencies_sharing_a_category_get_numeric_suffix():
    """Real NASR data: a TRACON can have several genuinely distinct
    APCH/S sector frequencies at one airport (e.g. Hayward/HWD), which
    would otherwise all sanitize to the same tag name and collide.
    """
    data = NormalizedData(
        airports=[_airport("HWD")],
        frequencies=[
            _freq("HWD", 125.35, "APCH_DEP", raw_freq_use="APCH/S"),
            _freq("HWD", 134.50, "APCH_DEP", raw_freq_use="APCH/S"),
            # Was 338.20 -- HWD really does carry that sector, but it is
            # UHF and no longer produces an entry at all.
            _freq("HWD", 135.65, "APCH_DEP", raw_freq_use="APCH/S"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    tags = [e.tag_name for e in entries]

    assert tags == ["HWD-APCH", "HWD-APCH2", "HWD-APCH3"]
    assert len(set(tags)) == 3  # no collisions
    freqs_by_tag = {e.tag_name: e.freq_mhz for e in entries}
    assert freqs_by_tag == {"HWD-APCH": 125.35, "HWD-APCH2": 134.50, "HWD-APCH3": 135.65}


def test_collapsing_is_scoped_within_a_category_not_globally_by_frequency():
    """Real NASR data: busy Class B/C airports (e.g. SFO) list several
    named SID/STAR procedures sharing one physical frequency alongside a
    real Approach/Departure row and an airspace-class annotation. Under
    the category system each classifies differently -- CLASS B is
    non-selectable metadata (produces nothing), DEP/P is its own
    Approach/Departure entry, and the two DP-suffixed names collapse
    into a single Procedure-fix entry -- rather than all four being
    crushed into one entry by raw frequency collapsing alone.

    The procedure-fix entry is tagged by procedure type ("DP") rather
    than by name; see _procedure_fix_suffix for why the name is dropped.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[
            _freq("SFO", 120.9, "PROCEDURE_FIX", raw_freq_use="SAN FRANCISCO DP"),
            _freq("SFO", 120.9, "AIRSPACE_INFO", raw_freq_use="CLASS B"),
            _freq("SFO", 120.9, "APCH_DEP", raw_freq_use="DEP/P"),
            _freq("SFO", 120.9, "PROCEDURE_FIX", raw_freq_use="SHORELINE DP"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    tags = {e.tag_name for e in entries}

    # Still two entries, not one: the point of this test. Both DP rows
    # collapse together, separately from the Approach/Departure row that
    # shares their frequency.
    assert tags == {"SFO-DEP", "SFO-DP"}
    assert all(e.freq_mhz == 120.9 for e in entries)


def test_different_frequencies_in_same_category_still_get_separate_entries():
    """Collapsing is keyed on (category, frequency) -- genuinely distinct
    channels in the same category must still each get their own entry.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[
            _freq("SFO", 133.95, "APCH_DEP", raw_freq_use="APCH/S"),
            # Was 251.05, which is UHF and now filtered out before
            # selection ever sees it.
            _freq("SFO", 135.40, "APCH_DEP", raw_freq_use="APCH/S"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    freqs = {e.freq_mhz for e in entries}

    assert freqs == {133.95, 135.40}
    assert len({e.tag_name for e in entries}) == 2  # disambiguated, no collision


def test_fixed_suffix_categories_ignore_raw_text_variation():
    """VOR/VORTAC/VOR-DME all classify as the VOR category and share the
    same clean fixed tag suffix, regardless of which raw value produced
    them -- unlike APCH_DEP/PROCEDURE_FIX/OTHER, raw text doesn't leak
    into the tag for these.
    """
    data = NormalizedData(
        airports=[_airport("SNS")],
        frequencies=[_freq("SNS", 115.7, "VOR", raw_freq_use="VORTAC")],
        ils=[],
    )
    entries = select_entries(data, mode="smart")
    assert entries[0].tag_name == "SNS-VOR"


def test_numeric_suffix_disambiguation_also_applies_in_raw_mode():
    data = NormalizedData(
        airports=[_airport("HWD")],
        frequencies=[
            _freq("HWD", 125.35, "APCH_DEP", raw_freq_use="APCH/S"),
            _freq("HWD", 134.50, "APCH_DEP", raw_freq_use="APCH/S"),
        ],
        ils=[],
    )
    entries = select_entries(data, mode="raw")
    tags = {e.tag_name for e in entries}

    assert tags == {"HWD-APCH", "HWD-APCH2"}


def test_default_group_buckets_match_ca_scheme():
    assert default_group_for("9AB") == "0-9"
    assert default_group_for("ABC") == "A-E"
    assert default_group_for("FOO") == "F-J"
    assert default_group_for("KLM") == "K-O"
    assert default_group_for("SNS") == "P-T"
    assert default_group_for("WVI") == "U-Z"


# ---------- orphan facility ID abbreviation (long SERVICED_FACILITY names) ----------


def test_abbreviate_facility_id_leaves_short_ids_unchanged():
    """Real short relay/center codes (the vast majority nationwide) and
    short single-word place names must pass through unmodified.
    """
    assert _abbreviate_facility_id("AVE") == "AVE"
    assert _abbreviate_facility_id("PXN") == "PXN"
    assert _abbreviate_facility_id("AKRON") == "AKRON"


def test_abbreviate_facility_id_shortens_long_place_names():
    """Real WY data that triggered the original bug: SERVICED_FACILITY
    values that are full multi-word place names, not short codes.
    """
    assert _abbreviate_facility_id("MEDICINE BOW") == "MEDICB"
    assert _abbreviate_facility_id("FORT BRIDGER") == "FORTB"
    assert _abbreviate_facility_id("ROCK SPRINGS 2") == "ROCKS2"
    assert _abbreviate_facility_id("FREDERICKSBURG") == "FREDER"  # single long word: plain truncation
    for name in ("MEDICINE BOW", "FORT BRIDGER", "ROCK SPRINGS 2", "FREDERICKSBURG"):
        assert len(_abbreviate_facility_id(name)) <= 6


def test_orphan_tag_ids_disambiguates_facilities_that_abbreviate_the_same():
    """"ALPHA BETA" and "ALPHA BRAVO" both abbreviate to "ALPHAB" -- the
    second must get a distinguishing suffix rather than silently
    colliding with the first (which would surface only much later, as an
    opaque "duplicate tag name" export failure).
    """
    mapping = orphan_tag_ids(set(), {"ALPHA BETA", "ALPHA BRAVO"})
    assert len(set(mapping.values())) == 2
    assert len(mapping) == 2


def test_orphan_tag_ids_never_collides_with_a_real_airport_id():
    """Regression: confirmed on real nationwide 2026-08-06 data -- the
    standalone RCAG relay facility "EL DORADO" abbreviates to "ELD",
    identical to the real, unrelated Eldorado Regional airport's own LID.
    Before airport_ids was seeded into the collision-avoidance set, this
    produced duplicate tag names (two unrelated facilities both emitting
    "ELD-RCAG") that only surfaced as an opaque export-time failure.
    """
    mapping = orphan_tag_ids({"ELD"}, {"ELD", "EL DORADO"})
    assert mapping == {"EL DORADO": "ELD2"}


def test_orphan_facility_with_long_place_name_produces_valid_length_tags():
    """Regression test for the reported bug: generating an export for a
    dataset containing a standalone facility (RCAG relay) whose
    SERVICED_FACILITY is a full place name ("MEDICINE BOW") used to
    produce tags like "MEDICINE BOW-RCAG2" (18 chars), exceeding the
    FTA-850L's 14-char cap and failing the whole export.
    """
    data = NormalizedData(
        airports=[],
        frequencies=[
            Frequency(
                airport_id="MEDICINE BOW", freq_mhz=122.5, freq_category="RCAG",
                raw_freq_use="MEDICINE BOW RCAG", state="WY", city="MEDICINE BOW",
                name="MEDICINE BOW", lat=41.9, lon=-106.2,
            ),
            Frequency(
                airport_id="MEDICINE BOW", freq_mhz=133.5, freq_category="RCAG",
                raw_freq_use="MEDICINE BOW RCAG", state="WY", city="MEDICINE BOW",
                name="MEDICINE BOW", lat=41.9, lon=-106.2,
            ),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    tags = {e.tag_name for e in entries}

    assert tags == {"MEDICB-RCAG", "MEDICB-RCAG2"}
    assert all(len(t) <= 14 for t in tags)
    assert validate(entries, FTA_850L) == []
    build_xml(entries, FTA_850L)  # should not raise


def test_orphan_facility_short_id_tag_unaffected_by_abbreviation_scheme():
    data = NormalizedData(
        airports=[],
        frequencies=[_freq("AVE", 117.1, "VOR")],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert entries[0].tag_name == "AVE-VOR"


# ---------- procedure-fix tag suffixes ----------


def test_procedure_fix_tags_use_the_procedure_type_not_its_name():
    """Real bug: CYXX (Abbotsford) publishes four STAR procedure fixes,
    which produced tags like CYXX-MADEERNAVSTAR -- 18 chars against the
    FTA-850L's 14-char cap, failing the whole export rather than just
    that entry.
    """
    data = NormalizedData(
        airports=[_airport("CYXX")],
        frequencies=[
            _freq("CYXX", 118.2, "PROCEDURE_FIX", raw_freq_use="MADEER RNAV STAR"),
            _freq("CYXX", 120.7, "PROCEDURE_FIX", raw_freq_use="DNKIN RNAV STAR"),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    tags = {e.tag_name for e in entries}

    assert tags == {"CYXX-STAR", "CYXX-STAR2"}
    assert all(len(t) <= FTA_850L.max_tag_length for t in tags)
    assert validate(entries, FTA_850L) == []
    build_xml(entries, FTA_850L)  # must not raise


def test_departure_procedures_are_tagged_dp_not_star():
    """Half of PROCEDURE_FIX rows nationwide are departures (1003 DP vs
    1128 STAR, 2026-09-03 cycle) -- tagging those "STAR" would label an
    arrival procedure on a departure frequency.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[
            _freq("SFO", 120.9, "PROCEDURE_FIX", raw_freq_use="SHORELINE DP"),
            _freq("SFO", 121.1, "PROCEDURE_FIX", raw_freq_use="WYLSN RNAV DP"),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert {e.tag_name for e in entries} == {"SFO-DP", "SFO-DP2"}


def test_unrecognised_procedure_text_falls_back_to_raw_rather_than_guessing():
    """A future cycle introducing a third procedure type must not be
    silently mislabelled as an arrival or a departure.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[_freq("SFO", 120.9, "PROCEDURE_FIX", raw_freq_use="SOMETHING NEW")],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert entries[0].tag_name == "SFO-SOMETHINGNEW"


# ---------- approach/departure and remark tag suffixes ----------


def test_apch_dep_suffix_drops_primary_secondary_and_ic_markers():
    """"APCH/P DEP/P IC" made 16-char tags. The /P, /S and IC markers
    aren't worth that length; approach-vs-departure is.
    """
    data = NormalizedData(
        airports=[_airport("HWD")],
        frequencies=[
            _freq("HWD", 125.35, "APCH_DEP", raw_freq_use="APCH/P DEP/P IC"),
            _freq("HWD", 134.50, "APCH_DEP", raw_freq_use="APCH/S DEP/S"),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert {e.tag_name for e in entries} == {"HWD-APCHDEP", "HWD-APCHDEP2"}
    assert validate(entries, FTA_850L) == []


def test_approach_only_and_departure_only_keep_that_distinction():
    """532 rows nationwide are approach-only and 381 departure-only, so
    labelling everything APCHDEP would tag a departure-only frequency as
    an approach and vice versa.
    """
    data = NormalizedData(
        airports=[_airport("SFO")],
        frequencies=[
            _freq("SFO", 120.5, "APCH_DEP", raw_freq_use="APCH/P"),
            _freq("SFO", 121.5, "APCH_DEP", raw_freq_use="DEP/S"),
            _freq("SFO", 122.5, "APCH_DEP", raw_freq_use="APCH/P DEP/P"),
        ],
        ils=[],
    )
    tags = {e.tag_name for e in select_entries(data, include_public=True, include_private=True)}
    assert tags == {"SFO-APCH", "SFO-DEP", "SFO-APCHDEP"}


def test_airport_remark_rows_collapse_to_aptrmk():
    """These produced the longest tags in the dataset (up to 29 chars).
    The remark number and "WITH <x> FREQ" tail are source bookkeeping.
    """
    data = NormalizedData(
        airports=[_airport("BWI")],
        frequencies=[
            _freq("BWI", 121.0, "OTHER", raw_freq_use="APT REMARK 39 WITH UNICOM FREQ"),
            _freq("BWI", 122.0, "OTHER", raw_freq_use="APT REMARK 100 WITH GCO FREQ"),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert {e.tag_name for e in entries} == {"BWI-APTRMK", "BWI-APTRMK2"}
    assert validate(entries, FTA_850L) == []


def test_non_remark_other_rows_are_left_alone():
    """503 of the 650 OTHER rows are already-short codes that fit fine --
    only the APT REMARK family needed shortening.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 120.0, "OTHER", raw_freq_use="PTD"),
            _freq("XXX", 121.0, "OTHER", raw_freq_use="PMSV METRO"),
        ],
        ils=[],
    )
    tags = {e.tag_name for e in select_entries(data, include_public=True, include_private=True)}
    assert tags == {"XXX-PTD", "XXX-PMSVMETRO"}


# ---------- command posts and the generic length safety net ----------


def test_command_post_suffixes_are_shortened_but_keep_the_service():
    """"ANG COMD POST" made 15-16 char tags. The service (Air National
    Guard vs Air Force Reserve) is the part worth keeping.
    """
    data = NormalizedData(
        airports=[_airport("DLH"), _airport("SKF")],
        frequencies=[
            _freq("DLH", 120.0, "MIL_GOV_OPS", raw_freq_use="ANG COMD POST"),
            _freq("SKF", 121.0, "MIL_GOV_OPS", raw_freq_use="AFRC COMD POST"),
        ],
        ils=[],
    )
    tags = {e.tag_name for e in select_entries(data, include_public=True, include_private=True)}
    assert tags == {"DLH-ANGCP", "SKF-AFRCCP"}


def test_short_mil_ops_suffixes_are_not_needlessly_abbreviated():
    """The suffix budget is computed from the actual prefix, not the
    worst case -- a 3-char LID leaves 9 chars, so ARNGOPS stays ARNGOPS
    rather than being cut down to fit a long orphan ID that isn't there.
    """
    data = NormalizedData(
        airports=[_airport("BIS"), _airport("BKT")],
        frequencies=[
            _freq("BIS", 120.0, "MIL_GOV_OPS", raw_freq_use="ARNG OPS"),
            _freq("BKT", 121.0, "MIL_GOV_OPS", raw_freq_use="RANGE CTL"),
        ],
        ils=[],
    )
    tags = {e.tag_name for e in select_entries(data, include_public=True, include_private=True)}
    assert tags == {"BIS-ARNGOPS", "BKT-RANGECTL"}


def test_unknown_long_raw_text_is_abbreviated_rather_than_overflowing():
    """The safety net: a value nobody wrote a rule for still has to
    produce a valid tag instead of failing the whole export.
    """
    data = NormalizedData(
        airports=[_airport("XXX")],
        frequencies=[
            _freq("XXX", 120.0, "MIL_GOV_OPS", raw_freq_use="SOME VERY LONG UNANTICIPATED THING"),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    tag = entries[0].tag_name
    assert len(tag) <= FTA_850L.max_tag_length
    assert validate(entries, FTA_850L) == []


def test_long_orphan_prefix_still_leaves_room_for_its_suffix():
    """Tightest case: an abbreviated 6-char orphan ID plus a long raw
    suffix, where the budget is smallest.
    """
    data = NormalizedData(
        airports=[],
        frequencies=[
            Frequency(
                airport_id="MEDICINE BOW", freq_mhz=120.0, freq_category="MIL_GOV_OPS",
                raw_freq_use="BASE OPS ADVISORY SVC", state="WY", city="X", name="X",
                lat=1.0, lon=-1.0,
            ),
        ],
        ils=[],
    )
    entries = select_entries(data, include_public=True, include_private=True)
    assert len(entries[0].tag_name) <= FTA_850L.max_tag_length
    assert validate(entries, FTA_850L) == []
