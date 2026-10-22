import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const INITIAL_VIEW = { lat: 49.240831, lng: 12.627241, zoom: 5 };
const GOOGLE_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY?.trim();
const GOOGLE_MAP_ID = import.meta.env.VITE_GOOGLE_MAPS_MAP_ID?.trim();
const TILE_LAYERS = {
  roadmap: {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxNativeZoom: 19,
  },
  satellite: {
    url: "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      'Source: <a href="https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9">Esri World Imagery</a>, Vantor, Earthstar Geographics, and the GIS User Community',
    maxNativeZoom: 19,
  },
};

let googleLoadPromise;
let googleCallbackSequence = 0;
const authSubscribers = new Set();
let previousAuthFailure;

function dispatchAuthFailure() {
  authSubscribers.forEach((callback) => callback());
  if (typeof previousAuthFailure === "function") previousAuthFailure();
}

function subscribeGoogleAuthFailure(callback) {
  if (authSubscribers.size === 0) {
    previousAuthFailure = window.gm_authFailure;
    window.gm_authFailure = dispatchAuthFailure;
  }
  authSubscribers.add(callback);
  return () => {
    authSubscribers.delete(callback);
    if (
      authSubscribers.size === 0 &&
      window.gm_authFailure === dispatchAuthFailure
    ) {
      if (previousAuthFailure === undefined) delete window.gm_authFailure;
      else window.gm_authFailure = previousAuthFailure;
    }
  };
}

// One shared request also survives React StrictMode's mount/cleanup/mount cycle.
function loadGoogleMaps() {
  if (
    window.google?.maps?.Map &&
    window.google?.maps?.marker?.AdvancedMarkerElement
  ) {
    return Promise.resolve(window.google.maps);
  }
  if (googleLoadPromise) return googleLoadPromise;
  if (window.google?.maps?.importLibrary) {
    googleLoadPromise = Promise.all([
      window.google.maps.importLibrary("maps"),
      window.google.maps.importLibrary("marker"),
    ]).then(() => window.google.maps);
    return googleLoadPromise;
  }

  googleLoadPromise = new Promise((resolve, reject) => {
    const callbackName = `__mcadMapsReady${++googleCallbackSequence}`;
    const script = document.createElement("script");
    let settled = false;
    let timeout;
    let unsubscribeAuth = () => {};
    const finish = (error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      unsubscribeAuth();
      script.onerror = null;
      if (error) {
        // A late script response can still invoke its callback after a timeout.
        window[callbackName] = () => {};
        script.remove();
        reject(error);
      } else {
        delete window[callbackName];
        resolve(window.google.maps);
      }
    };
    unsubscribeAuth = subscribeGoogleAuthFailure(() =>
      finish(
        new Error(
          "Google Maps rejected the API key or its website restrictions.",
        ),
      ),
    );
    window[callbackName] = () => {
      if (
        !window.google?.maps?.Map ||
        !window.google?.maps?.marker?.AdvancedMarkerElement
      ) {
        finish(
          new Error("Google Maps did not provide the required map libraries."),
        );
        return;
      }
      finish();
    };
    const params = new URLSearchParams({
      key: GOOGLE_KEY,
      v: "weekly",
      loading: "async",
      libraries: "marker",
      language: "en",
      callback: callbackName,
    });
    if (GOOGLE_MAP_ID) params.set("map_ids", GOOGLE_MAP_ID);
    script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
    script.async = true;
    script.onerror = () =>
      finish(new Error("Google Maps could not be downloaded."));
    const nonce = document.querySelector("script[nonce]")?.nonce;
    if (nonce) script.nonce = nonce;
    timeout = window.setTimeout(
      () => finish(new Error("Google Maps did not respond within 15 seconds.")),
      15000,
    );
    document.head.appendChild(script);
  });
  return googleLoadPromise;
}

function coordinates(latitude, longitude) {
  if (
    latitude === null ||
    latitude === undefined ||
    latitude === "" ||
    longitude === null ||
    longitude === undefined ||
    longitude === ""
  )
    return null;
  const lat = Number(latitude);
  const lng = Number(longitude);
  return Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lng) <= 180
    ? { lat, lng }
    : null;
}

function zoomLevel(value, fallback = INITIAL_VIEW.zoom) {
  const zoom = Number(value);
  return Number.isFinite(zoom) ? Math.min(20, Math.max(2, zoom)) : fallback;
}

