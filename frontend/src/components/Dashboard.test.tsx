// frontend/src/components/Dashboard.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchStations, fetchMeteoData } from '../services/apiService';
import Dashboard from './Dashboard';

// The API client is mocked so the suite never reaches the real backend, mirroring
// the network guard the backend test suite installs. Every test picks its own
// resolved value, which is what lets one file cover both the happy path and the
// failure path.
vi.mock('../services/apiService', () => ({
  fetchStations: vi.fn(),
  fetchMeteoData: vi.fn(),
}));

// Recharts renders through a responsive container that needs a real layout engine,
// so the chart is stubbed out: these tests are about the dashboard's own contract
// with the API, not about Recharts.
vi.mock('./WeatherChart', () => ({
  default: () => <div data-testid="weather-chart" />,
}));

const GABRIEL = { id: '89064', name: 'Meteo Station Gabriel de Castilla' };
const JUAN_CARLOS = { id: '89070', name: 'Meteo Station Juan Carlos I' };

const SAMPLE_RESPONSE = {
  status: 'success',
  station_requested: 'Meteo Station Gabriel de Castilla',
  station_resolved: '89064',
  location_requested: 'UTC',
  location_resolved: 'Europe/Madrid',
  data_types_filtered: 'All',
  data: [
    {
      Station: 'Meteo Station Gabriel de Castilla',
      Datetime: '2024-01-01T00:00:00+01:00',
      'Temperature (ºC)': 2.4,
      'Pressure (hpa)': 1005.1,
      'Speed (m/s)': 11.2,
    },
    {
      Station: 'Meteo Station Gabriel de Castilla',
      Datetime: '2024-01-02T00:00:00+01:00',
      'Temperature (ºC)': -3.1,
      'Pressure (hpa)': 1002.4,
      'Speed (m/s)': 8.6,
    },
  ],
};

/** Renders the dashboard and waits for the station list to populate the picker. */
async function renderWithStations(stations = [GABRIEL, JUAN_CARLOS]) {
  vi.mocked(fetchStations).mockResolvedValue(stations);
  render(<Dashboard />);
  return screen.findByRole('option', { name: /Gabriel de Castilla/ });
}

describe('Dashboard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the main title correctly', async () => {
    await renderWithStations();
    expect(screen.getByText(/Antarctica Wind Farm/i)).toBeInTheDocument();
  });

  it('renders the initial loading state for stations', () => {
    // The promise is never resolved, so the picker stays in its loading placeholder.
    vi.mocked(fetchStations).mockReturnValue(new Promise(() => {}));
    render(<Dashboard />);
    expect(screen.getByText(/Loading stations.../i)).toBeInTheDocument();
  });

  it('populates the station picker from the API instead of hardcoded ids', async () => {
    await renderWithStations();

    // Both registered stations reach the UI, which is what keeps the dashboard from
    // ever offering an identifier the backend does not know about.
    expect(screen.getByRole('option', { name: /Gabriel de Castilla \(89064\)/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Juan Carlos I \(89070\)/ })).toBeInTheDocument();

    // The placeholder is replaced once the list arrives.
    expect(screen.queryByText(/Loading stations.../i)).not.toBeInTheDocument();

    // The first station is pre-selected, matching the pre-filled date defaults.
    expect(screen.getByLabelText('Station')).toHaveValue('89064');
  });

  it('renders the KPI cards and results after a successful query', async () => {
    vi.mocked(fetchMeteoData).mockResolvedValue(SAMPLE_RESPONSE);
    await renderWithStations([GABRIEL]);

    fireEvent.click(screen.getByRole('button', { name: /Analyze Data/i }));

    expect(await screen.findByText(/Max Temperature/i)).toBeInTheDocument();

    // KPIs are derived, not echoed: max of the two temperatures (2.4), and the mean
    // wind speed ((11.2 + 8.6) / 2 = 9.9) over the returned window.
    expect(screen.getByText('2.4 ºC')).toBeInTheDocument();
    expect(screen.getByText('9.9 m/s')).toBeInTheDocument();
    expect(screen.getByText('Data Points Analyzed')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();

    // The resolved station name is shown, so the user can confirm what was queried.
    expect(screen.getByText(/Results: Meteo Station Gabriel de Castilla/)).toBeInTheDocument();
    expect(screen.getByTestId('weather-chart')).toBeInTheDocument();

    // The filters are forwarded verbatim: pre-filled dates, the pre-selected station,
    // the default 'Daily' aggregation, no data type restriction and no location, which
    // the backend reads as UTC.
    expect(fetchMeteoData).toHaveBeenCalledWith(
      '2024-01-01T00:00:00',
      '2024-01-05T23:59:59',
      '89064',
      'Daily',
      [],
      '',
    );
  });

  it('offers both location forms the endpoint accepts and defaults to UTC', async () => {
    await renderWithStations([GABRIEL]);

    // The brief allows either an IANA zone or a fixed offset, and a stale value would
    // be answered with a 400, so both shapes have to be reachable from the UI.
    expect(screen.getByRole('group', { name: 'Named time zone (IANA)' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Fixed offset (DST ignored)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Europe/Madrid (CET/CEST)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '+02:00 (CEST, fixed)' })).toBeInTheDocument();

    // Nothing is chosen by default, which keeps the request on the UTC assumption
    // the endpoint already made.
    expect(screen.getByLabelText('Location')).toHaveValue('');
    expect(screen.getByTestId('location-hint')).toHaveTextContent('UTC (AEMET publishes on)');
  });

  it('sends the selected location so wall-clock dates are not read as UTC', async () => {
    vi.mocked(fetchMeteoData).mockResolvedValue(SAMPLE_RESPONSE);
    await renderWithStations([GABRIEL]);

    fireEvent.change(screen.getByLabelText('Location'), {
      target: { value: 'Europe/Berlin' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Analyze Data/i }));

    expect(await screen.findByText(/Max Temperature/i)).toBeInTheDocument();

    // The choice reaches the API verbatim; the backend resolves it with ZoneInfo, so
    // a DST-aware zone and a fixed offset are not interchangeable.
    expect(fetchMeteoData).toHaveBeenCalledWith(
      '2024-01-01T00:00:00',
      '2024-01-05T23:59:59',
      '89064',
      'Daily',
      [],
      'Europe/Berlin',
    );

    // The form states how it read the dates, because a wrong zone shifts the whole
    // requested window by hours without looking like an error.
    expect(screen.getByTestId('location-hint')).toHaveTextContent('Europe/Berlin');
  });

  it('surfaces an upstream failure instead of showing an empty result', async () => {
    vi.mocked(fetchMeteoData).mockRejectedValue(
      new Error('Upstream AEMET API unavailable: 502 Bad Gateway'),
    );
    await renderWithStations([GABRIEL]);

    fireEvent.click(screen.getByRole('button', { name: /Analyze Data/i }));

    // The backend distinguishes a gateway failure from a station that published
    // nothing, so the UI has to show it as an error and not as "no data".
    expect(await screen.findByText(/Data Fetch Error/i)).toBeInTheDocument();
    expect(screen.getByText(/Upstream AEMET API unavailable/i)).toBeInTheDocument();
    expect(screen.queryByTestId('weather-chart')).not.toBeInTheDocument();

    // The form recovers: the button is re-enabled once the request settles.
    expect(screen.getByRole('button', { name: /Analyze Data/i })).toBeEnabled();
  });
});
