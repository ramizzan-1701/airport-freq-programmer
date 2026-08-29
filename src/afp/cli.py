"""CLI, extended for milestone 3: filter/query layer against the
in-memory DB, still CLI-driven -- proves out filter combinations and
performance before any UI gets built around them (spec §7 step 3).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .adapters.faa import FAAAdapter
from .counter import counter_status
from .export.fta850l import FTA_850L
from .export.xml_writer import ExportValidationError, build_xml
from .nasr.source import NASRSource
from .query import FilterState, RadiusFilter, build_database, filtered_entries, resolve_center


def _parse_radius_arg(raw: str) -> tuple[str | tuple[float, float], float, str]:
    parts = raw.rsplit(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"--radius must be CENTER:RADIUS_NM:MODE, got {raw!r}")
    center_str, radius_str, mode = parts

    if mode not in ("include", "exclude"):
        raise argparse.ArgumentTypeError(f"--radius mode must be 'include' or 'exclude', got {mode!r}")
    try:
        radius_nm = float(radius_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--radius NM must be a number, got {radius_str!r}") from None

    if "," in center_str:
        lat_str, lon_str = center_str.split(",", 1)
        try:
            center: str | tuple[float, float] = (float(lat_str), float(lon_str))
        except ValueError:
            raise argparse.ArgumentTypeError(f"--radius center lat,lon must be numeric, got {center_str!r}") from None
    else:
        center = center_str

    return center, radius_nm, mode


def _build_filter_state(args: argparse.Namespace, conn) -> FilterState:
    radius_filters = []
    for center, radius_nm, mode in args.radius or []:
        lat, lon = resolve_center(conn, center)
        radius_filters.append(RadiusFilter(center_lat=lat, center_lon=lon, radius_nm=radius_nm, mode=mode))

    return FilterState(
        states=frozenset(args.state) if args.state else None,
        cities=frozenset(args.city) if args.city else None,
        freq_categories=frozenset(args.freq_category) if args.freq_category else None,
        weather_subtypes=frozenset(args.weather_subtype) if args.weather_subtype else None,
        platform_types=frozenset(args.platform_type) if args.platform_type else None,
        facility_statuses=frozenset(args.facility_status) if args.facility_status else None,
        include_non_site_facilities=args.include_non_site_facilities,
        primary_approach_radio_calls=frozenset(args.approach_call) if args.approach_call else None,
        tower_hours_24_only=args.tower_24_hour_only,
        ils_component_statuses=frozenset(args.ils_status) if args.ils_status else None,
        ils_system_types=frozenset(args.ils_system_type) if args.ils_system_type else None,
        radius_filters=tuple(radius_filters),
        include_public=not args.exclude_public,
        include_private=args.include_private,
    )


def _run_query(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    adapter = FAAAdapter(
        apt_base=args.data_dir / "APT_BASE.csv",
        frq=args.data_dir / "FRQ.csv",
        ils_base=args.data_dir / "ILS_BASE.csv",
    )
    data = adapter.parse()
    parse_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    conn = build_database(data)
    build_ms = (time.perf_counter() - t1) * 1000

    filters = _build_filter_state(args, conn)

    t2 = time.perf_counter()
    entries = filtered_entries(conn, filters, mode=args.mode)
    query_ms = (time.perf_counter() - t2) * 1000

    status = counter_status(len(entries), FTA_850L)
    print(
        f"Parsed {len(data.airports)} airports, {len(data.frequencies)} frequencies, "
        f"{len(data.ils)} ILS records in {parse_ms:.0f} ms"
    )
    print(f"Built in-memory DB in {build_ms:.0f} ms")
    print(f"Filtered to {status.count} entries ({status.level}, cap {status.cap}) in {query_ms:.1f} ms")

    if args.output:
        try:
            xml_bytes = build_xml(entries, FTA_850L)
        except ExportValidationError as e:
            print(f"Cannot generate XML -- {len(e.problems)} validation problem(s):")
            for problem in e.problems[:10]:
                print(f"  - {problem}")
            if len(e.problems) > 10:
                print(f"  ... and {len(e.problems) - 10} more")
            return 1
        args.output.write_bytes(xml_bytes)
        print(f"Wrote {args.output}")

    return 0


def _run_serve(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from .web import create_app

    app = create_app(cache_dir=args.cache_dir)
    url = f"http://{args.host}:{args.port}"
    print(f"Serving at {url}  (cache dir: {args.cache_dir})")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="afp")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("./nasr_cache"),
        help="where downloaded zips/CSVs and the cycle store live",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="report the current NASR cycle and whether it's new")

    fetch_parser = subparsers.add_parser(
        "fetch", help="download and extract the current NASR cycle's CSVs"
    )
    fetch_parser.add_argument(
        "--mark-processed",
        action="store_true",
        help="record this cycle as processed after a successful fetch",
    )

    query_parser = subparsers.add_parser(
        "query", help="filter parsed NASR data and report the resulting entry count"
    )
    query_parser.add_argument(
        "--data-dir", type=Path, required=True,
        help="directory containing APT_BASE.csv, FRQ.csv, ILS_BASE.csv (e.g. a --cache-dir/<cycle> from `fetch`)",
    )
    query_parser.add_argument("--state", action="append", help="repeatable; OR's within this filter")
    query_parser.add_argument("--city", action="append", help="repeatable")
    query_parser.add_argument(
        "--freq-category", action="append",
        help="repeatable, e.g. CTAF, TOWER, WEATHER_STATION, APCH_DEP, VOR, ILS (see afp.classification for the "
        "full list); ILS is a pseudo-category -- include it to keep localizer entries, omit it while narrowing "
        "to exclude them (unconstrained/no filter still includes ILS by default)",
    )
    query_parser.add_argument(
        "--weather-subtype", action="append",
        help="repeatable, sub-filter within WEATHER_STATION: ATIS, ASOS, or AWOS",
    )
    query_parser.add_argument(
        "--platform-type", action="append",
        help="repeatable, e.g. AIRPORT, HELIPORT, SEAPLANE BASE, GLIDERPORT, BALLOONPORT, ULTRALIGHT",
    )
    query_parser.add_argument(
        "--facility-status", action="append",
        help="repeatable, TOWERED or NON_TOWERED",
    )
    query_parser.add_argument(
        "--include-non-site-facilities", action="store_true",
        help="exempt non-site facilities (VOR, RCAG, TRACON, etc. -- rows with no "
        "platform_type/facility_status at all) from --platform-type/--facility-status "
        "instead of --platform-type or --facility-status silently excluding them",
    )
    query_parser.add_argument("--approach-call", action="append", help="repeatable, matches PRIMARY_APPROACH_RADIO_CALL")
    query_parser.add_argument("--tower-24-hour-only", action="store_true")
    query_parser.add_argument("--ils-status", action="append", help="repeatable, e.g. 'OPERATIONAL IFR'")
    query_parser.add_argument("--ils-system-type", action="append", help="repeatable, e.g. ILS, LOC")
    query_parser.add_argument(
        "--radius", action="append", type=_parse_radius_arg, metavar="CENTER:NM:MODE",
        help="CENTER is an airport ID or 'LAT,LON'; MODE is include or exclude; repeatable -- "
        "multiple includes OR together (near any of them), multiple excludes AND together "
        "(away from all of them), and the two groups AND with each other",
    )
    query_parser.add_argument("--include-private", action="store_true")
    query_parser.add_argument("--exclude-public", action="store_true")
    query_parser.add_argument("--mode", choices=("smart", "raw"), default="smart")
    query_parser.add_argument("--output", type=Path, help="write the generated XML here")

    serve_parser = subparsers.add_parser("serve", help="launch the local web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")

    args = parser.parse_args(argv)

    if args.command == "query":
        return _run_query(args)

    if args.command == "serve":
        return _run_serve(args)

    source = NASRSource(cache_dir=args.cache_dir)

    if args.command == "check":
        cycle = source.get_current_cycle()
        update_available = source.is_update_available(current=cycle)
        print(f"Current published NASR cycle: {cycle.effective_date.isoformat()}")
        print("Update available" if update_available else "Already up to date")
        return 0

    if args.command == "fetch":
        result = source.fetch_current_cycle()
        print(f"Fetched cycle {result.cycle.effective_date.isoformat()}")
        print(f"  APT_BASE.csv -> {result.apt_base_csv}")
        print(f"  FRQ.csv      -> {result.frq_csv}")
        print(f"  ILS_BASE.csv -> {result.ils_base_csv}")
        if args.mark_processed:
            source.mark_processed(result.cycle)
            print("Marked as processed.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