function popupContent(incident) {
  const content = document.createElement("article");
  content.className = "incident-popup";
  const title = document.createElement("h3");
  title.textContent = String(incident.title || "Incident");
  content.appendChild(title);
  const details = document.createElement("dl");
  [
    ["Year", incident.year],
    ["Victim country", incident.victimCountry],
    ["Victim type", incident.victimType],
    ["Impact area", incident.impactArea],
    ["Incident country", incident.incidentCountry],
    ["Attacker country", incident.attackerCountry],
  ].forEach(([label, value]) => {
    if (value === undefined || value === null || value === "") return;
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = String(value);
    details.append(term, description);
  });
  content.appendChild(details);
  return content;
}

function markerContent() {
  const pin = document.createElement("span");
  pin.className = "incident-marker-icon";
  pin.setAttribute("aria-hidden", "true");
  Object.assign(pin.style, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: "25px",
    height: "25px",
    borderRadius: "50%",
    background: "#e62328",
    boxShadow: "0 2px 5px rgba(0,0,0,.5)",
  });
  const boat = document.createElement("img");
  boat.src = "/assets/img/markers/Boat.png";
  boat.alt = "";
  boat.height = 15;
  boat.style.width = "auto";
  pin.appendChild(boat);
  return pin;
}

function createLeafletMap(container, onError) {
  const map = L.map(container, {
    center: [INITIAL_VIEW.lat, INITIAL_VIEW.lng],
    zoom: INITIAL_VIEW.zoom,
    minZoom: 2,
    maxZoom: 20,
    zoomControl: false,
    attributionControl: true,
    keyboard: true,
    worldCopyJump: true,
  });
  map.attributionControl.setPrefix(false);
  const markers = L.layerGroup().addTo(map);
  let basemap;
  let currentLayer;
  return {
    zoomIn: () => map.zoomIn(),
    zoomOut: () => map.zoomOut(),
    flyTo: ({ lat, lng, zoom }) =>
      map.flyTo([lat, lng], zoomLevel(zoom, map.getZoom()), { duration: 0.7 }),
    reset: () =>
      map.setView([INITIAL_VIEW.lat, INITIAL_VIEW.lng], INITIAL_VIEW.zoom),
    resize: () => map.invalidateSize({ pan: false }),
    setLayer(layer) {
      const name = layer === "satellite" ? "satellite" : "roadmap";
      if (currentLayer === name) return;
      if (basemap) {
        basemap.off("tileerror");
        basemap.remove();
      }
      currentLayer = name;
      const { url, ...options } = TILE_LAYERS[name];
      let errorReported = false;
      basemap = L.tileLayer(url, {
        ...options,
        maxZoom: 20,
        subdomains: "abcd",
      });
      basemap.on("tileerror", () => {
        if (errorReported) return;
        errorReported = true;
        onError(
          `Some ${name === "satellite" ? "satellite" : "map"} tiles could not load. Check the connection or the tile provider.`,
        );
      });
      basemap.addTo(map);
    },
    setIncidents(incidents) {
      markers.clearLayers();
      incidents.forEach((incident) => {
        if (!incident || typeof incident !== "object") return;
        const position = coordinates(incident.latitude, incident.longitude);
        if (!position) return;
        const marker = L.marker([position.lat, position.lng], {
          title: String(incident.title || "Incident"),
          keyboard: true,
          icon: L.divIcon({
            className: "incident-map-pin",
            html: markerContent(),
            iconSize: [25, 25],
            iconAnchor: [12.5, 12.5],
          }),
        });
        marker.bindPopup(popupContent(incident), { maxWidth: 320 });
        markers.addLayer(marker);
      });
    },
    destroy() {
      basemap?.off("tileerror");
      markers.clearLayers();
      map.remove();
    },
  };
}

