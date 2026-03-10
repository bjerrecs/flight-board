# CLAUDE.md — Flight Board Project

## Service Management
- **Restart:** `systemctl restart flightboard`
- **Status:** `systemctl status flightboard --no-pager`
- There is only ONE service file: `flightboard.service` (no hyphen)
- The Cloudflare tunnel (`cloudflared.service`) runs independently — only restart it if the tunnel itself is broken, not for app changes

## Runtime files — do NOT commit
- `data/traffic_stats.json` (replaced by PostgreSQL — gitignored)

## Committed config files (do NOT gitignore)
- `static/stands.json` — stand coordinates/radii, committed intentionally
- `static/data/theme_map.json` — theme registry, committed app config
- `data/custom_airports.json` — custom airport config, committed app config

## Project Structure
```
app.py                        # Flask app + Socket.IO
checkin_assignments.py        # Check-in desk logic (per airport)
vatsim_fetcher.py             # VATSIM data fetching + stand matching
templates/index.html          # Main FIDS page
templates/admin.html          # Admin panel
templates/admin_login.html    # Admin login page
templates/map.html            # Live map page
templates/gate.html           # Gate display page
static/css/style.css          # Global styles
static/css/themes/            # One CSS file per ICAO code
static/js/app.js              # Main frontend logic
static/js/map.js              # Live map logic (Leaflet + Socket.IO)
static/js/split_flap.js       # Solari split-flap animation (EDDF only)
static/js/flight_tracking.js  # Flight tracking module
static/js/language_handler.js # Multi-language support
static/js/gate.js             # Gate display logic
static/js/admin.js            # Admin panel logic
static/data/theme_map.json    # Runtime theme registry (also mirrored in app.js defaultThemeMap)
static/data/airport_names_ja.json  # Japanese airport name translations
static/logos/                 # Airline logos
data/                         # Runtime data (not committed)
scripts/                      # One-off utility scripts (not part of runtime)
```

## Configured Airports (v1.2.2)
| ICAO | Airport | Theme notes |
|------|---------|-------------|
| LSZH | Zurich | Star Alliance blue |
| LSGG | Geneva | — |
| LFSB | Basel EuroAirport | French/Swiss split |
| EGLL | London Heathrow | Lowercase text (isTitleCaseThemeActive) |
| EGKK | London Gatwick | — |
| EGSS | London Stansted | — |
| EGCC | Manchester | Yellow/black header, lowercase |
| EGLC | London City | — |
| EHAM | Amsterdam Schiphol | — |
| EDDF | Frankfurt | Solari split-flap animation |
| LFPG | Paris CDG | — |
| ESSA | Stockholm Arlanda | Inter font, lowercase, dark blue |
| KJFK | New York JFK | — |
| RJTT | Tokyo Haneda | Japanese bilingual footer |

## Adding a New Airport Theme
Each airport theme requires all **six** of these:
1. CSS file in `static/css/themes/<ICAO>.css`
2. Entry in `static/data/theme_map.json`: `"ICAO": { "css": "/static/css/themes/icao.css", "class": "theme-icao" }`
3. Entry in `defaultThemeMap` in `static/js/app.js` (fallback — `loadThemeMap` now merges API over defaults, so both are needed)
4. Entry in `DEFAULT_THEME_MAP` in `app.py`
5. Entry in `configured_airports` in `vatsim_fetcher.py`
6. `<option value="ICAO">Name</option>` in the `<select>` in `templates/index.html`

Optionally:
- Check-in desk logic in `checkin_assignments.py` → add method + route in `get_checkin_desk()`
- `"ICAO": "Display Name"` in the `manualRenames` dict in `app.js` for clean airport name display
- Per-airport gate label override in `gateLabelOverrides` in `language_handler.js` (e.g. ESSA forces "Gates" in English despite Swedish locale)

