from afp.adapters.faa import FAAAdapter


def test_parses_airports_and_maps_public_use(faa_adapter):
    data = faa_adapter.parse()
    by_id = {a.id: a for a in data.airports}

    assert by_id["SNS"].public_use is True
    assert by_id["WVI"].public_use is True
    assert by_id["PVT1"].public_use is False

    sns = by_id["SNS"]
    assert sns.lat == 36.662783
    assert sns.lon == -121.606367
    assert sns.city == "SALINAS"
    assert sns.state == "CA"


def test_splits_freq_and_dme_channel(faa_adapter):
    data = faa_adapter.parse()
    vor = next(f for f in data.frequencies if f.freq_category == "VOR")

    assert vor.freq_mhz == 115.700
    assert vor.source_remark == "DME:109X"
    # standalone navaid carries its own position, distinct from any airport
    assert vor.lat == 36.664000
    assert vor.lon == -121.608000


def test_plain_freq_without_dme_has_no_remark(faa_adapter):
    data = faa_adapter.parse()
    atis = next(f for f in data.frequencies if f.weather_subtype == "ATIS")

    assert atis.freq_mhz == 124.850
    assert atis.freq_category == "WEATHER_STATION"
    assert atis.source_remark is None


def test_splits_trailing_letter_suffix_with_no_slash():
    """Real NASR data has RCO frequencies like "122.1R" (receive-only) with
    no slash -- discovered downloading the live 2026-08-06 cycle.
    """
    from afp.adapters.faa import _split_freq

    assert _split_freq("122.1R") == (122.1, "R")


def test_splits_oceanic_hf_frequency_with_family_name_remark():
    """Real NASR data also has FSS oceanic HF frequencies tagged with a
    trailing family name instead of a DME channel, e.g. "8903 CARIBBEAN
    FAMILY A" -- also from the live 2026-08-06 cycle.
    """
    from afp.adapters.faa import _split_freq

    assert _split_freq("8903 CARIBBEAN FAMILY A") == (8903.0, "CARIBBEAN FAMILY A")
    assert _split_freq("8903X") == (8903.0, "X")


def test_facility_status_is_not_substring_matched(faa_adapter):
    """NON-ATCT must never be confused with ATCT by substring checks."""
    data = faa_adapter.parse()
    wvi_ctaf = next(
        f for f in data.frequencies if f.airport_id == "WVI" and f.freq_category == "CTAF"
    )
    assert wvi_ctaf.facility_status == "NON_TOWERED"
    assert wvi_ctaf.facility_status != "TOWERED"


def test_classifies_tower_and_weather_station(faa_adapter):
    data = faa_adapter.parse()
    tower = next(f for f in data.frequencies if f.airport_id == "SNS" and f.freq_category == "TOWER")
    weather = next(f for f in data.frequencies if f.airport_id == "WVI" and f.freq_category == "WEATHER_STATION")

    assert tower.freq_mhz == 118.900
    assert tower.facility_status == "TOWERED"
    # real NASR AWOS rows are their own ASOS_AWOS-facility record, not a
    # plain "AWOS-3" on an otherwise-ordinary airport row
    assert weather.weather_subtype == "AWOS"
    assert weather.freq_mhz == 135.075


def test_raw_freq_use_preserved_for_tag_naming_fidelity(faa_adapter):
    data = faa_adapter.parse()
    vor = next(f for f in data.frequencies if f.airport_id == "SNS" and f.freq_category == "VOR")
    assert vor.raw_freq_use == "VOR"


def test_captures_serviced_state_city_name_for_every_row(faa_adapter):
    """SERVICED_STATE/SERVICED_CITY/SERVICED_FAC_NAME are captured for
    every row, not just standalone facilities -- needed for filtering
    facilities that have no matching airport record at all (e.g. a
    standalone VOR), confirmed present in real NASR data.
    """
    data = faa_adapter.parse()
    sns_atis = next(f for f in data.frequencies if f.airport_id == "SNS" and f.freq_category == "WEATHER_STATION")
    assert sns_atis.state == "CA"
    assert sns_atis.city == "SALINAS"
    assert sns_atis.name == "SALINAS MUNI"


def test_standalone_facility_with_no_matching_airport_still_parses(faa_adapter):
    """A standalone VORTAC (PXN/Panoche) has no row in APT_BASE.csv at
    all -- real-data bug report confirmed 8.3% of all FRQ.csv rows
    nationwide are like this. The adapter must still parse it (with its
    own state/city/name/position) -- dropping such rows happens later,
    in selection/query, not here.
    """
    data = faa_adapter.parse()
    airport_ids = {a.id for a in data.airports}
    assert "PXN" not in airport_ids

    pxn = next(f for f in data.frequencies if f.airport_id == "PXN")
    assert pxn.freq_category == "VOR"
    assert pxn.state == "CA"
    assert pxn.city == "PANOCHE"
    assert pxn.name == "PANOCHE"
    assert pxn.lat == 36.826000
    assert pxn.lon == -120.883000


def test_parses_ils_rows(faa_adapter):
    data = faa_adapter.parse()
    assert len(data.ils) == 2
    ils = data.ils[0]
    assert ils.airport_id == "SNS"
    assert ils.runway_end_id == "31"
    assert ils.freq_mhz == 109.900
    assert ils.system_type == "LS"
    assert ils.component_status == "OPERATIONAL IFR"


def test_accepts_file_like_streams(tmp_path):
    import io

    apt = io.StringIO(
        "ARPT_ID,ARPT_NAME,FACILITY_USE_CODE,CITY,STATE_CODE,LAT_DECIMAL,LONG_DECIMAL\n"
        "TST,TEST FIELD,PU,TESTVILLE,CA,10.0,-20.0\n"
    )
    frq = io.StringIO(
        "SERVICED_FACILITY,FACILITY_TYPE,FREQ,FREQ_USE\n"
        "TST,NON-ATCT,122.800,CTAF\n"
    )
    ils = io.StringIO("ARPT_ID,RWY_END_ID,LOC_FREQ,SYSTEM_TYPE_CODE,COMPONENT_STATUS\n")

    adapter = FAAAdapter(apt_base=apt, frq=frq, ils_base=ils)
    data = adapter.parse()

    assert len(data.airports) == 1
    assert data.airports[0].id == "TST"
