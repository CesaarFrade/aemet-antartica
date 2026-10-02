import { useState, useMemo } from 'react';
import { fetchMeteoData } from '../services/apiService';
import type { ApiResponse, MeteoData } from '../types/api';
import WeatherChart from './WeatherChart';

const AVAILABLE_VARS = [
  { id: 'temperature', label: 'Temperature (ºC)' },
  { id: 'pressure', label: 'Pressure (hpa)' },
  { id: 'speed', label: 'Wind Speed (m/s)' }
];

export default function Dashboard() {
  const [fechaIni, setFechaIni] = useState('2024-01-01T00:00:00');
  const [fechaFin, setFechaFin] = useState('2024-01-05T23:59:59');
  const [estacion, setEstacion] = useState('89064');
  const [aggregation, setAggregation] = useState('Daily');
  const [selectedVars, setSelectedVars] = useState<string[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiResponse, setApiResponse] = useState<ApiResponse | null>(null);
  
  const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart');

  const handleVarToggle = (varId: string) => {
    setSelectedVars(prev => 
      prev.includes(varId) ? prev.filter(v => v !== varId) : [...prev, varId]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setApiResponse(null);

    try {
      const response = await fetchMeteoData(fechaIni, fechaFin, estacion, aggregation, selectedVars);
      setApiResponse(response);
    } catch (err: any) {
      setError(err.message || 'Unknown error connecting to the server');
    } finally {
      setLoading(false);
    }
  };

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
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
              <div className="flex flex-col space-y-1">
                <label className="text-sm font-medium text-slate-600">Station (Code/Name)</label>
                <input 
                  type="text" value={estacion} onChange={(e) => setEstacion(e.target.value)}
                  className="px-3 py-2 border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500" required
                />
              </div>
              <div className="flex flex-col space-y-1">
                <label className="text-sm font-medium text-slate-600">Start Date</label>
                <input 
                  type="text" value={fechaIni} onChange={(e) => setFechaIni(e.target.value)}
                  className="px-3 py-2 border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500" required
                />
              </div>
              <div className="flex flex-col space-y-1">
                <label className="text-sm font-medium text-slate-600">End Date</label>
                <input 
                  type="text" value={fechaFin} onChange={(e) => setFechaFin(e.target.value)}
                  className="px-3 py-2 border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500" required
                />
              </div>
              <div className="flex flex-col space-y-1">
                <label className="text-sm font-medium text-slate-600">Aggregation</label>
                <select 
                  value={aggregation} onChange={(e) => setAggregation(e.target.value)}
                  className="px-3 py-2 border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  <option value="None">None (10 min)</option>
                  <option value="Hourly">Hourly</option>
                  <option value="Daily">Daily</option>
                  <option value="Monthly">Monthly</option>
                </select>
              </div>
            </div>

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
                className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-8 rounded-md transition-all flex items-center justify-center min-w-[140px]"
              >
                {loading ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                ) : 'Analyze Data'}
              </button>
            </div>
          </form>
        </div>

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