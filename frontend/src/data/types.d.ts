export interface Incident {
  id: string;
  title: string;
  latitude: number;
  longitude: number;
  victimCountry: string;
  year: number | null;
  victimType: string;
  impactArea: string;
  incidentCountry: string;
  attackerCountry: string;
}

export type IncidentFilterField =
  | "all"
  | "victimCountry"
  | "year"
  | "victimType"
  | "impactArea"
  | "incidentCountry"
  | "attackerCountry";

export interface IncidentRequestOptions {
  signal?: AbortSignal;
}

export interface Place {
  id: string;
  label: string;
  lat: number;
  lng: number;
  zoom: number;
  keywords?: readonly string[];
}
