export interface Incident {
  id: string;
  title: string;
  latitude: number | null;
  longitude: number | null;
  victimCountry: string;
  year: number | null;
  victimType: string;
  impactArea: string;
  incidentCountry: string;
  attackerCountry: string;
  description: string;
  dateText: string;
  datePrecision: string;
  locationName: string;
  locationPrecision: string;
  locationSource: string;
  sourceUrl: string;
  method: string;
  countryFacets?: Partial<Record<"incidentCountry" | "victimCountry" | "attackerCountry", string[]>>;
  rawFields?: Record<string, unknown>;
  sources?: Array<{ url: string; shortName?: string; rawText?: string }>;
  updates?: Array<{ candidateId: string; publishedAt: string; reportedAt?: string; summary: string; sourceUrls: string[] }>;
}
export type IncidentFilterField =
  | "all"
  | "victimCountry"
  | "year"
  | "victimType"
  | "impactArea"
  | "incidentCountry"
  | "attackerCountry"
  | "method";
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
