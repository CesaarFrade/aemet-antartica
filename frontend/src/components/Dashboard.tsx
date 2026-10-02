import { useState } from 'react';
import { fetchMeteoData } from '../services/apiService';
import type { ApiResponse } from '../types/api';

export default function Dashboard() {
  // --- FORM STATES ---
  // Default values for easier testing
  const [fechaIni, setFechaIni] = useState('2024-01-01T00:00:00');
  const [fechaFin, setFechaFin] = useState('2024-01-05T23:59:59');
  const [estacion, setEstacion] = useState('89064'); // Gabriel de Castilla code
  const [aggregation, setAggregation] = useState('Daily');

  // --- UI STATES ---
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiResponse, setApiResponse] = useState<ApiResponse | null>(null);

  // --- FORM HANDLER ---
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setApiResponse(null);

    try {
      // Call our service (which communicates with your FastAPI backend)
      const response = await fetchMeteoData(fechaIni, fechaFin, estacion, aggregation);
      setApiResponse(response);
    } catch (err: any) {
      setError(err.message || 'Unknown error connecting to the server');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-6 md:p-10 font-sans text-slate-800">
      <div className="max-w-6xl mx-auto space-y-6">
        
        {/* HEADER */}
        <header className="mb-8">
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight">
            Antarctica Wind Farm <span className="text-blue-600">Analytics</span>
          </h1>
          <p className="text-slate-500 mt-1">AEMET Meteorological Data Explorer</p>
        </header>

        {/* FILTER PANEL (FORM) */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            
            <div className="flex flex-col space-y-1">
              <label className="text-sm font-medium text-slate-600">Station (Code or Name)</label>
              <input 
                type="text" 
                value={estacion}
                onChange={(e) => setEstacion(e.target.value)}
                className="px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>

            <div className="flex flex-col space-y-1">
              <label className="text-sm font-medium text-slate-600">Start Date (YYYY-MM-DDTHH:MM:SS)</label>
              <input 
                type="text" 
                value={fechaIni}
                onChange={(e) => setFechaIni(e.target.value)}
                className="px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>

            <div className="flex flex-col space-y-1">
              <label className="text-sm font-medium text-slate-600">End Date (YYYY-MM-DDTHH:MM:SS)</label>
              <input 
                type="text" 
                value={fechaFin}
                onChange={(e) => setFechaFin(e.target.value)}
                className="px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>

            <div className="flex flex-col space-y-1">
              <label className="text-sm font-medium text-slate-600">Aggregation</label>
              <select 
                value={aggregation}
                onChange={(e) => setAggregation(e.target.value)}
                className="px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
              >
                <option value="None">None (10 min)</option>
                <option value="Hourly">Hourly</option>
                <option value="Daily">Daily</option>
                <option value="Monthly">Monthly</option>
              </select>
            </div>

            <div className="md:col-span-4 flex justify-end mt-2">
              <button 
                type="submit" 
                disabled={loading}
                className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-6 rounded-md transition-colors disabled:bg-blue-400"
              >
                {loading ? 'Fetching...' : 'Fetch Data'}
              </button>
            </div>
          </form>
        </div>

        {/* ERROR MESSAGE */}
        {error && (
          <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-md">
            <p className="text-red-700 font-medium">Request Error</p>
            <p className="text-red-600 text-sm">{error}</p>
          </div>
        )}

        {/* RESULTS AREA (Provisional) */}
        {apiResponse && !error && (
          <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
            <h2 className="text-lg font-bold text-slate-800 mb-2">
              Results: {apiResponse.station_requested}
            </h2>
            <p className="text-sm text-slate-500 mb-4">
              Found <span className="font-bold">{apiResponse.data.length}</span> records.
            </p>
            
            {/* We will add the charts here later. For now, dump the raw JSON to verify */}
            <div className="bg-slate-900 rounded-md p-4 overflow-auto max-h-96">
              <pre className="text-green-400 text-xs">
                {JSON.stringify(apiResponse.data, null, 2)}
              </pre>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}