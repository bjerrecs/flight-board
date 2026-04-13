#!/usr/bin/env python3
"""
Parse VATSIM Scandinavia GRPluginStands.txt files and convert to FlightBoard
stands.json format.

Data sourced from VATSIM Scandinavia sector file repositories under a
non-commercial, attribution-required licence (approved by Thor Høgås).

Two file formats are handled:
  - EKDK (single-point): STAND:ICAO:ID:Nlat:Elon:heading
  - ENOR/ESAA (polygon):  STAND:ICAO:ID
                           COORD:Nlat:Elon
                           COORD:Nlat:Elon
                           ...  (centroid is derived from vertices)

Usage:
    # Dry-run — writes per-division preview files only
    python3 scripts/parse_scandinavia_stands.py

    # Merge all three divisions directly into static/stands.json
    python3 scripts/parse_scandinavia_stands.py --update-stands-json

    # Merge one specific airport only
    python3 scripts/parse_scandinavia_stands.py --icao EKCH --update-stands-json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "scandi_stand_info"
STANDS_PATH = REPO_ROOT / "static" / "stands.json"
DEFAULT_RADIUS = 35  # metres — matches existing FlightBoard stand entries

# Each division directory and whether to use polygon or single-point format
DIVISIONS: List[Tuple[str, str]] = [
    ("EKDK", "point"),
    ("ENOR", "polygon"),
    ("ESAA", "polygon"),
]


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

_DMS_RE = re.compile(
    r"""
    ^
    ([NS])                  # hemisphere
    (\d{2,3})               # degrees
    \.
    (\d{2})                 # minutes
    \.
    ([\d.]+)                # seconds (may be decimal)
    :
    ([EW])                  # hemisphere
    (\d{3})                 # degrees
    \.
    (\d{2})                 # minutes
    \.
    ([\d.]+)                # seconds
    $
    """,
    re.VERBOSE,
)


def dms_to_decimal(hemi: str, deg: str, mins: str, secs: str) -> float:
    value = float(deg) + float(mins) / 60.0 + float(secs) / 3600.0
    if hemi in ("S", "W"):
        value = -value
    return value


def parse_coord(raw: str) -> Optional[Tuple[float, float]]:
    """Parse 'Nlat:Elon' or 'Slat:Wlon' string to (lat, lon) decimal degrees."""
    m = _DMS_RE.match(raw.strip())
    if not m:
        return None
    lat_hemi, lat_d, lat_m, lat_s, lon_hemi, lon_d, lon_m, lon_s = m.groups()
    lat = dms_to_decimal(lat_hemi, lat_d, lat_m, lat_s)
    lon = dms_to_decimal(lon_hemi, lon_d, lon_m, lon_s)
    return round(lat, 6), round(lon, 6)


def centroid(coords: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(coords)
    return round(sum(c[0] for c in coords) / n, 6), round(sum(c[1] for c in coords) / n, 6)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_point_format(text: str) -> Dict[str, List[Dict]]:
    """
    EKDK format — lat/lon on the STAND line:
        STAND:ICAO:ID:Nlat:Elon:heading
    """
    airports: Dict[str, List[Dict]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("STAND:"):
            continue
        parts = line.split(":")
        # Expected: STAND  ICAO  ID  Nhh.mm.ss.sss  Ehhh.mm.ss.sss  heading
        if len(parts) < 6:
            continue
        _, icao, stand_id = parts[0], parts[1], parts[2]
        # Format: STAND:ICAO:ID:N055.37.42.710:E012.38.33.450:heading
        coord_raw = parts[3] + ":" + parts[4]
        coord = parse_coord(coord_raw)
        if coord is None:
            continue
        entry = {
            "name": stand_id,
            "lat": coord[0],
            "lon": coord[1],
            "radius": DEFAULT_RADIUS,
            "type": "contact",
        }
        airports.setdefault(icao, []).append(entry)
    return airports


def parse_polygon_format(text: str) -> Dict[str, List[Dict]]:
    """
    ENOR/ESAA format — polygon of COORD lines follows each STAND line:
        STAND:ICAO:ID
        COORD:Nlat:Elon
        ...
    Centroid of polygon vertices is used as the stand position.
    """
    airports: Dict[str, List[Dict]] = {}
    current_icao: Optional[str] = None
    current_id: Optional[str] = None
    current_coords: List[Tuple[float, float]] = []

    def flush():
        if current_icao and current_id and current_coords:
            lat, lon = centroid(current_coords)
            entry = {
                "name": current_id,
                "lat": lat,
                "lon": lon,
                "radius": DEFAULT_RADIUS,
                "type": "contact",
            }
            airports.setdefault(current_icao, []).append(entry)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue

        if line.startswith("STAND:"):
            flush()
            current_coords = []
            parts = line.split(":")
            if len(parts) >= 3:
                current_icao = parts[1]
                current_id = parts[2]
            else:
                current_icao = current_id = None
            continue

        if line.startswith("COORD:"):
            # COORD:Nhh.mm.ss.sss:Ehhh.mm.ss.sss
            coord_raw = line[6:]  # strip "COORD:"
            coord = parse_coord(coord_raw)
            if coord:
                current_coords.append(coord)
            continue

        # Any other directive resets coord accumulation context but not stand identity
        # (stand metadata lines like WINGSPAN, WTC etc. come after coords)

    flush()  # last stand
    return airports


# ---------------------------------------------------------------------------
# Merge / output helpers
# ---------------------------------------------------------------------------

def sort_stands(stands: List[Dict]) -> List[Dict]:
    """Natural-sort stands by name so output is stable and readable."""
    def key(s: Dict):
        name = s["name"]
        # Split into (prefix-letters, numeric, suffix)
        parts = re.split(r"(\d+)", name)
        return [p.zfill(6) if p.isdigit() else p for p in parts]
    return sorted(stands, key=key)


def load_stands_json(path: Path) -> Dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_stands_json(path: Path, data: Dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_preview(division: str, airports: Dict[str, List[Dict]]) -> Path:
    out = REPO_ROOT / "static" / f"scandi_{division.lower()}_stands.generated.json"
    payload = {
        "source": f"VATSIM Scandinavia GRPluginStands — {division}",
        "license_note": (
            "Non-commercial use with attribution. "
            "Source: VATSIM Scandinavia sector file repositories."
        ),
        "airports": {icao: sort_stands(stands) for icao, stands in sorted(airports.items())},
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_division(division: str, fmt: str) -> Dict[str, List[Dict]]:
    txt_path = DATA_DIR / division / "GRpluginStands.txt"
    if not txt_path.exists():
        print(f"  WARNING: {txt_path} not found — skipping {division}")
        return {}
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    if fmt == "point":
        return parse_point_format(text)
    return parse_polygon_format(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse VATSIM Scandinavia stands into FlightBoard format.")
    ap.add_argument(
        "--icao",
        default=None,
        help="Only process this airport (e.g. EKCH). Omit to process all.",
    )
    ap.add_argument(
        "--update-stands-json",
        action="store_true",
        help=f"Merge results into {STANDS_PATH} (default: write preview .generated.json files only).",
    )
    ap.add_argument(
        "--stands-path",
        default=str(STANDS_PATH),
        help=f"Path to master stands JSON (default: {STANDS_PATH}).",
    )
    args = ap.parse_args()

    filter_icao = args.icao.upper() if args.icao else None
    stands_path = Path(args.stands_path)

    all_airports: Dict[str, List[Dict]] = {}

    for division, fmt in DIVISIONS:
        print(f"Parsing {division} ({fmt} format)...")
        airports = parse_division(division, fmt)
        for icao, stands in airports.items():
            if filter_icao and icao != filter_icao:
                continue
            all_airports.setdefault(icao, []).extend(stands)
        stand_count = sum(len(v) for v in airports.items() if not filter_icao or v[0] == filter_icao)
        print(f"  {len(airports)} airports, "
              f"{sum(len(v) for v in airports.values())} stands total")
        if not args.update_stands_json:
            out = write_preview(division, airports)
            print(f"  Preview written to {out.relative_to(REPO_ROOT)}")

    if args.update_stands_json:
        data = load_stands_json(stands_path)
        for icao, stands in all_airports.items():
            data[icao] = sort_stands(stands)
            print(f"  Merged {len(stands):>3} stands for {icao}")
        write_stands_json(stands_path, data)
        print(f"\nUpdated {stands_path.relative_to(REPO_ROOT)} "
              f"({len(all_airports)} airports added/replaced).")
    else:
        total = sum(len(v) for v in all_airports.values())
        print(f"\nDry run complete — {total} stands across {len(all_airports)} airports.")
        print("Re-run with --update-stands-json to merge into static/stands.json.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
