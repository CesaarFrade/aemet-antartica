export interface MeteoData {
  Station: string;
  Datetime: string;
  "Temperature (ºC)": number | null;
  "Pressure (hpa)": number | null;
  "Speed (m/s)": number | null;
}

export interface ApiResponse {
  status: string;
  station_requested: string;
  station_resolved?: string;
  location_requested?: string | null;
  location_resolved: string;
  data_types_filtered: string | string[];
  data: MeteoData[];
}