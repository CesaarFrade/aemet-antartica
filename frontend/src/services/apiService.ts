import type { ApiResponse } from '../types/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const fetchMeteoData = async (
  fechaIni: string,
  fechaFin: string,
  estacion: string,
  aggregation: string = 'None',
  dataTypes: string[] = [],
  location: string = ''
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

  const response = await fetch(url);
  
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

export const fetchStations = async (): Promise<StationInfo[]> => {
  const url = `${API_BASE_URL}/api/antartida/estaciones`;

  const response = await fetch(url);

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    const errorMessage = errorData?.detail || `HTTP Error: ${response.status}`;
    throw new Error(errorMessage);
  }

  const json = await response.json();
  return json.data;
};