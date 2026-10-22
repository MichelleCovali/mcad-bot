# Design QA — MCAD map frontend

Date: 2026-10-21

final result: passed

Passed for the requested map frontend and the documented provider fallback. This is not a claim that OpenStreetMap has the same cartography as Google Maps, or that the original site's other pages have been cloned.

## Evidence

- Visual source: https://maritimecybersecurity.nl/
- Desktop source: `docs/qa/source-desktop-empty.png`, `docs/qa/source-desktop-filter.png`.
- Desktop implementation: `docs/qa/implementation-desktop.png`, `docs/qa/implementation-desktop-filter.png`, `docs/qa/implementation-desktop-menu.png`.
- Mobile source: `docs/qa/source-mobile.png`.
- Mobile implementation: `docs/qa/implementation-mobile.png`, `docs/qa/implementation-mobile-filter.png`.
- Full comparisons: `docs/qa/comparison-desktop.png`, `docs/qa/comparison-filter.png`, `docs/qa/comparison-mobile.png`.
- Focused comparison: `docs/qa/comparison-header.png` contains both source and implementation headers at original scale.
- CSS evidence: `docs/reference/original-main.css`, `docs/reference/original-map.css`.

Desktop viewport and source/implementation raster: **1280 × 720 CSS px / 1280 × 720 pixels**. Mobile viewport and raster: **390 × 844 CSS px / 390 × 844 pixels**. Capture density is 1×; no scaling was used for individual screenshots. Contact sheets concatenate equal-density captures with a 28px caption strip.

Desktop state: initial center 49.240831, 12.627241, zoom 5, empty search, no visible incident records. The source's records were hidden using its search control; data were not copied. Mobile source capture contains its original markers; those are intentionally absent from the implementation. The source's loaded mobile map obscured its search/navigation; the implementation keeps the source's intended responsive search and bottom navigation usable. Mobile is therefore a comparison of responsive structure and controls with these explicit content/stacking differences.

## Required fidelity surfaces

| Surface | Result |
| --- | --- |
| Typography | Original local Cera Round Pro 400/500/700. Header title, placeholder, filter text, sizes and desktop wrapping match the focused comparison. |
| Spacing/layout | Source 100px desktop header, 31px horizontal padding, 76px logo, 50px controls, 32px desktop right margins, 225px filter, 171px navigation preserved. Mobile search margins are 25px, persistent bottom navigation matches source CSS, horizontal overflow absent at 390px. |
| Colors/tokens | Source navy #282369 / #00294b, blue #005aaa, search #f2f7fb and selected filter #e62328 preserved. Cartographic colors differ by provider as documented. |
| Images/icons | Original logo, search/filter/menu/zoom/layer/boat assets and favicons bundled locally. No substitute icon drawings or hotlinked source assets. Map providers supply runtime tiles with visible attribution. |
| Copy/content | Main map title, controls and seven filter labels preserved. Real incidents omitted as requested. List, Info and Report are scoped local preview panels, not reproductions of source pages. |

## Comparison history and resolved findings

1. **[P1] Zero-height map after initial integration.** The source CSS targeted `div#map`, while the semantic implementation uses `main#map`. Added an explicit `.map-stage` height; desktop map and all controls now occupy the source's proportions. Verified in the final desktop contact sheet.
2. **[P1] Provider returned an error graphic with HTTP 200.** Browser capture exposed CARTO's API-key watermark. Replaced it with standard OpenStreetMap tiles and visually verified real map pixels. Final full-view comparisons use the working fallback.
3. **[P2] Mobile hidden logo extended layout beyond viewport.** Increased selector specificity to remove the desktop brand group below 769px. Final mobile DOM reports `scrollWidth === innerWidth === 390`.
4. **[P2] Mobile radio controls became elliptical.** Source-wide mobile input padding affected radio buttons. Scoped padding reset restores 19px circles; verified in `implementation-mobile-filter.png`.
5. **[P2] Field autocomplete applied record titles to year/country filters.** Suggestions now use the selected field value; country navigation retains the country filter. Temporary API fixture verified Year=2024 and Victim country=Netherlands each retain only the matching record.
6. **[P2] Layer attribution survived tile-layer removal.** Narrowed event cleanup to the app's tile-error listener, preserving Leaflet's attribution cleanup. Roadmap now shows OpenStreetMap attribution; satellite shows the imagery provider attribution.

After these fixes the screenshots were captured again and compared together. No actionable P0/P1/P2 findings remain within the stated scope.

## Interaction and runtime verification

- Desktop navigation open/close; List view shows 0 records in the normal preview.
- Local Info panel, modal focus, close button and Escape; Report explicitly indicates preview unavailability and sends nothing.
- Seven radio filters, outside-click dismissal and keyboard focus.
- Local country/city search, selection by ArrowDown/Enter, unknown-result state and clear action.
- Escape removes a stale active suggestion reference.
- Zoom in/out, drag to pan, reset through an empty search and switch between roadmap/satellite; actual imagery inspected in the browser.
- Mobile 390 × 844 controls, filter panel, bottom navigation, Info close/Escape, no horizontal overflow.
- Default empty data mode verified in the browser.
- Isolated preview on port 4174 connected to a temporary API on port 4175. Two synthetic records loaded as markers; popup content, year filtering and country selection verified. Fixture data were never included in app defaults or an environment file.
- Browser warning/error logs were empty in the normal and fixture previews.
- Final normal desktop DOM: 1280 × 720, zero incident markers, 27 loaded tiles, no horizontal overflow. Temporary fixture servers were stopped; only the normal preview remains running.
- Production build, 13 data-client/filter checks and 4 static-worker checks pass.

## Expected differences and limits

- Exact Google cartography requires the developer's own Google Maps API key and, where appropriate, a configured map ID. No source credentials were copied. Google initialization/fallback is implemented but the successful Google path was not exercised without a key.
- OpenStreetMap and Esri tile labels, colors, detail and imagery differ from the source. The UI geometry/assets match independently of this configured-provider difference.
- Full incident pages, source List/Info/Report pages and report submission are outside this map-only implementation.
- Future server persistence and production database integration remain separate work. The frontend API boundary is implemented and tested.

## Follow-up polish

- [P3] The source capture's selected radio appears slightly lighter than its CSS token; implementation follows the actual #e62328 token.
- [P3] Local search intentionally covers a small European gazetteer. A future geocoder can extend place coverage without changing the map UI.

## Implementation checklist

- [x] Bundle original interface assets and fonts.
- [x] Keep default map free of incident records.
- [x] Implement map controls, search and filters.
- [x] Isolate validated API access for future database data.
- [x] Compare source and rendered implementation together.
- [x] Verify desktop/mobile and temporary API integration.
- [x] Keep the normal local preview running.
