import { useEffect, useMemo, useRef, useState } from "react";
import MaritimeMap from "./components/MaritimeMap.jsx";
import { IncidentDetails } from "./components/IncidentDetails.jsx";
import { fetchIncident, hasCoordinates } from "./data/incidents.js";
import {
  countryRoles,
  createIncidentSearchIndex,
  facetLabels,
  incidentFacetOptions,
  matchingTitles,
  searchIncidentIndex,
  sortIncidentResults,
} from "./data/incidentSearch.js";
import { searchPlaces } from "./data/places.js";
import { useIncidents } from "./data/useIncidents.js";
import "./explorer.css";

const adminHost = import.meta.env.VITE_ADMIN_HOST?.trim();
const adminUrl = adminWorkspaceUrl();
const initialPageSize = 24;
const formatCount = new Intl.NumberFormat("en");
const incidentIdFromUrl = () => new URLSearchParams(window.location.search).get("incident");

function setIncidentLink(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("incident", id);
  else url.searchParams.delete("incident");
  window.history.replaceState(null, "", url);
}

function adminWorkspaceUrl() {
  if (!adminHost) return "/admin";
  try {
    const url = new URL("/admin", adminHost.includes("://") ? adminHost : `https://${adminHost}`);
    return ["https:", "http:"].includes(url.protocol) ? url.href : "/admin";
  } catch {
    return "/admin";
  }
}

function Facet({ field, options, selected, onChange }) {
  return (
    <label className="explorer-facet">
      <span>{facetLabels[field]}</span>
      <select value={selected ?? ""} onChange={(event) => onChange(field, event.target.value)}>
        <option value="">{{
          method: "All types",
          country: "All countries",
          year: "All years",
          victimType: "All victim types",
          impactArea: "All impact areas",
        }[field]}</option>
        {options.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
  );
}

function ExplorerDialog({ heading, onDismiss, children }) {
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current?.showModal();
    const element = dialog.current;
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="explorer-dialog"
      aria-labelledby="dialog-heading"
      onCancel={(event) => {
        event.preventDefault();
        onDismiss();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onDismiss();
      }}
    >
      <div className="explorer-dialog-body">
        <div className="explorer-dialog-heading">
          <h2 id="dialog-heading">{heading}</h2>
          <button type="button" onClick={onDismiss} autoFocus>Close</button>
        </div>
        {children}
      </div>
    </dialog>
  );
}

