import Supercluster from "supercluster";
import { hasCoordinates } from "./incidents.js";

/** Each incident stays a distinct point, even when several source rows share coordinates. */
export function createIncidentIndex(incidents) {
  const points = incidents.filter(hasCoordinates).map((incident) => ({
    type: "Feature",
    geometry: {
      type: "Point",
      coordinates: [incident.longitude, incident.latitude],
    },
    properties: { incident },
  }));
  return new Supercluster({ radius: 48, maxZoom: 20 }).load(points);
}
