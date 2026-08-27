from afp.adapters.faa_classification import (
    classify_facility_status,
    classify_freq_category,
    classify_platform_type,
    classify_weather_subtype,
)


def cat(freq_use="", facility_type="", serviced_site_type=""):
    return classify_freq_category(freq_use, facility_type, serviced_site_type)


def test_weather_station_spans_all_three_columns():
    # ATIS is a broadcast a tower performs -- FREQ_USE only
    assert cat(freq_use="ATIS") == "WEATHER_STATION"
    assert cat(freq_use="D-ATIS") == "WEATHER_STATION"
    # ASOS/AWOS is its own facility record -- confirmed via FACILITY_TYPE
    # or SERVICED_SITE_TYPE, and real data never puts it plainly in
    # FREQ_USE without a site-ID prefix
    assert cat(freq_use="CVH AWOS-3", facility_type="ASOS_AWOS", serviced_site_type="AWOS-3") == "WEATHER_STATION"
    assert cat(freq_use="0J4 ASOS", facility_type="ASOS_AWOS", serviced_site_type="ASOS") == "WEATHER_STATION"
    assert cat(facility_type="ASOS_AWOS") == "WEATHER_STATION"
    assert cat(serviced_site_type="ASOS") == "WEATHER_STATION"
    assert cat(serviced_site_type="AWOS-3PT") == "WEATHER_STATION"


def test_ctaf_and_tower_and_ground_and_clearance():
    assert cat(freq_use="CTAF") == "CTAF"
    for lcl in ("LCL/P", "LCL/S", "LCL/P IC"):
        assert cat(freq_use=lcl) == "TOWER"
    for gnd in ("GND/P", "GND/S", "GND/P IC", "GND METERING"):
        assert cat(freq_use=gnd) == "GROUND"
    assert cat(freq_use="CD/P") == "CLEARANCE"
    assert cat(freq_use="CD PRE TAXI CLNC") == "CLEARANCE"


def test_ground_does_not_false_positive_on_procedure_fix_starting_with_gnd():
    """Real data has a "GNDLF STAR" procedure-fix row -- a naive "starts
    with GND" rule would misclassify it as Ground Control.
    """
    assert cat(freq_use="GNDLF STAR") == "PROCEDURE_FIX"


def test_unicom_and_approach_departure():
    assert cat(freq_use="UNICOM") == "UNICOM"
    assert cat(freq_use="APCH/P DEP/P") == "APCH_DEP"
    assert cat(freq_use="APCH/S") == "APCH_DEP"
    assert cat(freq_use="DEP/P") == "APCH_DEP"


def test_airspace_info_is_not_a_frequency_category():
    """CLASS B/C/TRSA are usage annotations on a co-frequency Approach/
    Departure row, not a real category -- but still identifiable as
    exactly that, not dumped into OTHER.
    """
    for value in ("CLASS B", "CLASS C", "CLASS C/S", "TRSA"):
        assert cat(freq_use=value) == "AIRSPACE_INFO"


def test_navaid_families():
    for value in ("VOR", "VORTAC", "VOR/DME"):
        assert cat(freq_use=value) == "VOR"
        assert cat(serviced_site_type=value) == "VOR"
    assert cat(freq_use="VOT") == "VOT"
    for value in ("NDB", "NDB/DME", "MARINE NDB"):
        assert cat(freq_use=value) == "NDB"
    assert cat(serviced_site_type="DME") == "DME"
    assert cat(freq_use="TACAN") == "TACAN"


def test_facility_based_categories():
    for value in ("RCAG", "RCO", "RCO1", "RADIO"):
        assert cat(facility_type=value) == "RCAG"
        assert cat(serviced_site_type=value) == "RCAG"
    # real RCAG rows are site-name-prefixed in FREQ_USE ("BANGOR RCAG")
    # but the classification relies on FACILITY_TYPE/SERVICED_SITE_TYPE,
    # which stay clean
    assert cat(freq_use="BANGOR RCAG", facility_type="RCAG") == "RCAG"
    assert cat(facility_type="TRACON") == "TRACON"
    assert cat(facility_type="FSS") == "FSS"
    assert cat(facility_type="ARTCC") == "ARTCC"
    assert cat(facility_type="CERAP") == "ARTCC"


def test_military_gov_ops_and_emergency():
    for value in ("OPS", "ARNG OPS", "ANG OPS", "COMD POST", "NASA OPS", "BASE OPS", "RANGE CTL", "RAMP CTL"):
        assert cat(freq_use=value) == "MIL_GOV_OPS"
    assert cat(freq_use="ANG COMD POST") == "MIL_GOV_OPS"  # substring, not just exact list
    assert cat(freq_use="SOME UNKNOWN OPS") == "MIL_GOV_OPS"  # ends with " OPS"
    assert cat(freq_use="EMERG") == "EMERGENCY"


def test_procedure_fix():
    assert cat(freq_use="TARPN STAR") == "PROCEDURE_FIX"
    assert cat(freq_use="GARLAND DP") == "PROCEDURE_FIX"
    assert cat(freq_use="WYLSN RNAV DP") == "PROCEDURE_FIX"


def test_unmatched_falls_to_other():
    assert cat(freq_use="PMSV METRO") == "OTHER"
    assert cat(freq_use="SOMETHING NOBODY ANTICIPATED") == "OTHER"


def test_weather_subtype_matches_freq_category_weather_station_rule():
    assert classify_weather_subtype("ATIS", "") == "ATIS"
    assert classify_weather_subtype("D-ATIS", "") == "ATIS"
    assert classify_weather_subtype("CVH AWOS-3", "AWOS-3") == "AWOS"
    assert classify_weather_subtype("0J4 ASOS", "ASOS") == "ASOS"
    assert classify_weather_subtype("", "ASOS") == "ASOS"
    assert classify_weather_subtype("", "AWOS-3PT") == "AWOS"


def test_weather_subtype_none_for_non_weather_rows():
    assert classify_weather_subtype("CTAF", "AIRPORT") is None


def test_platform_type_scoped_to_landing_platforms_only():
    for value in ("AIRPORT", "HELIPORT", "SEAPLANE BASE", "GLIDERPORT", "BALLOONPORT", "ULTRALIGHT"):
        assert classify_platform_type(value) == value
    # navaid/facility site types aren't landing platforms
    assert classify_platform_type("VOR") is None
    assert classify_platform_type("RCAG") is None
    assert classify_platform_type("") is None


def test_facility_status_towered_vs_non_towered():
    assert classify_facility_status("NON-ATCT") == "NON_TOWERED"
    for value in ("ATCT", "ATCT-TRACON", "ATCT-RAPCON", "ATCT-A/C", "ATCT-RATCF"):
        assert classify_facility_status(value) == "TOWERED"
    # not applicable for standalone navaids/facilities
    assert classify_facility_status("RCAG") is None
    assert classify_facility_status("TRACON") is None
    assert classify_facility_status("") is None