export function App() {
  const [mode, setMode] = useState("map");
  const [query, setQuery] = useState("");
  const [deferredQuery, setDeferredQuery] = useState("");
  const [filters, setFilters] = useState({});
  const [countryRole, setCountryRole] = useState("any");
  const [moreFilters, setMoreFilters] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [layer, setLayer] = useState("roadmap");
  const [infoOpen, setInfoOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const [shown, setShown] = useState(initialPageSize);
  const [sort, setSort] = useState("newest");
  const [mapWarning, setMapWarning] = useState("");
  const [mapReady, setMapReady] = useState(false);
  const [detailId, setDetailId] = useState(incidentIdFromUrl);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  const map = useRef(null);
  const mapTarget = useRef(null);
  const pendingMapMove = useRef(null);
  const searchBox = useRef(null);
  const searchInput = useRef(null);
  const data = useIncidents(reload);
  const searchIndex = useMemo(() => createIncidentSearchIndex(data.items), [data.items]);
  const facets = useMemo(() => incidentFacetOptions(searchIndex, countryRole), [searchIndex, countryRole]);
  const results = useMemo(() => searchIncidentIndex(searchIndex, deferredQuery, filters, countryRole),
    [searchIndex, deferredQuery, filters, countryRole]);
  const sorted = useMemo(() => sortIncidentResults(results, sort), [results, sort]);
  const mapped = useMemo(() => results.filter(hasCoordinates).length, [results]);

  const suggestions = useMemo(() => {
    if (query.trim().length < 2) return [];
    const filteredIds = new Set(searchIncidentIndex(searchIndex, "", filters, countryRole).map(({ id }) => id));
    return [
      ...matchingTitles(searchIndex, query, filteredIds).map((record) => ({
        type: "record", id: record.id, label: record.title, record,
      })),
      ...searchPlaces(query).slice(0, 3).map((place) => ({ ...place, type: "place" })),
    ].slice(0, 7);
  }, [searchIndex, query, filters, countryRole]);
  const suggestionsVisible = suggestionsOpen && suggestions.length > 0;
  const selected = detailId && detail?.id === detailId ? detail : null;
  const detailError = error?.id === detailId ? error.message : "";
  const loading = data.status === "loading";
  const failed = data.status === "error" && !data.lastSuccess;
  const tags = Object.entries(filters).filter(([, value]) => value);
  const hasFilters = Boolean(query || tags.length || countryRole !== "any");

  useEffect(() => {
    const timer = window.setTimeout(() => setDeferredQuery(query), 160);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const outside = (event) => {
      if (!searchBox.current?.contains(event.target)) {
        setSuggestionsOpen(false);
        setActiveSuggestion(-1);
      }
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);

  useEffect(() => {
    const sync = () => {
      const id = incidentIdFromUrl();
      if (id) {
        mapTarget.current = id;
        setInfoOpen(false);
      }
      setDetailId(id);
    };
    mapTarget.current = incidentIdFromUrl();
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (!detailId) return;
    const controller = new AbortController();
    setError(null);
    fetchIncident(detailId, { signal: controller.signal }).then(
      (record) => {
        if (!controller.signal.aborted) setDetail(record);
      },
      (failure) => {
        if (!controller.signal.aborted) setError({ id: detailId, message: failure.message });
      },
    );
    return () => controller.abort();
  }, [detailId]);

  useEffect(() => {
    if (mapReady && mapTarget.current && selected?.id === mapTarget.current && hasCoordinates(selected)) {
      map.current?.flyTo({ lat: selected.latitude, lng: selected.longitude, zoom: 9 });
      mapTarget.current = null;
    }
  }, [mapReady, selected]);

  useEffect(() => {
    if (!mapReady || mode !== "map" || !pendingMapMove.current) return;
    const target = pendingMapMove.current;
    const frame = window.requestAnimationFrame(() => {
      map.current?.flyTo(target);
      pendingMapMove.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [mode, mapReady]);

  function updateFilter(field, value) {
    setFilters((previous) => ({ ...previous, [field]: value }));
    setShown(initialPageSize);
  }

  function changeCountryRole(role) {
    setCountryRole(role);
    if (filters.country && !incidentFacetOptions(searchIndex, role).country.some(({ value }) => value === filters.country)) {
      updateFilter("country", "");
    }
    setShown(initialPageSize);
  }

  function resetFilters() {
    setQuery("");
    setDeferredQuery("");
    setFilters({});
    setCountryRole("any");
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
    setShown(initialPageSize);
  }

  function openIncident(incident) {
    setDetail(incident);
    setDetailId(incident.id);
    setSuggestionsOpen(false);
    setIncidentLink(incident.id);
  }

  function dismissIncident() {
    setDetailId(null);
    setDetail(null);
    setIncidentLink(null);
  }

  function selectSuggestion(item) {
    searchInput.current?.focus();
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
    if (item.type === "record") {
      openIncident(item.record);
      return;
    }
    setQuery("");
    setDeferredQuery("");
    moveMap(item);
  }

  function submitSearch(event) {
    event.preventDefault();
    if (suggestionsOpen && suggestions[activeSuggestion]) {
      selectSuggestion(suggestions[activeSuggestion]);
      return;
    }
    setDeferredQuery(query);
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
  }

  function handleSearchKeys(event) {
    if (event.key === "Escape") {
      setSuggestionsOpen(false);
      setActiveSuggestion(-1);
    }
    if (!suggestions.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestionsOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveSuggestion((current) => (
        current < 0 && step < 0 ? suggestions.length - 1 : (current + step + suggestions.length) % suggestions.length
      ));
    }
  }

  function showIncidentOnMap() {
    const target = { lat: selected.latitude, lng: selected.longitude, zoom: 9 };
    // An incident opened by link may not be in the current filtered result.
    if (!results.some(({ id }) => id === selected.id)) resetFilters();
    dismissIncident();
    moveMap(target);
  }

  function moveMap(target) {
    if (mode === "map" && mapReady) {
      map.current?.flyTo(target);
    } else {
      pendingMapMove.current = target;
      setMode("map");
    }
  }

  const facetControl = (field) => (
    <Facet key={field} field={field} options={facets[field]} selected={filters[field]} onChange={updateFilter} />
  );

  return (
    <div className="explorer">
      <a href="#incident-search" className="explorer-skip">Skip to incident search</a>
      <div role="banner" className="explorer-header">
        <div className="explorer-header-inner">
          <a className="explorer-brand" href="/" aria-label="MCAD home">
            <span aria-hidden="true" className="explorer-brand-name">MCAD</span>
            <span className="explorer-brand-caption">Maritime Cyber Attack Database</span>
          </a>
          <nav className="explorer-header-nav" aria-label="Main navigation">
            <button type="button" onClick={() => setInfoOpen(true)}>About</button>
            <a href={adminUrl}>Admin workspace <span aria-hidden="true">↗</span></a>
          </nav>
        </div>
      </div>

      <main className="explorer-body">
        <div className="explorer-intro">
          <h1>Incident explorer</h1>
          <div className="explorer-modes" role="group" aria-label="Incident view">
            {[
              ["map", "Map", "Map_Icon_Selected.png"],
              ["list", "List", "List_icon_selected.png"],
            ].map(([value, label, icon]) => (
              <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>
                <img src={`/assets/img/icons/${icon}`} alt="" />
                {label}
              </button>
            ))}
          </div>
        </div>

        <section className="explorer-controls" id="incident-search" aria-label="Incident search and filters">
          <div className="explorer-filters">
            <form className="explorer-search" role="search" ref={searchBox} onSubmit={submitSearch}>
              <label htmlFor="explorer-search">Search incidents</label>
              <div className="explorer-search-field">
                <img src="/assets/img/search.png" alt="" />
                <input
                  ref={searchInput}
                  type="search"
                  id="explorer-search"
                  role="combobox"
                  aria-autocomplete="list"
                  aria-expanded={suggestionsVisible}
                  aria-controls={suggestionsVisible ? "explorer-suggestions" : undefined}
                  aria-activedescendant={suggestionsVisible && suggestions[activeSuggestion] ? `explorer-suggestion-${activeSuggestion}` : undefined}
                  value={query}
                  placeholder="Title, location, organisation…"
                  autoComplete="off"
                  onFocus={() => setSuggestionsOpen(true)}
                  onBlur={(event) => {
                    if (!event.currentTarget.form?.contains(event.relatedTarget)) {
                      setSuggestionsOpen(false);
                      setActiveSuggestion(-1);
                    }
                  }}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setSuggestionsOpen(true);
                    setActiveSuggestion(-1);
                    setShown(initialPageSize);
                  }}
                  onKeyDown={handleSearchKeys}
                />
              </div>
              {suggestionsVisible && (
                <ul id="explorer-suggestions" role="listbox" aria-label="Search suggestions">
                  {suggestions.map((item, index) => (
                    <li key={`${item.type}-${item.id}`} role="presentation">
                      <button
                        type="button"
                        role="option"
                        id={`explorer-suggestion-${index}`}
                        aria-selected={index === activeSuggestion}
                        onClick={() => selectSuggestion(item)}
                      >
                        <span>{item.label}</span>
                        <small>{item.type === "record" ? "Incident" : "Go to map"}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </form>
            {["method", "country", "year"].map(facetControl)}
            <button
              type="button"
              className="explorer-more"
              aria-expanded={moreFilters}
              aria-controls="explorer-more-filters"
              onClick={() => setMoreFilters(!moreFilters)}
            >
              <img src="/assets/img/Funnel.png" alt="" />
              More filters
            </button>
          </div>

          <div className="explorer-advanced" id="explorer-more-filters" hidden={!moreFilters}>
            <label className="explorer-facet">
              <span>Country role</span>
              <select value={countryRole} onChange={(event) => changeCountryRole(event.target.value)}>
                {countryRoles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            {["victimType", "impactArea"].map(facetControl)}
          </div>

          <div className="explorer-result-bar">
            <div role="status" aria-live="polite" aria-atomic="true" className="explorer-result-count">
              {loading ? "Loading incidents…" : failed ? "Incidents unavailable" : (
                <>
                  <strong>{formatCount.format(results.length)}</strong> incident{results.length === 1 ? "" : "s"}
                  <span aria-hidden="true" className="explorer-result-divider">·</span>
                  <span>{formatCount.format(mapped)} mapped</span>
                  {results.length > mapped && (
                    <button type="button" onClick={() => setMode("list")} title="View all results, including unmapped records">
                      {formatCount.format(results.length - mapped)} unmapped
                    </button>
                  )}
                </>
              )}
            </div>
            {hasFilters && (
              <div className="explorer-filter-tags" aria-label="Selected filters">
                {tags.map(([field, value]) => {
                  const label = facets[field].find((option) => option.value === value)?.label ?? value;
                  return (
                    <button type="button" key={field} onClick={() => updateFilter(field, "")} aria-label={`Remove ${facetLabels[field]} filter: ${label}`}>
                      {label} <span aria-hidden="true">×</span>
                    </button>
                  );
                })}
                <button type="button" className="explorer-reset" onClick={resetFilters}>Reset</button>
              </div>
            )}
          </div>
        </section>

        {data.status === "error" && (
          <div role="alert" className="explorer-notice">
            <span>{data.lastSuccess ? "Live updates paused. Showing the last available records." : data.error || "The incident records could not be loaded."}</span>
            <button type="button" onClick={() => setReload((value) => value + 1)}>Retry</button>
          </div>
        )}

        <section className="explorer-map" aria-label="Map view" hidden={mode !== "map"}>
          <MaritimeMap
            ref={map}
            layer={layer}
            incidents={results}
            onError={setMapWarning}
            onSelectIncident={openIncident}
            onReady={() => setMapReady(true)}
          />
          <div className="explorer-map-tools" aria-label="Map controls">
            <button type="button" aria-label="Zoom in" onClick={() => map.current?.zoomIn()}>
              <img src="/assets/img/plus.svg" alt="" />
            </button>
            <button type="button" aria-label="Zoom out" onClick={() => map.current?.zoomOut()}>
              <img src="/assets/img/minus.svg" alt="" />
            </button>
            <button type="button" aria-label="Reset map view" onClick={() => map.current?.reset()}>Reset view</button>
            <button
              type="button"
              aria-pressed={layer === "satellite"}
              onClick={() => setLayer(layer === "roadmap" ? "satellite" : "roadmap")}
            >
              Satellite
            </button>
          </div>
          {!loading && !failed && mapped === 0 && (
            <div className="explorer-map-empty">
              {results.length ? (
                <>
                  <strong>{results.length === 1 ? "This incident is unmapped" : "These incidents are unmapped"}</strong>
                  <button type="button" onClick={() => setMode("list")}>Show in list</button>
                </>
              ) : (
                <>
                  <strong>{hasFilters ? "No incidents match" : "No published incidents"}</strong>
                  {hasFilters && <button type="button" onClick={resetFilters}>Reset filters</button>}
                </>
              )}
            </div>
          )}
          {mapWarning && (
            <div className="explorer-map-warning" role="status">
              <span>{mapWarning}</span>
              <button type="button" onClick={() => setMapWarning("")}>Dismiss</button>
            </div>
          )}
        </section>

        {mode === "list" && (
          <section className="explorer-results" aria-label="Incident list" aria-busy={loading}>
            {loading ? (
              <div className="explorer-empty">Loading incident records…</div>
            ) : !failed && sorted.length === 0 ? (
              <div className="explorer-empty">
                <strong>{hasFilters ? "No incidents match these filters" : "No published incidents yet"}</strong>
                {hasFilters && <button type="button" onClick={resetFilters}>Reset filters</button>}
              </div>
            ) : !failed && (
              <>
                <div className="explorer-list-header">
                  <span>Incident records</span>
                  <label>
                    <span>Sort by</span>
                    <select value={sort} onChange={(event) => { setSort(event.target.value); setShown(initialPageSize); }}>
                      <option value="newest">Newest first</option>
                      <option value="oldest">Oldest first</option>
                      <option value="title">Title A–Z</option>
                    </select>
                  </label>
                </div>
                <ul className="explorer-list">
                  {sorted.slice(0, shown).map((incident) => (
                    <li key={incident.id}>
                      <button type="button" onClick={() => openIncident(incident)}>
                        <span className="explorer-record-year">{incident.year || "—"}</span>
                        <span className="explorer-record-identity">
                          <strong>{incident.title}</strong>
                          <span>{[incident.incidentCountry || incident.victimCountry, incident.locationName].filter(Boolean).join(" · ") || "Location not recorded"}</span>
                        </span>
                        <span className="explorer-record-type">{incident.method || "Type not recorded"}</span>
                        {!hasCoordinates(incident) && <span className="explorer-unmapped">Unmapped</span>}
                        <span className="explorer-record-arrow" aria-hidden="true">↗</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {shown < sorted.length && (
                  <button className="explorer-show-more" type="button" onClick={() => setShown((count) => count + initialPageSize)}>
                    Show more <span>({Math.min(shown, sorted.length)} of {sorted.length})</span>
                  </button>
                )}
              </>
            )}
          </section>
        )}
      </main>

      {infoOpen && (
        <ExplorerDialog heading="About MCAD" onDismiss={() => setInfoOpen(false)}>
          <div className="explorer-about">
            <img src="/assets/img/logo.png" alt="NHL Stenden University of Applied Sciences" />
            <div>
              <h3>Maritime Cyber Attack Database</h3>
              <p>Explore published incidents using the map or list. Country filters can match the incident location, victim country or threat actor country.</p>
              <p>Incidents with missing coordinates appear in the list. Recorded location accuracy and original references are available in each incident.</p>
            </div>
          </div>
        </ExplorerDialog>
      )}
      {detailId && (
        <ExplorerDialog heading="Incident record" onDismiss={dismissIncident}>
          <IncidentDetails
            key={detailId}
            incident={selected}
            loading={!selected && !detailError}
            error={detailError}
            onShowMap={showIncidentOnMap}
            onCopyLink={typeof navigator.clipboard?.writeText === "function" ? () => navigator.clipboard.writeText(window.location.href) : undefined}
          />
        </ExplorerDialog>
      )}
    </div>
  );
}
