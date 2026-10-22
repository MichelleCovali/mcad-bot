import { useEffect, useMemo, useRef, useState } from "react";
import MaritimeMap from "./components/MaritimeMap.jsx";
import { filterIncidents, listIncidents } from "./data/incidents.js";
import { searchPlaces } from "./data/places.js";

const title = "MCAD Maritime Cyber Attack Database";
const countryFilters = ["victimCountry", "incidentCountry", "attackerCountry"];
const filters = [
  ["all", "All"],
  ["victimCountry", "Victim country"],
  ["year", "Year"],
  ["victimType", "Victim type"],
  ["impactArea", "Impact area"],
  ["incidentCountry", "Incident country"],
  ["attackerCountry", "Attacker country"],
];
const navigation = [
  ["map", "Map"],
  ["list", "List view"],
  ["info", "Info"],
  ["report", "Report"],
];

export function App() {
  const map = useRef(null);
  const searchInput = useRef(null);
  const searchShell = useRef(null);
  const navigationButton = useRef(null);
  const filterButton = useRef(null);
  const closeButton = useRef(null);
  const returnFocus = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [layer, setLayer] = useState("roadmap");
  const [panel, setPanel] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [dataState, setDataState] = useState("loading");
  const [dataError, setDataError] = useState("");
  const [mapWarning, setMapWarning] = useState("");
  const [reload, setReload] = useState(0);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 768px)").matches,
  );

  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const change = () => setMobile(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDataState("loading");
    setDataError("");
    listIncidents({ signal: controller.signal }).then(
      (records) => {
        if (controller.signal.aborted) return;
        setIncidents(records);
        setDataState("ready");
      },
      (error) => {
        if (controller.signal.aborted) return;
        setIncidents([]);
        setDataError(
          error instanceof Error
            ? error.message
            : "Records could not be loaded.",
        );
        setDataState("error");
      },
    );
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    function dismiss(event) {
      if (!searchShell.current?.contains(event.target)) {
        setFiltersOpen(false);
        setSuggestionsOpen(false);
        setActiveSuggestion(-1);
      }
    }
    function escape(event) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      setFiltersOpen(false);
      setSuggestionsOpen(false);
      setActiveSuggestion(-1);
      setPanel(null);
      if (event.target.closest?.("#search-filters"))
        filterButton.current?.focus();
      else if (event.target.closest?.("#navigation"))
        navigationButton.current?.focus();
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  useEffect(() => {
    if (panel) closeButton.current?.focus();
    else returnFocus.current?.focus();
  }, [panel]);

  const visibleIncidents = useMemo(
    () => filterIncidents(incidents, appliedQuery, filter),
    [incidents, appliedQuery, filter],
  );
  const suggestions = useMemo(() => {
    if (query.trim().length < 2) return [];
    const places =
      filter === "all" || countryFilters.includes(filter)
        ? searchPlaces(query)
            .filter(
              (place) =>
                filter === "all" ||
                (!place.label.includes(",") && place.id !== "europe"),
            )
            .map((place) => ({ ...place, kind: "place" }))
        : [];
    const seenLabels = new Set(places.map(({ label }) => label.toLowerCase()));
    const records = filterIncidents(incidents, query, filter)
      .map((record) => ({
        id: record.id,
        label: filter === "all" ? record.title : String(record[filter] ?? ""),
        lat: record.latitude,
        lng: record.longitude,
        zoom: 9,
        kind: filter === "all" ? "record" : "value",
      }))
      .filter(({ label }) => {
        const normalized = label.toLowerCase();
        if (!label || seenLabels.has(normalized)) return false;
        seenLabels.add(normalized);
        return true;
      })
      .slice(0, 5);
    return (
      filter === "all" ? [...records, ...places] : [...places, ...records]
    ).slice(0, 6);
  }, [query, filter, incidents]);

  function selectSuggestion(suggestion) {
    setQuery(suggestion.label);
    setActiveSuggestion(-1);
    setAppliedQuery(
      suggestion.kind === "place" && filter === "all" ? "" : suggestion.label,
    );
    map.current?.flyTo(suggestion);
    searchInput.current?.focus();
    setSuggestionsOpen(false);
  }
  function search(event) {
    event.preventDefault();
    const selected =
      suggestionsOpen && activeSuggestion >= 0
        ? suggestions[activeSuggestion]
        : null;
    if (selected && query.trim()) return selectSuggestion(selected);
    // All can navigate the basemap. Scoped fields always retain their search term.
    const place =
      filter === "all" &&
      suggestions.find((suggestion) => suggestion.kind === "place");
    if (place && query.trim()) return selectSuggestion(place);
    setAppliedQuery(query);
    setActiveSuggestion(-1);
    setSuggestionsOpen(Boolean(query.trim()));
    if (!query.trim()) map.current?.reset();
  }
  function openPanel(event, destination) {
    event.preventDefault();
    setMenuOpen(false);
    setFiltersOpen(false);
    setSuggestionsOpen(false);
    returnFocus.current = event.currentTarget;
    setPanel(destination === "map" ? null : destination);
  }
  function clearSearch() {
    setQuery("");
    setAppliedQuery("");
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
    searchInput.current?.focus();
  }

  return (
    <>
      <header inert={panel ? true : undefined}>
        <div className="nav bar">
          <div className="search-shell" ref={searchShell}>
            <button
              type="button"
              ref={navigationButton}
              className={`hamburger ${menuOpen ? "opened" : ""}`}
              aria-label={menuOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={menuOpen}
              aria-controls="navigation"
              onClick={() => {
                setMenuOpen(!menuOpen);
                setFiltersOpen(false);
                setSuggestionsOpen(false);
                setActiveSuggestion(-1);
              }}
            >
              <span />
              <span />
              <span />
            </button>
            <form className="searchbar" role="search" onSubmit={search}>
              <input
                ref={searchInput}
                type="search"
                id="map-search"
                aria-label="Search in the map"
                placeholder="Search in the map…"
                autoComplete="off"
                role="combobox"
                aria-autocomplete="list"
                aria-expanded={suggestionsOpen && query.trim().length >= 2}
                aria-controls="search-results"
                aria-activedescendant={
                  activeSuggestion >= 0 &&
                  suggestionsOpen &&
                  activeSuggestion < suggestions.length
                    ? `suggestion-${activeSuggestion}`
                    : undefined
                }
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setSuggestionsOpen(true);
                  setFiltersOpen(false);
                  setActiveSuggestion(-1);
                  if (!event.target.value) setAppliedQuery("");
                }}
                onFocus={() =>
                  query.trim().length >= 2 && setSuggestionsOpen(true)
                }
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" && suggestions.length) {
                    event.preventDefault();
                    setSuggestionsOpen(true);
                    setActiveSuggestion((i) =>
                      Math.min(i + 1, suggestions.length - 1),
                    );
                  }
                  if (event.key === "ArrowUp" && suggestions.length) {
                    event.preventDefault();
                    setSuggestionsOpen(true);
                    setActiveSuggestion((i) =>
                      i < 0 ? suggestions.length - 1 : Math.max(i - 1, 0),
                    );
                  }
                }}
              />
              <button
                className="search"
                type="submit"
                aria-label="Search"
                title="Search"
              />
              <button
                className="filter"
                ref={filterButton}
                type="button"
                aria-label="Search filters"
                aria-expanded={filtersOpen}
                aria-controls="search-filters"
                title="Search filters"
                onClick={() => {
                  setFiltersOpen(!filtersOpen);
                  setMenuOpen(false);
                  setSuggestionsOpen(false);
                  setActiveSuggestion(-1);
                }}
              />
              <div
                className={`filters ${filtersOpen ? "opened" : ""}`}
                id="search-filters"
                hidden={!filtersOpen}
              >
                <h3>Search filter</h3>
                {filters.map(([value, label]) => (
                  <label key={value}>
                    <input
                      type="radio"
                      name="search-filter"
                      value={value}
                      checked={filter === value}
                      onChange={() => {
                        setFilter(value);
                        setActiveSuggestion(-1);
                        setSuggestionsOpen(false);
                      }}
                    />
                    <p>{label}</p>
                  </label>
                ))}
              </div>
              {suggestionsOpen && query.trim().length >= 2 && (
                <ul
                  className="typeahead"
                  id="search-results"
                  role="listbox"
                  aria-label="Search results"
                >
                  {suggestions.map((suggestion, index) => (
                    <li
                      key={`${suggestion.kind}-${suggestion.id}`}
                      role="presentation"
                    >
                      <button
                        id={`suggestion-${index}`}
                        type="button"
                        role="option"
                        aria-selected={index === activeSuggestion}
                        onClick={() => selectSuggestion(suggestion)}
                      >
                        {suggestion.label}
                      </button>
                    </li>
                  ))}
                  {!suggestions.length && (
                    <li className="no-results" role="presentation">
                      <p role="status">No results found</p>
                      <button type="button" onClick={clearSearch}>
                        Clear search
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </form>
          </div>
          <div className="logo">
            <h2 className="hidden" aria-hidden="true">
              {title}
            </h2>
            <img
              src="/assets/img/logo.png"
              alt="NHL Stenden University of Applied Sciences"
            />
            <h2>{title}</h2>
          </div>
        </div>
        <div
          role="navigation"
          id="navigation"
          aria-label="Main navigation"
          className={`sidebar ${menuOpen ? "opened" : ""}`}
          inert={!menuOpen && !mobile ? true : undefined}
        >
          <div>
            {navigation.map(([destination, label]) => (
              <a
                key={destination}
                className={`${destination} ${(panel || "map") === destination ? "active" : ""}`}
                href={destination === "map" ? "#map" : `#${destination}`}
                aria-current={
                  (panel || "map") === destination ? "page" : undefined
                }
                onClick={(event) => openPanel(event, destination)}
              >
                {label}
              </a>
            ))}
          </div>
        </div>
      </header>
      <main id="map" className="map-stage" inert={panel ? true : undefined}>
        <MaritimeMap
          ref={map}
          layer={layer}
          incidents={visibleIncidents}
          onError={setMapWarning}
        />
        <div className="zoomControls" aria-label="Map zoom controls">
          <button
            type="button"
            className="zoomIn"
            aria-label="Zoom in"
            onClick={() => map.current?.zoomIn()}
          >
            <img src="/assets/img/plus.svg" alt="" />
          </button>
          <button
            type="button"
            className="zoomOut"
            aria-label="Zoom out"
            onClick={() => map.current?.zoomOut()}
          >
            <img src="/assets/img/minus.svg" alt="" />
          </button>
        </div>
        <button
          type="button"
          className={`layerToggle ${layer === "satellite" ? "satelite" : ""}`}
          aria-label={
            layer === "roadmap"
              ? "Switch to satellite view"
              : "Switch to map view"
          }
          aria-pressed={layer === "satellite"}
          onClick={() =>
            setLayer(layer === "roadmap" ? "satellite" : "roadmap")
          }
        >
          <img className="top" src="/assets/img/layer_top.svg" alt="" />
          <img className="bottom" src="/assets/img/layer_bottom.svg" alt="" />
        </button>
        {dataState === "error" && (
          <div className="map-notice" role="alert">
            <p>{dataError || "Records could not be loaded."}</p>
            <button type="button" onClick={() => setReload((v) => v + 1)}>
              Try again
            </button>
          </div>
        )}
        {mapWarning && (
          <div className="map-notice provider-notice" role="status">
            <p>{mapWarning}</p>
            <button type="button" onClick={() => setMapWarning("")}>
              Dismiss
            </button>
          </div>
        )}
      </main>
      {panel && (
        <div className="panel-backdrop" onClick={() => setPanel(null)}>
          <section
            className="content-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="panel-title"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key !== "Tab") return;
              const focusable = [
                ...event.currentTarget.querySelectorAll(
                  "button, a[href], input",
                ),
              ];
              const first = focusable[0],
                last = focusable[focusable.length - 1];
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
          >
            <div className="panel-heading">
              <h1 id="panel-title">
                {navigation.find(([key]) => key === panel)?.[1]}
              </h1>
              <button
                type="button"
                ref={closeButton}
                className="close-panel"
                aria-label="Close"
                onClick={() => setPanel(null)}
              >
                Close
              </button>
            </div>
            {panel === "list" && (
              <>
                <p className="panel-count">{visibleIncidents.length} records</p>
                {dataState === "loading" ? (
                  <p>Loading records…</p>
                ) : dataState === "error" ? (
                  <div role="alert">
                    <p>{dataError || "Records could not be loaded."}</p>
                    <button
                      type="button"
                      onClick={() => setReload((v) => v + 1)}
                    >
                      Try again
                    </button>
                  </div>
                ) : !visibleIncidents.length ? (
                  <p>No records to display.</p>
                ) : (
                  <ul className="record-list">
                    {visibleIncidents.map((record) => (
                      <li key={record.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setPanel(null);
                            map.current?.flyTo({
                              lat: record.latitude,
                              lng: record.longitude,
                              zoom: 9,
                            });
                          }}
                        >
                          <strong>{record.title}</strong>
                          <span>
                            {[record.victimCountry, record.year]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
            {panel === "info" && (
              <>
                <h2>{title}</h2>
                <p>
                  Explore the maritime map. Search for a country or city, zoom
                  in to an area, or switch between map and satellite views.
                </p>
                <p>
                  This development preview contains no bundled incident records.
                </p>
              </>
            )}
            {panel === "report" && (
              <p>Incident reporting is unavailable in this preview.</p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