## Theme CSS Conventions
- Scope all rules with `body.theme-<icao>` to avoid bleed into other themes
- Status colours use `data-status` attribute on `.col-status` cell — text colour only (no background), with `!important` to beat global `style.css` specificity
- Gate column has class `col-gate` — use it for theme-specific gate cell styling
- Do NOT use `!important` on `.widget-icon` colour — it blocks CSS animations (the ATC radar-pulse needs to override the colour)
- To suppress `text-transform: uppercase` from `style.css` on table headers and status text, add `text-transform: none` to `body.theme-<icao> .flight-table th` and `body.theme-<icao> .col-status .flap-container`. Use `!important` if it doesn't take effect — theme CSS loaded dynamically can sometimes lose the cascade battle against `style.css` media query rules
- Table outer border: override `.split-panel { border-color: ... }` to blend into background (the rounded outline comes from `.split-panel`, not `.flight-table`)
- After editing a theme CSS file, **restart the service** (`systemctl restart flightboard`) so `asset_version` updates and Cloudflare serves the new file instead of a cached copy

## Check-in Desk Assignments (`checkin_assignments.py`)
- Each airport has a dedicated method (e.g. `_arlanda`, `_frankfurt`)
- Desk numbers are deterministic from the callsign character sum (`seed`)
- Some airports use flat desk **range** strings (e.g. ESSA: `"01-12"`, `"71-90"`) rather than a single desk number
- Add new airport: add method + add `elif airport_code == 'ICAO': return self._method(airline, seed)` in `get_checkin_desk()`

## Multi-language Footer (`language_handler.js`)
- Footer text (Arrivals, Departures, Gates, Security) auto-translates based on airport country
- `COUNTRY_CODE_TO_LANGUAGE` and `COUNTRY_TO_LANGUAGE` map country → language code
- `gateLabelOverrides` dict allows per-airport overrides for the Gates label only (e.g. `'ESSA': 'Gates'` keeps English despite Swedish locale)
- Swedish (`sv`) uses bilingual display (Swedish primary + English subtitle) — other languages may also be bilingual; check the `bilingual` flag in `/api/translations`

## Split-Flap Animation (EDDF only)
- `static/js/split_flap.js` exposes `window.SplitFlap.animateContainer(container, text)`
- Only activates when `body.theme-eddf` is present; all other themes get plain `textContent`
- `app.js` delegates both `updateFlapText()` and `updateStatusWithFade()` to it
- Animation CSS (`@keyframes sf-flip`) lives in `eddf.css`

## Stand Matching
- Priority 1 (UK airports only): UKCP API
- Priority 2: radius-only geofencing via `find_stand()` in `vatsim_fetcher.py`
- `static/stands.json` is the single source of truth for stand coordinates/radii
- Any airport with stands **must** have `has_stands: True` in `configured_airports` — otherwise geofencing is silently skipped
- Admin panel hot-reloads stands on save — no restart needed
- OSM stand import script: `scripts/import_osm_stands.py --icao <ICAO> --update-stands-json`

## Live Map (`map.js` + `templates/map.html`)
- Leaflet map at `/map/<ICAO>` — served by Flask, powered by Socket.IO
- OSM airport features (runways, taxiways, stand labels) fetched from Overpass API on page load
- Overpass response is **cached in sessionStorage** per airport (`flightboard.osm.<ICAO>`) — subsequent opens in the same browser session skip the API call
- `fitBounds` to route only fires when the tracked flight **is airborne** — ground/taxiing flights keep the airport zoom so taxiways remain visible
- Taxiways visible at zoom ≥ 13, stand labels at zoom ≥ 14

## PostgreSQL (Traffic Stats)
- Database at `10.29.29.139/flightboard`, credentials in `/etc/systemd/system/flightboard.service`
- 5 tables: `traffic_totals`, `traffic_daily`, `traffic_path_views`, `traffic_airport_joins`, `traffic_visitors`
- Tables auto-created by `_init_db()` on startup — DB errors don't crash the app
- `psycopg2` installed via `apt install python3-psycopg2` (system package)

## Asset Cache-Busting
- `asset_version` is a Unix timestamp injected server-side into every page render
- Exposed to JS as `window.ASSET_VERSION` via inline script in `index.html`
- All `<script>` and `<link>` tags use `v=asset_version` query param
- `updateTheme()` in `app.js` appends `?v=<ASSET_VERSION>` when loading theme CSS dynamically — prevents Cloudflare from serving stale CSS
