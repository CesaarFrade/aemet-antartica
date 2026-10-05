import type { ApiResponse } from '../types/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Whether a rejection is a cancellation rather than a failure.
 *
 * `fetch` rejects with a `DOMException` named `AbortError`, which is an `Error`, so
 * the name is what has to be checked. Callers must treat it as "superseded" and keep
 * quiet: reporting it would show an error for a request the user themselves replaced.
 */
export const isAbortError = (error: unknown): boolean =>
  error instanceof Error && error.name === 'AbortError';

export const fetchMeteoData = async (
  fechaIni: string,
  fechaFin: string,
  estacion: string,
  aggregation: string = 'None',
  dataTypes: string[] = [],
  location: string = '',
  signal?: AbortSignal
): Promise<ApiResponse> => {
  let url = `${API_BASE_URL}/api/antartida/datos/fechaini/${fechaIni}/fechafin/${fechaFin}/estacion/${encodeURIComponent(estacion)}?aggregation=${aggregation}`;

  if (dataTypes.length > 0) {
    dataTypes.forEach(type => {
      url += `&data_types=${encodeURIComponent(type)}`;
    });
  }

  // Only sent when the analyst actually picked something: the endpoint reads an
  // absent `location` as UTC, the zone AEMET publishes on, so omitting it by default
  // keeps the request identical to the one the backend has always received.
  if (location) {
    // `encodeURIComponent` is what turns the sign into `%2B`; a bare `+02:00` would
    // otherwise be read as a space by the query parser.
    url += `&location=${encodeURIComponent(location)}`;
  }

  const response = await fetch(url, { signal });

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    const errorMessage = errorData?.detail || `HTTP Error: ${response.status}`;
    throw new Error(errorMessage);
  }

  return response.json();
};

export interface StationInfo {
  id: string;
  name: string;
}

export const fetchStations = async (signal?: AbortSignal): Promise<StationInfo[]> => {
  const url = `${API_BASE_URL}/api/antartida/estaciones`;

  const response = await fetch(url, { signal });

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    const errorMessage = errorData?.detail || `HTTP Error: ${response.status}`;
    throw new Error(errorMessage);
  }

  const json = await response.json();
  return json.data;
};