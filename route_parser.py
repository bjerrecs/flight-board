"""
Route parser for flight plan route strings.

Navdata sources (all in data/navdata/, gitignored):
  VATSpy.dat       — airport lat/lon (VATSpy Data Project)
  earth_fix.dat    — waypoint fixes (X-Plane 12 format)
  earth_nav.dat    — VORs and NDBs (X-Plane 12 format)
  CIFP/<ICAO>.dat  — SID/STAR procedures (X-Plane 12 CIFP format)

If any navdata file is missing, resolve_route() returns [] gracefully.
"""

import math
import os
import logging
import re

log = logging.getLogger(__name__)

_NAVDATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'navdata')
_CIFP_DIR    = os.path.join(_NAVDATA_DIR, 'CIFP')

# Loaded once at import time
airports: dict = {}   # ICAO → (lat, lon)
fixes: dict = {}      # ident → [(lat, lon), ...]
navaids: dict = {}    # ident → [(lat, lon), ...]

_loaded = False

# Lazy per-airport CIFP cache
# ICAO → {'SID': {proc_name: [ident, ...]}, 'STAR': {proc_name: [ident, ...]}}
_cifp_cache: dict = {}


def _parse_vatspy(path: str) -> dict:
    result = {}
    in_airports = False
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line == '[Airports]':
                    in_airports = True
                    continue
                if line.startswith('[') and line.endswith(']'):
                    in_airports = False
                    continue
                if not in_airports or not line or line.startswith(';'):
                    continue
                parts = line.split('|')
                if len(parts) < 4:
                    continue
                icao = parts[0].strip().upper()
                try:
                    lat = float(parts[2])
                    lon = float(parts[3])
                except ValueError:
                    continue
                result[icao] = (lat, lon)
    except FileNotFoundError:
        log.warning('VATSpy.dat not found at %s', path)
    except Exception as e:
        log.warning('Error parsing VATSpy.dat: %s', e)
    return result


def _parse_fixes(path: str) -> dict:
    result: dict = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('I') or line.startswith('A') or line.startswith('99'):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    lat = float(parts[0])
                    lon = float(parts[1])
                except ValueError:
                    continue
                ident = parts[2].upper()
                result.setdefault(ident, []).append((lat, lon))
    except FileNotFoundError:
        log.warning('earth_fix.dat not found at %s', path)
    except Exception as e:
        log.warning('Error parsing earth_fix.dat: %s', e)
    return result


def _parse_navaids(path: str) -> dict:
    """Parse earth_nav.dat. Keep types 2 (NDB), 3 (VOR), and 13 (standalone DME/TACAN)."""
    result: dict = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('I') or line.startswith('A') or line.startswith('99'):
                    continue
                parts = line.split()
                if len(parts) < 9:
                    continue
                try:
                    nav_type = int(parts[0])
                except ValueError:
                    continue
                if nav_type not in (2, 3, 13):
                    continue
                try:
                    lat = float(parts[1])
                    lon = float(parts[2])
                except ValueError:
                    continue
                ident = parts[7].upper()
                result.setdefault(ident, []).append((lat, lon))
    except FileNotFoundError:
        log.warning('earth_nav.dat not found at %s', path)
    except Exception as e:
        log.warning('Error parsing earth_nav.dat: %s', e)
    return result


def _parse_cifp(icao: str) -> dict:
    """
    Load and parse the CIFP procedure file for an airport.
    Returns:
      {'SID':  {proc_name: {transition: [ident, ...]}},
       'STAR': {proc_name: {transition: [ident, ...]}}}
    Legs with no named fix (CA, VA, etc.) are omitted.
    Results are cached after first load.
    """
    if icao in _cifp_cache:
        return _cifp_cache[icao]

    result: dict = {'SID': {}, 'STAR': {}}
    path = os.path.join(_CIFP_DIR, icao + '.dat')
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip().rstrip(';')
                if not line:
                    continue
                parts = line.split(',')
                if len(parts) < 12:
                    continue
                type_seq = parts[0]
                if ':' not in type_seq:
                    continue
                proc_type = type_seq.split(':', 1)[0].strip().upper()
                if proc_type not in ('SID', 'STAR'):
                    continue
                proc_name  = parts[2].strip()
                transition = parts[3].strip()
                ident      = parts[4].strip()
                if not proc_name or not transition or not ident:
                    continue
                bucket = result[proc_type].setdefault(proc_name, {}).setdefault(transition, [])
                if ident not in bucket:
                    bucket.append(ident)
    except FileNotFoundError:
        pass  # No CIFP data for this airport — silently skip
    except Exception as e:
        log.warning('Error parsing CIFP for %s: %s', icao, e)

    _cifp_cache[icao] = result
    return result


