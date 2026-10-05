import { useState, useMemo, useEffect, useRef } from 'react';
import { fetchMeteoData, fetchStations, isAbortError, type StationInfo } from '../services/apiService';
import { LOCATION_GROUPS, DEFAULT_LOCATION, describeLocation } from '../constants/locations';
import type { ApiResponse } from '../types/api';
import SelectField, { CONTROL_CLASS } from './SelectField';
import WeatherChart from './WeatherChart';

const AVAILABLE_VARS = [
  { id: 'temperature', label: 'Temperature (ºC)' },
  { id: 'pressure', label: 'Pressure (hpa)' },
  { id: 'speed', label: 'Wind Speed (m/s)' }
];

export default function Dashboard() {
  const [fechaIni, setFechaIni] = useState('2024-01-01T00:00:00');
  const [fechaFin, setFechaFin] = useState('2024-01-05T23:59:59');
  const [estacion, setEstacion] = useState('');
  const [aggregation, setAggregation] = useState('Daily');
  const [selectedVars, setSelectedVars] = useState<string[]>([]);
  // Time zone the typed dates are written in. Defaults to UTC, which is what the
  // endpoint assumes when the parameter is absent, so the initial query is unchanged.
  const [location, setLocation] = useState(DEFAULT_LOCATION);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiResponse, setApiResponse] = useState<ApiResponse | null>(null);

  const [stations, setStations] = useState<StationInfo[]>([]);
  const [stationsError, setStationsError] = useState<string | null>(null);

  // The station picker is populated from the API instead of hardcoded, so the UI can
  // never offer an identifier the service does not know about. The first station is
  // pre-selected once the list arrives, matching the pre-filled date defaults.
  //
  // The controller replaces the usual `let active = true` guard: it both suppresses the
  // update after unmount and stops the request instead of leaving it in flight.
  useEffect(() => {
    const controller = new AbortController();

    fetchStations(controller.signal)
      .then((data) => {
        setStations(data);
        setEstacion((current) => current || data[0]?.id || '');
      })
      .catch((err: unknown) => {
        // Unmounting mid-flight aborts on purpose, and that is not a failure to report.
        if (isAbortError(err)) return;
        setStationsError(
          err instanceof Error
            ? err.message
            : 'Could not reach the backend on port 8000.'
        );
      });

    return () => controller.abort();
  }, []);
  
  const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart');

  const handleVarToggle = (varId: string) => {
    setSelectedVars(prev => 
      prev.includes(varId) ? prev.filter(v => v !== varId) : [...prev, varId]
    );
  };

  // Owns the in-flight query so a newer submission can cancel it. Kept in a ref
  // because it is plumbing, not something to re-render on.
  const queryAbortRef = useRef<AbortController | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Whatever is in flight is already stale: the user just asked for something else.
    queryAbortRef.current?.abort();
    const controller = new AbortController();
    queryAbortRef.current = controller;

    setLoading(true);
    setError(null);
    setApiResponse(null);

    try {
      const response = await fetchMeteoData(fechaIni, fechaFin, estacion, aggregation, selectedVars, location, controller.signal);
      // Identity check as well as the abort: it keeps a late response harmless even if
      // the transport could not cancel, which is the case for the mock in the tests.
      if (queryAbortRef.current === controller) {
        setApiResponse(response);
      }
    } catch (err: unknown) {
      // Being replaced is not an error, and only the request that still owns the ref
      // gets to report or clear anything.
      if (!isAbortError(err) && queryAbortRef.current === controller) {
        setError(err instanceof Error ? err.message : 'Unknown error connecting to the server');
      }
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setLoading(false);
      }
    }
  };

  // Leaving the page abandons the query instead of letting it finish into nothing.
  useEffect(() => () => queryAbortRef.current?.abort(), []);

  const kpis = useMemo(() => {
    if (!apiResponse?.data || apiResponse.data.length === 0) return null;
    const data = apiResponse.data;
    
    // Añadimos comprobación extra para undefined
    const temps = data.map(d => d['Temperature (ºC)']).filter(t => t !== null && t !== undefined) as number[];
    const speeds = data.map(d => d['Speed (m/s)']).filter(s => s !== null && s !== undefined) as number[];

    return {
      maxTemp: temps.length > 0 ? Math.max(...temps).toFixed(1) : '-',
      avgSpeed: speeds.length > 0 ? (speeds.reduce((a, b) => a + b, 0) / speeds.length).toFixed(1) : '-',
      records: data.length
    };
  }, [apiResponse]);

  return (
    <div className="min-h-screen bg-slate-50 p-6 md:p-10 font-sans text-slate-800">
      <div className="max-w-6xl mx-auto space-y-6">
        
        <header className="mb-8 flex justify-between items-end">
          <div>
            <h1 className="text-3xl font-bold text-slate-900 tracking-tight">
              Antarctica Wind Farm <span className="text-blue-600">Analytics</span>
            </h1>
            <p className="text-slate-500 mt-1">AEMET Meteorological Data Explorer</p>
          </div>
        </header>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 items-end">
              <SelectField
                id="station-select"
                label="Station"
                value={estacion}
                onChange={setEstacion}
                disabled={stations.length === 0}
                required
              >
                {stations.length === 0 ? (
                  <option value="">
                    {stationsError ? 'Stations unavailable' : 'Loading stations...'}
                  </option>
                ) : (
                  stations.map((st) => (
                    <option key={st.id} value={st.id}>
                      {st.name} ({st.id})
                    </option>
                  ))
                )}
              </SelectField>
              <div className="flex flex-col space-y-1">
                <label htmlFor="fecha-ini" className="text-sm font-medium text-slate-600">Start Date</label>
                <input 
                  id="fecha-ini"
                  type="text" value={fechaIni} onChange={(e) => setFechaIni(e.target.value)}
                  className={CONTROL_CLASS} required
                />
              </div>
              <div className="flex flex-col space-y-1">
                <label htmlFor="fecha-fin" className="text-sm font-medium text-slate-600">End Date</label>
                <input 
                  id="fecha-fin"
                  type="text" value={fechaFin} onChange={(e) => setFechaFin(e.target.value)}
                  className={CONTROL_CLASS} required
                />
              </div>
              <SelectField
                id="aggregation-select"
                label="Aggregation"
                value={aggregation}
                onChange={setAggregation}
              >
                <option value="None">None (10 min)</option>
                <option value="Hourly">Hourly</option>
                <option value="Daily">Daily</option>
                <option value="Monthly">Monthly</option>
              </SelectField>
              <SelectField
                id="location-select"
                label="Location"
                value={location}
                onChange={setLocation}
              >
                {LOCATION_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.options.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </SelectField>
            </div>

            {/* Reading the dates in the wrong zone silently shifts the whole window by
                hours, so the current interpretation is always spelled out. */}
            <p data-testid="location-hint" className="text-xs text-slate-500">
              Dates are read as <span className="font-medium text-slate-600">{describeLocation(location)}</span>.
              The response is always rendered in Europe/Madrid (CET/CEST) including the offset.
            </p>

            <div className="flex flex-col md:flex-row justify-between items-start md:items-center pt-4 border-t border-slate-100">
              <div className="flex items-center space-x-4 mb-4 md:mb-0">
                <span className="text-sm font-medium text-slate-600">Variables (Leave empty for all):</span>
                <div className="flex space-x-3">
                  {AVAILABLE_VARS.map(v => (
                    <label key={v.id} className="flex items-center space-x-1 cursor-pointer">
                      <input 
                        type="checkbox"
                        checked={selectedVars.includes(v.id)}
                        onChange={() => handleVarToggle(v.id)}
                        className="rounded text-blue-600 focus:ring-blue-500"
                      />
                      <span className="text-sm text-slate-700">{v.label}</span>
                    </label>
                  ))}
                </div>
              </div>
              
              <button 
                type="submit" disabled={loading}
                // The spinner replaces the label while loading, so the accessible name
                // is pinned here; otherwise the control goes nameless mid-request.
                aria-label="Analyze Data"
                className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-8 rounded-md transition-all flex items-center justify-center min-w-[140px]"
              >
                {loading ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                ) : 'Analyze Data'}
              </button>
            </div>
          </form>
        </div>

        {stationsError && (
          <div className="bg-amber-50 border-l-4 border-amber-500 p-4 rounded-md">
            <p className="text-amber-700 font-medium">Could not load the station list</p>
            <p className="text-amber-700 text-sm">
              {stationsError}. Is the FastAPI backend running on http://localhost:8000?
            </p>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-md">
            <p className="text-red-700 font-medium">Data Fetch Error</p>
            <p className="text-red-600 text-sm">{error}</p>
          </div>
        )}

        {apiResponse && !error && (
          <div className="space-y-6 animate-in fade-in duration-500">
            {/* KPI CARDS */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
                <p className="text-sm text-slate-500 font-medium">Max Temperature</p>
                <p className="text-2xl font-bold text-slate-800">{kpis?.maxTemp} ºC</p>
              </div>
              <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
                <p className="text-sm text-slate-500 font-medium">Avg Wind Speed</p>
                <p className="text-2xl font-bold text-slate-800">{kpis?.avgSpeed} m/s</p>
              </div>
              <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
                <p className="text-sm text-slate-500 font-medium">Data Points Analyzed</p>
                <p className="text-2xl font-bold text-slate-800">{kpis?.records}</p>
              </div>
            </div>

            {/* DATA VIEW AREA */}
            <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h2 className="text-lg font-bold text-slate-800">Results: {apiResponse.station_requested}</h2>
                  <p className="text-sm text-slate-500">Filtered by: {Array.isArray(apiResponse.data_types_filtered) ? apiResponse.data_types_filtered.join(', ') : apiResponse.data_types_filtered}</p>
                </div>
                
                <div className="flex bg-slate-100 p-1 rounded-lg">
                  <button 
                    onClick={() => setViewMode('chart')}
                    className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${viewMode === 'chart' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                  >
                    Chart View
                  </button>
                  <button 
                    onClick={() => setViewMode('table')}
                    className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${viewMode === 'table' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                  >
                    Data Table
                  </button>
                </div>
              </div>

              {apiResponse.data.length > 0 ? (
                viewMode === 'chart' ? (
                  <WeatherChart data={apiResponse.data} />
                ) : (
                  <div className="overflow-x-auto max-h-[400px] border border-slate-200 rounded-lg">
                    <table className="w-full text-sm text-left text-slate-600">
                      <thead className="text-xs text-slate-700 uppercase bg-slate-50 sticky top-0">
                        <tr>
                          <th className="px-6 py-3">Datetime</th>
                          <th className="px-6 py-3">Temp (ºC)</th>
                          <th className="px-6 py-3">Speed (m/s)</th>
                          <th className="px-6 py-3">Pressure (hpa)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {apiResponse.data.map((row, idx) => (
                          <tr key={idx} className="border-b border-slate-100 hover:bg-slate-50">
                            <td className="px-6 py-3 font-medium text-slate-900">{row.Datetime}</td>
                            <td className="px-6 py-3">{row['Temperature (ºC)'] ?? '-'}</td>
                            <td className="px-6 py-3">{row['Speed (m/s)'] ?? '-'}</td>
                            <td className="px-6 py-3">{row['Pressure (hpa)'] ?? '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : (
                <div className="py-12 text-center text-slate-500 bg-slate-50 rounded-lg border border-dashed border-slate-300">
                  No variables matched the filters for this time period.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}