import type { ApiResponse } from '../types/api';

const API_BASE_URL = 'http://localhost:8000';

export const fetchMeteoData = async (
  fechaIni: string,
  fechaFin: string,
  estacion: string,
  aggregation: string = 'None',
  dataTypes: string[] = []
): Promise<ApiResponse> => {
  let url = `${API_BASE_URL}/api/antartida/datos/fechaini/${fechaIni}/fechafin/${fechaFin}/estacion/${encodeURIComponent(estacion)}?aggregation=${aggregation}`;

  if (dataTypes.length > 0) {
    dataTypes.forEach(type => {
      url += `&data_types=${encodeURIComponent(type)}`;
    });
  }

  const response = await fetch(url);
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    const errorMessage = errorData?.detail || `HTTP Error: ${response.status}`;
    throw new Error(errorMessage);
  }

  return response.json();
};