def _resolve_fix(ident: str, ref: tuple, max_km: float = 500) -> tuple | None:
    """Return the best (lat, lon) for a fix ident near ref, or None."""
    candidates = fixes.get(ident) or navaids.get(ident)
    if not candidates:
        return None
    return _pick_closest(candidates, ref, max_km=max_km)


# ── Runway / wind helpers ────────────────────────────────────────────────────

def _runway_heading(transition_name: str):
    """RW27L → 270, RW09R → 90. Returns None for non-runway transitions."""
    m = re.match(r'^RW(\d{2})[LRC]?$', transition_name, re.IGNORECASE)
    return int(m.group(1)) * 10 if m else None


def _heading_diff(a: int, b: int) -> int:
    """Smallest angular difference between two headings, 0–180."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def parse_metar_wind(metar_text: str):
    """
    Extract wind direction (degrees, FROM) from a METAR string.
    Returns None for calm, variable, or unparseable wind.
    """
    if not metar_text:
        return None
    m = re.search(r'\b(VRB|\d{3})(\d{2,3})(?:G\d{2,3})?KT\b', metar_text)
    if not m:
        return None
    dir_str = m.group(1)
    if dir_str == 'VRB':
        return None
    val = int(dir_str)
    return None if val == 0 else val


def _into_wind_score(transitions: dict, wind_dir: int) -> float:
    """
    Score a set of procedure transitions against wind direction.
    Returns the lowest heading difference across all runway transitions
    (0 = perfectly into wind, 180 = direct tailwind).
    Non-runway transitions (ALL, fix-named) are ignored; if none found, returns 180.
    """
    best = 180
    for trans in transitions:
        rh = _runway_heading(trans)
        if rh is not None:
            best = min(best, _heading_diff(rh, wind_dir))
    return best


def _pick_into_wind(candidates: list, cifp_procs: dict, wind_dir, dist_fn) -> str:
    """
    From a list of procedure name candidates, return the best one ranked by:
      1. Into-wind score (lower = better) — only used when wind_dir is not None
      2. dist_fn(name) → float proximity score (lower = better)
    """
    def sort_key(name):
        ws = _into_wind_score(cifp_procs[name], wind_dir) if wind_dir is not None else 180
        return (ws, dist_fn(name))
    return min(candidates, key=sort_key)


# ── Procedure inference from route waypoints ────────────────────────────────

def _infer_sid(origin_cifp: dict, first_fix: str, airport_ref: tuple,
               next_fix_ref, wind_dir) -> tuple:
    """
    Infer a SID when no procedure name is filed in the route.
    Uses first_fix (the first named en-route waypoint) as the SID exit fix.
    Prefers SIDs where first_fix is the last ident of some transition.
    Wind direction (FROM, degrees) is used to prefer into-wind runways.
    Returns (sid_name, idents_list) or (None, []).
    """
    sids = origin_cifp.get('SID', {})
    if not sids:
        return None, []

    # Candidates where first_fix is the true exit (last ident of any transition)
    exit_cands = [n for n, tr in sids.items()
                  if any(ids and ids[-1] == first_fix for ids in tr.values())]
    # Fallback: first_fix appears anywhere in the procedure
    any_cands  = [n for n, tr in sids.items()
                  if any(first_fix in ids for ids in tr.values())]
    candidates = exit_cands if exit_cands else any_cands
    if not candidates:
        return None, []

    if len(candidates) > 1:
        ref = next_fix_ref or airport_ref
        def dist_fn(name):
            for ids in sids[name].values():
                for ident in reversed(ids):
                    pos = _resolve_fix(ident, airport_ref)
                    if pos:
                        return _haversine(ref[0], ref[1], pos[0], pos[1])
            return float('inf')
        name = _pick_into_wind(candidates, sids, wind_dir, dist_fn)
    else:
        name = candidates[0]

    return name, _best_sid_transition(sids[name], airport_ref, next_fix_ref)


def _infer_star(dest_cifp: dict, last_fix: str, airport_ref: tuple,
                prev_fix_ref, wind_dir) -> tuple:
    """
    Infer a STAR when no procedure name is filed in the route.
    Uses last_fix (the last named en-route waypoint) as the STAR entry fix.
    Prefers STARs where last_fix is the first ident of some transition.
    Wind direction (FROM, degrees) is used to prefer into-wind runways.
    Returns (star_name, idents_list) or (None, []).
    """
    stars = dest_cifp.get('STAR', {})
    if not stars:
        return None, []

    entry_cands = [n for n, tr in stars.items()
                   if any(ids and ids[0] == last_fix for ids in tr.values())]
    any_cands   = [n for n, tr in stars.items()
                   if any(last_fix in ids for ids in tr.values())]
    candidates = entry_cands if entry_cands else any_cands
    if not candidates:
        return None, []

    if len(candidates) > 1:
        ref = prev_fix_ref or airport_ref
        def dist_fn(name):
            for ids in stars[name].values():
                for ident in ids:
                    pos = _resolve_fix(ident, airport_ref)
                    if pos:
                        return _haversine(ref[0], ref[1], pos[0], pos[1])
            return float('inf')
        name = _pick_into_wind(candidates, stars, wind_dir, dist_fn)
    else:
        name = candidates[0]

    return name, _best_star_transition(stars[name], airport_ref, prev_fix_ref)


def _best_sid_transition(transitions: dict, airport_ref: tuple, next_fix_ref: tuple | None) -> list:
    """
    Pick the SID transition whose EXIT (last) fix is closest to next_fix_ref
    (the first en-route waypoint after the SID in the filed route).
    Falls back to nearest ENTRY fix from airport if next_fix_ref is unavailable.
    Prepends the common 'ALL' transition idents if present.
    """
    if not transitions:
        return []
    if len(transitions) == 1:
        return next(iter(transitions.values()))

    all_idents = transitions.get('ALL', [])
    runway_transitions = {k: v for k, v in transitions.items() if k != 'ALL'}

    if not runway_transitions:
        return all_idents

    ref = next_fix_ref if next_fix_ref else airport_ref
    # For SID: score by distance from ref to the LAST resolvable fix in each transition
    best_key, best_dist = None, float('inf')
    for key, idents in runway_transitions.items():
        for ident in reversed(idents):  # last fix first
            pos = _resolve_fix(ident, airport_ref)
            if pos:
                dist = _haversine(ref[0], ref[1], pos[0], pos[1])
                if dist < best_dist:
                    best_dist = dist
                    best_key = key
                break

    if best_key is None:
        return all_idents + list(runway_transitions.values())[0]

    chosen = list(all_idents)
    for ident in runway_transitions[best_key]:
        if ident not in chosen:
            chosen.append(ident)
    return chosen


def _best_star_transition(transitions: dict, airport_ref: tuple, prev_fix_ref: tuple | None) -> list:
    """
    Pick the STAR transition whose ENTRY (first) fix is closest to prev_fix_ref
    (the last en-route waypoint before the STAR in the filed route).
    Falls back to airport ref if prev_fix_ref is unavailable.
    Appends the common 'ALL' transition idents if present.
    """
    if not transitions:
        return []
    if len(transitions) == 1:
        return next(iter(transitions.values()))

    all_idents = transitions.get('ALL', [])
    runway_transitions = {k: v for k, v in transitions.items() if k != 'ALL'}

    if not runway_transitions:
        return all_idents

    ref = prev_fix_ref if prev_fix_ref else airport_ref
    # For STAR: score by distance from ref to the FIRST resolvable fix in each transition
    best_key, best_dist = None, float('inf')
    for key, idents in runway_transitions.items():
        for ident in idents:  # first fix first
            pos = _resolve_fix(ident, airport_ref)
            if pos:
                dist = _haversine(ref[0], ref[1], pos[0], pos[1])
                if dist < best_dist:
                    best_dist = dist
                    best_key = key
                break

    if best_key is None:
        return all_idents

    # Runway transitions (RW06L etc.) append after the common route.
    # Entry transitions (fix-named, e.g. HAKMN) are the approach path INTO the
    # common route, so they come first.
    if re.match(r'^RW', best_key, re.IGNORECASE):
        chosen = list(all_idents)
        for ident in runway_transitions[best_key]:
            if ident not in chosen:
                chosen.append(ident)
    else:
        chosen = list(runway_transitions[best_key])
        for ident in all_idents:
            if ident not in chosen:
                chosen.append(ident)
    return chosen


def _expand_procedure(idents: list, airport_ref: tuple, wp_type: str) -> list:
    """
    Resolve CIFP procedure fix idents to waypoint dicts.
    Uses the airport position as the reference throughout — procedure fixes
    are always local to their airport, so we use a tight 500km cap and don't
    chain positions across the Atlantic.
    """
    out = []
    pos = airport_ref
    for ident in idents:
        candidates = fixes.get(ident) or navaids.get(ident)
        if not candidates:
            continue
        chosen = _pick_closest(candidates, pos, max_km=500)
        if chosen:
            out.append({'name': ident, 'lat': chosen[0], 'lon': chosen[1], 'type': wp_type})
            pos = chosen  # chain within the procedure for ordering
    return out


def _load_navdata():
    global airports, fixes, navaids, _loaded
    if _loaded:
        return
    airports = _parse_vatspy(os.path.join(_NAVDATA_DIR, 'VATSpy.dat'))
    fixes = _parse_fixes(os.path.join(_NAVDATA_DIR, 'earth_fix.dat'))
    navaids = _parse_navaids(os.path.join(_NAVDATA_DIR, 'earth_nav.dat'))
    log.info('Navdata loaded: %d airports, %d fix idents, %d navaid idents',
             len(airports), len(fixes), len(navaids))
    _loaded = True


def reload_navdata():
    global _loaded, airports, fixes, navaids, _cifp_cache
    airports = {}
    fixes = {}
    navaids = {}
    _cifp_cache.clear()
    _loaded = False
    _load_navdata()


# ── Great-circle helpers ────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))



_MAX_STEP_KM = 8000  # raised to handle transatlantic/transpacific routes


def _pick_closest(candidates, ref, max_km=_MAX_STEP_KM):
    """Return the nearest candidate to ref, or None if all are beyond max_km."""
    best = min(candidates, key=lambda c: _haversine(ref[0], ref[1], c[0], c[1]))
    if _haversine(ref[0], ref[1], best[0], best[1]) > max_km:
        return None
    return best


# ── Token classification ────────────────────────────────────────────────────

_SPEED_ALT_RE = re.compile(r'^[NKM]\d{4}[FSAM]\d{3,4}$', re.IGNORECASE)
_AIRWAY_RE = re.compile(r'^[A-Z]{1,2}\d{1,4}[A-Z]?$', re.IGNORECASE)  # L612, UN601, N57, Y803
_SKIP_WORDS = frozenset(['DCT', 'SID', 'STAR', 'TRANS', 'RNAV'])


def _strip_runway_suffix(token: str) -> str:
    """EGLL/27R → EGLL"""
    if '/' in token:
        return token.split('/')[0]
    return token


# ── Public API ──────────────────────────────────────────────────────────────

def resolve_route(route_str: str, origin_icao: str, dest_icao: str,
                  wind_dir: int = None) -> list:
    """
    Parse a VATSIM flight plan route string into an ordered list of waypoints.

    Returns a list of dicts: [{name, lat, lon, type}, ...]
    where type is one of: 'airport', 'fix', 'navaid'

    wind_dir: wind direction in degrees (FROM), used to prefer into-wind runway
    transitions when inferring SID/STAR from route waypoints.

    Returns [] if navdata is unavailable or route cannot be resolved.
    """
    _load_navdata()

    if not airports:
        return []  # navdata not loaded

    origin_icao = (origin_icao or '').strip().upper()
    dest_icao = (dest_icao or '').strip().upper()

    origin_coords = airports.get(origin_icao)
    dest_coords = airports.get(dest_icao)

    # Load CIFP procedure data for origin and destination (cached, silently absent if missing)
    origin_cifp = _parse_cifp(origin_icao) if origin_icao else {'SID': {}, 'STAR': {}}
    dest_cifp   = _parse_cifp(dest_icao)   if dest_icao   else {'SID': {}, 'STAR': {}}

    # Pre-scan tokens to identify exactly one SID (first match) and one STAR (last match)
    tokens = [_strip_runway_suffix(t).upper() for t in (route_str or '').split()]
    sid_token  = next((t for t in tokens if t in origin_cifp['SID']), None)
    star_token = next((t for t in reversed(tokens) if t in dest_cifp['STAR']), None)

    # Find the first en-route fix after the SID (used to pick the right runway transition)
    def _first_enroute_fix_after_sid():
        if sid_token is None:
            return None
        idx = tokens.index(sid_token)
        ref = origin_coords or (0.0, 0.0)
        for t in tokens[idx + 1:]:
            if t in _SKIP_WORDS or _SPEED_ALT_RE.match(t) or _AIRWAY_RE.match(t):
                continue
            if t == star_token:
                break
            pos = _resolve_fix(t, ref, max_km=8000)
            if pos:
                return pos
        return None

    # Find the last en-route fix before the STAR (used to pick the right runway transition)
    def _last_enroute_fix_before_star():
        if star_token is None:
            return None
        idx = len(tokens) - 1 - list(reversed(tokens)).index(star_token)
        ref = dest_coords or (0.0, 0.0)
        for t in reversed(tokens[:idx]):
            if t in _SKIP_WORDS or _SPEED_ALT_RE.match(t) or _AIRWAY_RE.match(t):
                continue
            if t == sid_token:
                break
            pos = _resolve_fix(t, ref, max_km=8000)
            if pos:
                return pos
        return None

    sid_next_ref  = _first_enroute_fix_after_sid()
    star_prev_ref = _last_enroute_fix_before_star()

    # ── Infer SID from first route fix when no SID name was filed ───────────
    # Common in UK/Europe: pilots file the exit fix (e.g. "CPT") not the
    # procedure name (e.g. "CPT5J").  Wind direction (if available) is used to
    # prefer the into-wind runway transition.
    _inferred_sid_fix   = None   # the route token that triggered SID inference
    _inferred_sid_idents = []
    if sid_token is None and origin_cifp['SID'] and origin_coords:
        ref = origin_coords
        _found_first = _found_second = False
        _first_fix_pos = _second_fix_pos = None
        for t in tokens:
            if t in _SKIP_WORDS or _SPEED_ALT_RE.match(t) or _AIRWAY_RE.match(t):
                continue
            if len(t) == 4 and t.isalpha():   # ICAO code — skip
                continue
            pos = _resolve_fix(t, ref, max_km=8000)
            if pos is None:
                continue
            if not _found_first:
                _inferred_sid_fix = t
                _first_fix_pos    = pos
                _found_first      = True
            else:
                _second_fix_pos = pos
                break
        if _inferred_sid_fix:
            _, _inferred_sid_idents = _infer_sid(
                origin_cifp, _inferred_sid_fix, origin_coords,
                _second_fix_pos, wind_dir
            )

    # ── Infer STAR from last route fix when no STAR name was filed ───────────
    _inferred_star_fix    = None
    _inferred_star_idents = []
    if star_token is None and dest_cifp['STAR'] and dest_coords:
        ref = dest_coords
        _found_last = _found_prev = False
        _last_fix_pos = _prev_fix_pos = None
        for t in reversed(tokens):
            if t in _SKIP_WORDS or _SPEED_ALT_RE.match(t) or _AIRWAY_RE.match(t):
                continue
            if len(t) == 4 and t.isalpha():
                continue
            pos = _resolve_fix(t, ref, max_km=8000)
            if pos is None:
                continue
            if not _found_last:
                _inferred_star_fix = t
                _last_fix_pos      = pos
                _found_last        = True
            else:
                _prev_fix_pos = pos
                break
        if _inferred_star_fix:
            _, _inferred_star_idents = _infer_star(
                dest_cifp, _inferred_star_fix, dest_coords,
                _prev_fix_pos, wind_dir
            )

    _inferred_sid_done  = False
    _inferred_star_done = False

    waypoints = []

    if origin_coords:
        waypoints.append({'name': origin_icao, 'lat': origin_coords[0], 'lon': origin_coords[1], 'type': 'airport'})

    # Tracks the last known position for nearest-neighbour disambiguation
    last_coords = origin_coords or dest_coords or (0.0, 0.0)

    sid_done  = False
    star_done = False

    for token in tokens:
        if not token:
            continue
        if token in _SKIP_WORDS:
            continue
        if _SPEED_ALT_RE.match(token):
            continue

        # SID/STAR checks must come before the airway regex so that procedure
        # names matching the airway pattern (e.g. BIG1X, L8B, UN4C) are not
        # silently discarded before they can be expanded.

        # SID — only expand the first matching token
        if token == sid_token and not sid_done:
            sid_done = True
            sid_ref = origin_coords or last_coords
            idents = _best_sid_transition(origin_cifp['SID'][token], sid_ref, sid_next_ref)
            expanded = _expand_procedure(idents, sid_ref, 'sid')
            waypoints.extend(expanded)
            if expanded:
                last_coords = (expanded[-1]['lat'], expanded[-1]['lon'])
            continue

        # STAR — only expand the last matching token
        if token == star_token and not star_done:
            # Check this is the last occurrence
            remaining = tokens[tokens.index(token) + 1:]
            if star_token not in remaining:
                star_done = True
                star_ref = dest_coords or last_coords
                idents = _best_star_transition(dest_cifp['STAR'][token], star_ref, star_prev_ref)
                expanded = _expand_procedure(idents, star_ref, 'star')
                waypoints.extend(expanded)
                if expanded:
                    last_coords = (expanded[-1]['lat'], expanded[-1]['lon'])
                continue

        # Inferred SID — inject when we reach the exit fix token
        if (_inferred_sid_idents and not _inferred_sid_done
                and token == _inferred_sid_fix):
            _inferred_sid_done = True
            expanded = _expand_procedure(_inferred_sid_idents, origin_coords or last_coords, 'sid')
            waypoints.extend(expanded)
            if expanded:
                last_coords = (expanded[-1]['lat'], expanded[-1]['lon'])
            continue  # exit fix is already the last waypoint of the expanded SID

        # Inferred STAR — inject when we reach the entry fix token
        if (_inferred_star_idents and not _inferred_star_done
                and token == _inferred_star_fix):
            _inferred_star_done = True
            expanded = _expand_procedure(_inferred_star_idents, dest_coords or last_coords, 'star')
            waypoints.extend(expanded)
            if expanded:
                last_coords = (expanded[-1]['lat'], expanded[-1]['lon'])
            continue  # entry fix is already the first waypoint of the expanded STAR

        # Skip airways — but only after SID/STAR/airport checks above have had
        # a chance to claim the token first.
        if _AIRWAY_RE.match(token):
            continue

        # 4-letter ICAO airport?
        if len(token) == 4 and token.isalpha():
            if token in (origin_icao, dest_icao):
                continue
            coords = airports.get(token)
            if coords:
                waypoints.append({'name': token, 'lat': coords[0], 'lon': coords[1], 'type': 'airport'})
                last_coords = coords
                continue

        # 2-5 char alphanumeric — try fix, then navaid
        if 2 <= len(token) <= 5 and token.isalnum():
            candidates = fixes.get(token) or navaids.get(token)
            nav_type = 'fix' if fixes.get(token) else 'navaid'
            if candidates:
                chosen = _pick_closest(candidates, last_coords)
                if chosen:
                    waypoints.append({'name': token, 'lat': chosen[0], 'lon': chosen[1], 'type': nav_type})
                    last_coords = chosen
                continue

        # Unknown token — skip silently

    if dest_coords:
        waypoints.append({'name': dest_icao, 'lat': dest_coords[0], 'lon': dest_coords[1], 'type': 'airport'})

    # Remove consecutive duplicate fixes (e.g. SID exit fix repeated in route string)
    deduped = []
    for wp in waypoints:
        if deduped and deduped[-1]['name'] == wp['name']:
            continue
        deduped.append(wp)
    return deduped
