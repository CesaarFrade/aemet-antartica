# Antarctica Wind Farm - AEMET API Service

## 1. Executive Summary
This project provides a full-stack web service to retrieve, aggregate, and visualize historical weather data from the AEMET API for meteorological stations in Antarctica. Developed to support the Analytical team's feasibility study for a future Wind Farm, the solution features a backend cache (SQLite) to manage high-volume trader requests without overloading the source API, alongside a frontend interface for data visualization.

## 2. Key Features (Core API)
- **Clean Architecture & Modular Design:** The codebase is structured into logical, decoupled layers (`api/`, `core/`, `models/`, `services/`). This separation of concerns ensures high maintainability, readability, and readiness for future scalability.
- **HATEOAS Integration:** Seamlessly handles the AEMET two-step data retrieval process.
- **Smart Database Caching (SQLite):** Implements an interception layer using SQLAlchemy ORM. Caches historical data locally to prevent source API overload, delivering millisecond response times for intraday traders (Cache Hit/Miss logic).
- **Professional Logging:** Centralized tracking of cache operations, data ingestion, and system behavior to facilitate robust troubleshooting and monitoring.
- **Data Transformation:** Uses `pandas` for highly efficient filtering and column mapping.
- **Timezone & DST Handling:** Converts all UTC timestamps to `Europe/Madrid` (CET/CEST) dynamically, ensuring strict Daylight-Saving Time (DST) compliance.
- **Time Aggregation:** Supports `Hourly`, `Daily`, and `Monthly` data resampling (calculating the mean of numerical variables) directly at the Madrid local midnight boundaries.
- **Defensive Programming & Validation:** Strict input validation for ISO 8601 date formats and temporal coherence. Invalid requests are caught at the API boundary, returning descriptive 400 Bad Request errors to prevent unnecessary downstream processing.

## 3. Prerequisites
- Python 3.9 or higher.
- A personal AEMET OpenData API Key (Corporate emails might be blocked by the agency).

## 4. Setup & Installation

**1. Clone the repository:**
```bash
git clone https://github.com/CesaarFrade/aemet-antartica
cd aemet-antartica/backend
```

**2. Create and activate a virtual environment:**
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
```

**3. Install dependencies:**
```bash
pip install -r requirements.txt
```

**4. Environment Variables:**
Create a `.env` file in the `backend` directory and add your AEMET API key. This ensures credentials remain secure and are not committed to version control:
```env
AEMET_API_KEY=your_personal_api_key_here
```

## 5. Running the Service

Start the FastAPI server using Uvicorn:
```bash
uvicorn main:app --reload
```

## 6. API Documentation & Usage
Once the server is running, navigate to [http://localhost:8000/docs](http://localhost:8000/docs) in your web browser. 

FastAPI automatically generates an interactive Swagger UI documentation where you can test the endpoints, pass datetime parameters, filter required data types, and review the timezone-aware JSON responses.

## 7. Testing
Automated unit tests have been implemented using `pytest` and FastAPI's `TestClient` to ensure API reliability and logical accuracy. The test suite validates the defensive error-handling mechanisms and utilizes mocking (`@patch`) to deterministically test the Pandas aggregation logic without consuming external API quotas.

To run the test suite, ensure your virtual environment is active and execute:
```bash
pytest
