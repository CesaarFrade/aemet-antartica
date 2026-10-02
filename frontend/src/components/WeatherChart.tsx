import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer 
} from 'recharts';
import type { MeteoData } from '../types/api';

interface WeatherChartProps {
  data: MeteoData[];
}

export default function WeatherChart({ data }: WeatherChartProps) {
  const formatXAxis = (tickItem: string) => {
    const date = new Date(tickItem);
    return `${date.getDate()}/${date.getMonth() + 1} ${date.getHours().toString().padStart(2, '0')}:00`;
  };

  return (
    <div className="w-full h-96 mt-6 bg-white p-4 rounded-xl border border-slate-100 shadow-sm">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
          
          <XAxis 
            dataKey="Datetime" 
            tickFormatter={formatXAxis} 
            stroke="#94a3b8" 
            fontSize={12} 
            tickMargin={10}
          />
          
          <YAxis yAxisId="left" stroke="#ef4444" fontSize={12} tickFormatter={(val) => `${val}º`} />
          <YAxis yAxisId="right" orientation="right" stroke="#3b82f6" fontSize={12} />
          
          <Tooltip 
            contentStyle={{ backgroundColor: '#0f172a', borderRadius: '8px', border: 'none', color: '#f8fafc' }}
            itemStyle={{ color: '#cbd5e1' }}
            labelStyle={{ color: '#94a3b8', marginBottom: '8px', fontWeight: 'bold' }}
          />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />
          
          <Line yAxisId="left" type="monotone" dataKey="Temperature (ºC)" stroke="#ef4444" strokeWidth={3} dot={false} name="Temp (ºC)" activeDot={{ r: 6 }} />
          <Line yAxisId="right" type="monotone" dataKey="Speed (m/s)" stroke="#3b82f6" strokeWidth={3} dot={false} name="Wind Speed (m/s)" />
          <Line yAxisId="right" type="monotone" dataKey="Pressure (hpa)" stroke="#10b981" strokeWidth={3} dot={false} name="Pressure (hpa)" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}