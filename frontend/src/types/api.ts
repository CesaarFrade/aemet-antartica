export interface MeteoData {
  // Optional on purpose: the backend only replays `nombre` when a label was actually
  // stored, so a cache hit for a payload without one has no `Station` key at all.
  // Emitting an empty one would make a cache hit differ from a cache miss for the
  // same request, which is the invariant the backend is protecting (see
  // `_restore_raw_records` in `backend/api/routes.py`).
  Station?: string;
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