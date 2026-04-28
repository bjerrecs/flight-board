// SimFixr FlightBoard — Simplified Board Display (streamer/OBS mode)
// Copyright (C) 2026 Tariq Mattar/SimFixr
// This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation.

document.addEventListener('DOMContentLoaded', () => {
    const BOARD_AIRPORT    = window.BOARD_AIRPORT || '';
    const PAGE_INTERVAL_MS = 8000;

    // --- GLOBAL LOGO FALLBACK (used inline via onerror="handleLogoError(this)") ---
    window.handleLogoError = function(img) {
        const attempt = parseInt(img.dataset.attempt || '0');
        if (attempt === 0 && img.dataset.secondary) {
            img.dataset.attempt = '1';
            img.src = img.dataset.secondary;
        } else if (attempt === 1 && img.dataset.tertiary) {
            img.dataset.attempt = '2';
            img.src = img.dataset.tertiary;
        } else {
            img.style.display = 'none';
        }
    };

    // --- AIRLINE MAPPING (ICAO prefix → IATA logo code) ---
    const airlineMapping = {
        'SWS': 'LX', 'EZY': 'U2', 'EJU': 'U2', 'EZS': 'DS', 'BEL': 'SN',
        'GWI': '4U', 'EDW': 'WK', 'ITY': 'AZ', 'FDX': 'FX', 'UPS': '5X',
        'GEC': 'LH', 'BCS': 'QY', 'SAZ': 'REGA', 'SHT': 'BA'
    };
    const airlineLogoAliasGroups = {
        BA: ['SHT', 'EFW'],
        W6: ['WAU', 'WAZ', 'WIZ', 'WMT', 'WUK', 'WVL', 'WZZ']
    };
    const airlineLogoAliases = Object.entries(airlineLogoAliasGroups).reduce((acc, [logoCode, prefixes]) => {
        prefixes.forEach(p => { acc[p.toUpperCase()] = logoCode; });
        return acc;
    }, {});
    const virtualAirlines = new Set(['XNO']);
    const airportMapping  = {};

    // --- DOM ELEMENTS ---
    const depList = document.getElementById('departureList');
    const arrList = document.getElementById('arrivalList');
    const nameEl  = document.getElementById('boardAirportName');
    const clockEl = document.getElementById('boardClock');

    // --- UTC CLOCK ---
    function updateClock() {
        if (clockEl) {
            clockEl.textContent = new Date().toLocaleTimeString('en-GB', { timeZone: 'UTC', hour12: false });
        }
    }
    updateClock();
    setInterval(updateClock, 1000);

    // --- PAGINATION ENGINE ---
    const paginationState = {
        dep: { page: 0, rowsPerPage: 1, totalPages: 1, intervalId: null },
        arr: { page: 0, rowsPerPage: 1, totalPages: 1, intervalId: null }
    };

    function applyPagination(type, recompute = true) {
        const state     = paginationState[type];
        const container = type === 'dep' ? depList : arrList;
        if (!container) return;

        const scrollArea = container.closest('.table-scroll-area');
        const table      = scrollArea ? scrollArea.querySelector('table') : null;
        const header     = table ? table.querySelector('thead') : null;
        const rows       = Array.from(container.children);

        if (recompute && scrollArea) {
            const headerHeight = header ? header.offsetHeight : 0;
            let rowHeight      = rows[0]?.offsetHeight;
            if (!rowHeight) {
                const css = getComputedStyle(document.documentElement).getPropertyValue('--row-height');
                rowHeight = parseFloat(css) || 42;
            }
            const available     = Math.max(0, scrollArea.clientHeight - headerHeight);
            state.rowsPerPage   = Math.max(1, Math.floor(available / rowHeight));
            state.totalPages    = rows.length ? Math.ceil(rows.length / state.rowsPerPage) : 1;
        }

        if (state.page >= state.totalPages) state.page = 0;
        const start = state.page * state.rowsPerPage;
        const end   = start + state.rowsPerPage;
        rows.forEach((row, idx) => { row.style.display = (idx >= start && idx < end) ? '' : 'none'; });

        const indicator = document.getElementById(type === 'dep' ? 'depPageInd' : 'arrPageInd');
        if (indicator) {
            if (state.totalPages > 1) {
                indicator.textContent  = `${state.page + 1} of ${state.totalPages}`;
                indicator.style.display = 'inline';
            } else {
                indicator.textContent  = '';
                indicator.style.display = 'none';
            }
        }

        if (state.totalPages > 1) {
            if (!state.intervalId) {
                state.intervalId = setInterval(() => {
                    const s = paginationState[type];
                    if (s.totalPages <= 1) return;
                    s.page = (s.page + 1) % s.totalPages;
                    applyPagination(type, false);
                }, PAGE_INTERVAL_MS);
            }
        } else if (state.intervalId) {
            clearInterval(state.intervalId);
            state.intervalId = null;
        }
    }

    (function initPaginationObservers() {
        document.querySelectorAll('.table-scroll-area').forEach(area => {
            const type = area.querySelector('#departureList') ? 'dep'
                : area.querySelector('#arrivalList') ? 'arr'
                : null;
            if (!type) return;
            if ('ResizeObserver' in window) {
                new ResizeObserver(() => applyPagination(type, true)).observe(area);
            }
        });
        if (!('ResizeObserver' in window)) {
            window.addEventListener('resize', () => {
                applyPagination('dep', true);
                applyPagination('arr', true);
            });
        }
    })();

    // --- STATUS FLIP ENGINE (alternates between status text and delay/boarding text) ---
    let showingDelayPhase = false;
    setInterval(() => {
        showingDelayPhase = !showingDelayPhase;
        document.querySelectorAll('.col-status[data-has-delay="true"], .col-status[data-is-boarding="true"]').forEach(cell => {
            const flap      = cell.querySelector('.flap-container');
            if (!flap) return;
            const hasDelay  = cell.getAttribute('data-has-delay') === 'true';
            const isBoarding = cell.getAttribute('data-is-boarding') === 'true';
            const normal    = cell.getAttribute('data-status-normal') || '';
            const delayText = cell.getAttribute('data-status-delay') || '';

            let newText, newColorClass;
            if (showingDelayPhase) {
                if (hasDelay) {
                    newText       = delayText.toUpperCase();
                    newColorClass = 'Delayed';
                } else if (isBoarding) {
                    newText       = 'GO TO GATE';
                    newColorClass = 'GO TO GATE';
                } else {
                    return;
                }
            } else {
                newText       = normal.toUpperCase();
                newColorClass = normal;
            }
            applyStatusFade(flap, cell, newText, newColorClass);
        });
    }, 5000);

    function applyStatusFade(flap, statusCell, newText, newColorClass) {
        flap.classList.add('status-updating');
        setTimeout(() => {
            flap.textContent = newText;
            statusCell.setAttribute('data-status', newColorClass);
            flap.classList.remove('status-updating');
        }, 175);
    }

    // --- ROW RENDERING ---
    function renderFlights(flights, container, type) {
        if (!container) return;
        const existingRows = Array.from(container.children);
        const seenIds      = new Set();

        flights.forEach(flight => {
            const safeCallsign = String(flight.callsign || '').trim().toUpperCase();
            const rowId        = `row-${type === 'Departures' ? 'dep' : 'arr'}-${safeCallsign}`;
            seenIds.add(rowId);
            let row = document.getElementById(rowId);

            // Determine logo URLs
            const prefix    = safeCallsign.substring(0, 3).toUpperCase();
            const code      = airlineLogoAliases[prefix] || airlineMapping[prefix] || prefix;
            const localOnly = ['FX', 'FDX', 'UPS', '5X', 'REGA', 'SAZ'];
            let primaryLogo, secondaryLogo, tertiaryLogo;
            if (virtualAirlines.has(prefix)) {
                primaryLogo   = `/static/logos/${prefix}.png`;
                secondaryLogo = '';
                tertiaryLogo  = '';
            } else if (localOnly.includes(code)) {
                primaryLogo   = `/static/logos/${code}.png`;
                secondaryLogo = `https://images.kiwi.com/airlines/64/${code}.png`;
                tertiaryLogo  = `https://content.r9cdn.net/rimg/provider-logos/airlines/v/${code}.png`;
            } else {
                primaryLogo   = `https://images.kiwi.com/airlines/64/${code}.png`;
                secondaryLogo = `https://content.r9cdn.net/rimg/provider-logos/airlines/v/${code}.png`;
                tertiaryLogo  = `/static/logos/${code}.png`;
            }

            const destIcao = type === 'Arrivals' ? flight.origin : flight.destination;
            const destName = airportMapping[destIcao]?.name || destIcao;
            const timeStr  = flight.time_display || '--:--';

            // Gate logic
            let gate         = flight.gate || 'TBA';
            let isGateWaiting = false;
            if (type === 'Departures') {
                if (flight.status === 'Taxiing' || flight.status === 'Departing') gate = 'CLOSED';
            } else {
                if ((!gate || gate === 'TBA') && (flight.status === 'Landed' || flight.status === 'Landing')) {
                    gate = 'WAIT';
                    isGateWaiting = true;
                }
            }

            const canShowDelay = ['Boarding', 'Check-in', 'Pushback', 'Taxiing', 'Departing', 'Approaching', 'Landing'].includes(flight.status);
            const hasDelay     = !!(flight.delay_text && canShowDelay);
            const isBoarding   = flight.status === 'Boarding' && gate && gate !== 'TBA' && gate !== 'CLOSED';

            // Create row on first render
            if (!row) {
                row = document.createElement('tr');
                row.id = rowId;
                const commonCells = `
                    <td>
                        <div class="flight-cell" id="${rowId}-cell">
                            ${type === 'Departures' ? '<span class="boarding-lights"></span>' : ''}
                            <img src="${primaryLogo}"
                                 data-primary="${primaryLogo}"
                                 data-secondary="${secondaryLogo}"
                                 data-tertiary="${tertiaryLogo}"
                                 class="airline-logo"
                                 style="filter: none;"
                                 onerror="handleLogoError(this)">
                            <div class="flap-container" id="${rowId}-callsign"></div>
                        </div>
                    </td>
                    <td><div class="flap-container flap-dest" id="${rowId}-dest"></div></td>
                    <td><div class="flap-container" id="${rowId}-ac"></div></td>`;

                if (type === 'Departures') {
                    row.innerHTML = commonCells + `
                        <td class="col-checkin"><div class="flap-container" id="${rowId}-checkin"></div></td>
                        <td class="col-gate"><div class="flap-container" id="${rowId}-gate"></div></td>
                        <td><div class="flap-container" id="${rowId}-time"></div></td>
                        <td class="col-status"><div class="flap-container" id="${rowId}-status"></div></td>`;
                } else {
                    row.innerHTML = commonCells + `
                        <td></td>
                        <td class="col-gate"><div class="flap-container" id="${rowId}-gate"></div></td>
                        <td><div class="flap-container" id="${rowId}-time"></div></td>
                        <td class="col-status"><div class="flap-container" id="${rowId}-status"></div></td>`;
                }
                container.appendChild(row);
            }

            row.setAttribute('data-callsign',           safeCallsign);
            row.setAttribute('data-track-origin',       String(flight.origin      || ''));
            row.setAttribute('data-track-destination',  String(flight.destination || ''));

            const flightCellRef = document.getElementById(`${rowId}-cell`);
            if (flightCellRef) flightCellRef.setAttribute('data-route', flight.route || 'No route');

            const setText = (id, text) => {
                const el = document.getElementById(id);
                if (el) el.textContent = String(text || '');
            };

            setText(`${rowId}-callsign`, flight.callsign);
            setText(`${rowId}-dest`, destName);
            setText(`${rowId}-ac`, flight.aircraft);

            // Time
            const timeFlap   = document.getElementById(`${rowId}-time`);
            const isActualDep = type === 'Departures' && flight.status === 'Departing' && flight.actual_dep_time;
            const isActualArr = type === 'Arrivals' && (flight.status === 'Landed' || flight.status === 'At Gate') && flight.actual_arr_time;
            if (timeFlap) {
                if (isActualDep) {
                    timeFlap.textContent = flight.actual_dep_time;
                    timeFlap.classList.add('time-actual-dep');
                    timeFlap.classList.remove('time-actual-arr');
                } else if (isActualArr) {
                    timeFlap.textContent = flight.actual_arr_time;
                    timeFlap.classList.add('time-actual-arr');
                    timeFlap.classList.remove('time-actual-dep');
                } else {
                    timeFlap.textContent = timeStr;
                    timeFlap.classList.remove('time-actual-dep', 'time-actual-arr');
                }
            }

            // Check-in
            const checkinFlap = document.getElementById(`${rowId}-checkin`);
            if (checkinFlap) {
                checkinFlap.textContent = flight.checkin || '';
                checkinFlap.classList.toggle('gate-closed', flight.checkin === 'CLOSED');
            }

            // Gate
            const gateEl = document.getElementById(`${rowId}-gate`);
            if (gateEl) {
                gateEl.textContent = gate;
                gateEl.classList.toggle('status-wait', isGateWaiting);
                gateEl.classList.toggle('gate-closed', !isGateWaiting && gate === 'CLOSED');
            }

            // Status
            const statusCell = row.querySelector('.col-status');
            const statusFlap = document.getElementById(`${rowId}-status`);
            if (statusCell && statusFlap) {
                const flightCell = document.getElementById(`${rowId}-cell`);
                if (flightCell) flightCell.classList.toggle('is-boarding', isBoarding);

                statusCell.setAttribute('data-has-delay',     hasDelay   ? 'true' : 'false');
                statusCell.setAttribute('data-is-boarding',   isBoarding ? 'true' : 'false');
                statusCell.setAttribute('data-gate',          gate);
                statusCell.setAttribute('data-status-normal', flight.status);
                statusCell.setAttribute('data-status-delay',  flight.delay_text || '');

                let displayStatus     = flight.status;
                let displayColorClass = flight.status;
                if (showingDelayPhase) {
                    if (hasDelay)    { displayStatus = flight.delay_text; displayColorClass = 'Delayed'; }
                    else if (isBoarding) { displayStatus = 'GO TO GATE'; displayColorClass = 'GO TO GATE'; }
                }
                const formatted = String(displayStatus || '').toUpperCase();
                if (statusFlap.textContent !== formatted) {
                    applyStatusFade(statusFlap, statusCell, formatted, displayColorClass);
                } else {
                    statusCell.setAttribute('data-status', displayColorClass);
                }
            }

            // Maintain server-side sort order
            container.appendChild(row);
        });

        existingRows.forEach(r => { if (!seenIds.has(r.id)) r.remove(); });
    }

    // --- SOCKET ---
    const socket = io({ auth: { token: window._socketToken || '' } });

    socket.on('connect', () => {
        socket.emit('join_airport', { airport: BOARD_AIRPORT, explicit: true });
    });

    socket.on('flight_update', (data) => {
        if (data.airport_name && nameEl) nameEl.textContent = data.airport_name;
        renderFlights(data.departures || [], depList, 'Departures');
        renderFlights(data.arrivals   || [], arrList, 'Arrivals');
        applyPagination('dep', true);
        applyPagination('arr', true);
    });

    // --- AIRPORT & AIRLINE DATABASE (for destination name and logo lookups) ---
    async function loadDatabases() {
        try {
            const r = await fetch('https://cdn.jsdelivr.net/gh/npow/airline-codes@master/airlines.json');
            if (r.ok) {
                const data = await r.json();
                data.forEach(a => {
                    if (a.icao && a.iata && a.active === 'Y' && !airlineMapping[a.icao]) {
                        airlineMapping[a.icao] = a.iata;
                    }
                });
            }
        } catch (e) { /* non-critical */ }

        try {
            const r = await fetch('https://raw.githubusercontent.com/mwgg/Airports/master/airports.json');
            if (r.ok) {
                const data = await r.json();
                const manualRenames = {
                    'EGGW': 'London Luton',      'KLGA': 'New York LaGuardia',
                    'LFPO': 'Paris Orly',        'EDDM': 'Munich',
                    'OMDB': 'Dubai',             'VHHH': 'Hong Kong',
                    'WSSS': 'Singapore',         'KBOS': 'Boston',
                    'LLBG': 'Tel Aviv',          'LSHD': 'Zurich Heliport',
                    'LIBG': 'Taranto-Grottaglie'
                };
                for (const [icao, details] of Object.entries(data)) {
                    let name;
                    if (manualRenames[icao])   name = manualRenames[icao];
                    else if (details.city)     name = details.city;
                    else                       name = details.name;
                    airportMapping[icao] = {
                        name: name
                            .replace(/\b(Airport|International|Intl|Field|Airfield)\b/g, '')
                            .replace(/\s+/g, ' ')
                            .trim()
                    };
                }
            }
        } catch (e) { /* non-critical */ }
    }
    loadDatabases();
});