function createGoogleMap(container, maps) {
  const map = new maps.Map(container, {
    center: { lat: INITIAL_VIEW.lat, lng: INITIAL_VIEW.lng },
    zoom: INITIAL_VIEW.zoom,
    minZoom: 2,
    maxZoom: 20,
    mapId: GOOGLE_MAP_ID || "DEMO_MAP_ID",
    disableDefaultUI: true,
    keyboardShortcuts: true,
    gestureHandling: "greedy",
    clickableIcons: false,
    tilt: 0,
  });
  const popup = new maps.InfoWindow();
  let markers = [];
  const clearMarkers = () => {
    popup.close();
    markers.forEach(({ marker, onClick }) => {
      marker.removeEventListener("gmp-click", onClick);
      marker.map = null;
    });
    markers = [];
  };
  return {
    zoomIn: () =>
      map.setZoom(zoomLevel((map.getZoom() ?? INITIAL_VIEW.zoom) + 1)),
    zoomOut: () =>
      map.setZoom(zoomLevel((map.getZoom() ?? INITIAL_VIEW.zoom) - 1)),
    flyTo({ lat, lng, zoom }) {
      map.panTo({ lat, lng });
      if (zoom !== undefined) map.setZoom(zoomLevel(zoom));
    },
    reset() {
      map.setCenter({ lat: INITIAL_VIEW.lat, lng: INITIAL_VIEW.lng });
      map.setZoom(INITIAL_VIEW.zoom);
    },
    resize: () => maps.event.trigger(map, "resize"),
    setLayer: (layer) =>
      map.setMapTypeId(layer === "satellite" ? "satellite" : "roadmap"),
    setIncidents(incidents) {
      clearMarkers();
      incidents.forEach((incident) => {
        if (!incident || typeof incident !== "object") return;
        const position = coordinates(incident.latitude, incident.longitude);
        if (!position) return;
        const marker = new maps.marker.AdvancedMarkerElement({
          map,
          position,
          title: String(incident.title || "Incident"),
          gmpClickable: true,
          anchorTop: "-50%",
        });
        marker.append(markerContent());
        const onClick = () => {
          popup.setContent(popupContent(incident));
          popup.open({ map, anchor: marker });
        };
        marker.addEventListener("gmp-click", onClick);
        markers.push({ marker, onClick });
      });
    },
    destroy() {
      clearMarkers();
      maps.event.clearInstanceListeners(map);
      map.unbindAll();
      container.replaceChildren();
    },
  };
}

const MaritimeMap = forwardRef(function MaritimeMap(
  { layer = "roadmap", incidents = [], onReady, onError },
  ref,
) {
  const containerRef = useRef(null);
  const adapterRef = useRef(null);
  const latest = useRef({ layer, incidents, onReady, onError });
  latest.current = { layer, incidents, onReady, onError };

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => adapterRef.current?.zoomIn(),
      zoomOut: () => adapterRef.current?.zoomOut(),
      flyTo: (target = {}) => {
        const position = coordinates(target.lat, target.lng);
        if (position)
          adapterRef.current?.flyTo({ ...position, zoom: target.zoom });
      },
      reset: () => adapterRef.current?.reset(),
    }),
    [],
  );

  useEffect(() => {
    const container = containerRef.current;
    let disposed = false;
    let fallbackUsed = false;
    let unsubscribeAuth = () => {};
    let resizeFrame;
    const reportError = (message) => {
      if (!disposed) latest.current.onError?.(message);
    };
    const install = (adapter, provider) => {
      if (disposed) {
        adapter.destroy();
        return;
      }
      adapterRef.current = adapter;
      adapter.setLayer(latest.current.layer);
      adapter.setIncidents(
        Array.isArray(latest.current.incidents) ? latest.current.incidents : [],
      );
      adapter.resize();
      latest.current.onReady?.(provider);
    };
    const useLeaflet = (reason) => {
      if (disposed || fallbackUsed) return;
      fallbackUsed = true;
      unsubscribeAuth();
      adapterRef.current?.destroy();
      adapterRef.current = null;
      container.replaceChildren();
      if (reason)
        reportError(`${reason} The map is using OpenStreetMap instead.`);
      install(createLeafletMap(container, reportError), "leaflet");
    };
    if (GOOGLE_KEY) {
      loadGoogleMaps()
        .then((maps) => {
          if (disposed) return;
          try {
            unsubscribeAuth = subscribeGoogleAuthFailure(() =>
              useLeaflet(
                "Google Maps rejected the API key or its website restrictions.",
              ),
            );
            install(createGoogleMap(container, maps), "google");
          } catch (error) {
            useLeaflet(
              error instanceof Error
                ? error.message
                : "Google Maps could not start.",
            );
          }
        })
        .catch((error) =>
          useLeaflet(
            error instanceof Error
              ? error.message
              : "Google Maps could not load.",
          ),
        );
    } else {
      useLeaflet();
    }
    const resize = () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        if (!disposed) adapterRef.current?.resize();
      });
    };
    const observer =
      typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    observer?.observe(container);
    window.addEventListener("resize", resize);
    return () => {
      disposed = true;
      unsubscribeAuth();
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(resizeFrame);
      adapterRef.current?.destroy();
      adapterRef.current = null;
    };
  }, []);

  useEffect(() => {
    adapterRef.current?.setLayer(layer);
  }, [layer]);
  useEffect(() => {
    adapterRef.current?.setIncidents(Array.isArray(incidents) ? incidents : []);
  }, [incidents]);

  return (
    <div
      ref={containerRef}
      className="map-canvas"
      role="region"
      aria-label="Maritime map"
      tabIndex={0}
    />
  );
});

export default MaritimeMap;
