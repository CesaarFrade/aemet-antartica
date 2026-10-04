// frontend/src/components/Dashboard.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Dashboard from './Dashboard';

// Hacemos un mock del servicio para aislar el frontend y no llamar al backend real
vi.mock('../services/apiService', () => ({
  fetchStations: vi.fn().mockResolvedValue([
    { id: '89064', name: 'Meteo Station Gabriel de Castilla' }
  ]),
  fetchMeteoData: vi.fn()
}));

describe('Dashboard Component', () => {
  it('renders the main title correctly', () => {
    render(<Dashboard />);
    // Comprueba que el título principal aparece en la pantalla
    expect(screen.getByText(/Antarctica Wind Farm/i)).toBeInTheDocument();
  });

  it('renders the initial loading state for stations', () => {
    render(<Dashboard />);
    // Comprueba que el desplegable muestra el estado de carga al arrancar
    expect(screen.getByText(/Loading stations.../i)).toBeInTheDocument();
  });
});