# Design QA — MCAD map frontend

Date: 2026-10-22

Current result: automated frontend checks and production build passed. Browser verification of the database-connected interface is tracked separately; the historical screenshots below do not verify the new integration. A successful Google Maps session has not been tested without an API key.

## Current implementation scope

- The source map header, typography, assets, navigation labels, filters and map controls are retained. The visual source is https://maritimecybersecurity.nl/.
- Published incidents load from the same-origin `/api`. The development proxy targets `http://127.0.0.1:8090`.
- The map clusters incidents with recorded coordinates; incidents without coordinates remain searchable in List. Record totals distinguish mapped and unmapped records. Coordinates are not invented from victim countries.
- List rows and marker popups open incident details, references, original source fields and import provenance. Source-coordinate accuracy is explicitly labelled approximate and not independently verified. Source formulas and markup are displayed as text; links accept HTTP(S) only.
- `?incident=<id>` links select a record and, when mapped, move to its coordinates. These links support the backend's Telegram notification format.
- The Report panel and `/admin` provide protected candidate approval, manual publishing and historical XLSX import. The user enters the bearer token for the session; it is not stored in local storage or bundled in the frontend. Historical import explicitly states that it does not send notifications.
- The data hook refreshes every 45 seconds while visible and on focus/visibility changes. It cancels superseded requests and retains the last successful snapshot on refresh failure, with an explicit stale/error state.
- Leaflet renders clusters within the visible bounds plus a 5% margin and updates after pan/zoom. Google uses its bounds and idle event. Both providers preserve a currently open visible popup when markers refresh. These interaction paths require browser QA; the production build verifies compilation only.

## Verification performed on 2026-10-22

| Check | Result and scope |
| --- | --- |
| `npm --prefix frontend test` | 21 checks passed, including five nested error cases. Tests use isolated fixtures and mocked requests. |
| `npm --prefix frontend run build` | Passed. Vite transformed 42 modules and prepared the required Sites packaging outputs. |
| `npm --prefix frontend run test:sites` | 4 checks passed: static assets, SPA route fallback, API/write exclusion from fallback, and packaging outputs. |
| Read-only local API integration | `/api/incidents` passed the real frontend validator: 440 records, 426 mapped and 14 unmapped. API metadata agrees with these counts. |
| Full-world cluster accounting | The real 440-record snapshot produced a cluster/point sum of 426, retaining every mapped record. Unmapped records remain in the validated dataset. |
| One real incident detail | The first record returned 33 original non-internal fields and 5 source links. No source content was copied into static build inputs. |
| Production artifact inspection | All 27 files inventoried; required `dist/client/index.html`, `dist/server/index.js` and `dist/.openai/hosting.json` are present. No workbook, CSV/TSV, database, environment, log, private-key or source-map files are present. Content scans found no database connection literals, Telegram token patterns, private-key material or test-fixture strings. |

The frontend data tests cover cancellation forwarding, HTTP/network/JSON/schema failures, identifier/year/coordinate validation, paired nullable coordinates, preserving original fields and public provenance, safe reference URL protocols, selected-field filtering, local place aliases, bearer authorization, actionable authorization errors, retaining distinct co-located records, and authenticated raw-binary workbook upload with file type/size checks. They do not mount React or exercise real map-provider browser events, Telegram delivery or production deployment.

The inspected build contains application JavaScript/CSS, original interface images/fonts, the app HTML and static deployment files. Incident records are requested at runtime; neither the supplied workbook nor local database files are bundled.

## Historical visual evidence

These files document the original map-only prototype comparison on 2026-10-21. They are retained as evidence for its interface fidelity, not as captures of the current incident, detail or admin views.

- Desktop source: `docs/qa/source-desktop-empty.png`, `docs/qa/source-desktop-filter.png`.
- Desktop prototype: `docs/qa/implementation-desktop.png`, `docs/qa/implementation-desktop-filter.png`, `docs/qa/implementation-desktop-menu.png`.
- Mobile source: `docs/qa/source-mobile.png`.
- Mobile prototype: `docs/qa/implementation-mobile.png`, `docs/qa/implementation-mobile-filter.png`.
- Comparisons: `docs/qa/comparison-desktop.png`, `docs/qa/comparison-filter.png`, `docs/qa/comparison-mobile.png`, `docs/qa/comparison-header.png`.
- Source CSS evidence: `docs/reference/original-main.css`, `docs/reference/original-map.css`.

Historical desktop captures use 1280 × 720 CSS pixels at 1× density; mobile captures use 390 × 844 at 1×. Contact sheets add a 28px caption strip. The original prototype desktop center was 49.240831, 12.627241 at zoom 5. Source records were hidden with its search control for the desktop comparison. Those captures predate database integration and depict an empty prototype map.

Previously resolved visual findings included map height, a tile provider returning an error graphic, mobile overflow, radio dimensions, selected-field autocomplete and map-layer attribution cleanup. They were documented in the prior report; no browser recapture is claimed by this automated verification pass.

## Remaining verification and limits

- Complete current desktop/mobile browser checks for real record rendering, marker/cluster navigation, filtering, popup-to-detail navigation, incident deep links, stale/error feedback, dialog focus and protected admin forms. Browser QA belongs to the coordinating task.
- Verify visible marker count and popup persistence after pan, zoom and resize in a real Leaflet session. Automated cluster accounting establishes data retention, not browser accessibility behavior.
- Google Maps initialization, markers, viewport refresh and fallback are implemented, but a successful Google path remains untested without the developer's own key/map ID. No source credentials were copied.
- OpenStreetMap and Esri cartography differ from the source's Google map. Source interface assets and CSS are retained independently of the tile provider.
- Local place search uses a small European gazetteer. Incident records may be worldwide; this lookup is not a global geocoder.
- Backend persistence, silent import transaction behavior, production deployment and actual Telegram delivery require their respective backend/deployment checks. The frontend's neutral save message does not claim that a notification was sent.
