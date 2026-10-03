import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router

# Initialize the main FastAPI application
app = FastAPI(
    title="Antarctica Wind Farm AEMET API",
    description=(
        "Timeseries of AEMET measurements for the Antarctic stations, cached in "
        "SQLite so the source API is queried once per station per TTL window."
    ),
    version="1.0.0",
)

# --- CORS CONFIGURATION ---
# The dashboard is served from a different origin (Vite on :5173), so CORS has to
# be enabled. Credentials are NOT allowed: the spec forbids combining them with a
# wildcard origin, and browsers reject such a response outright, which would break
# every call from the frontend. Listing the dev origins explicitly keeps cookies
# out of it, which is all this API needs since it carries no session.
#
# Override with CORS_ORIGINS (comma-separated) to point at a deployed dashboard.
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Register the application routes (endpoints) defined in the controllers layer
app.include_router(router)


@app.get("/health", tags=["operational"])
def health():
    """
    Liveness probe for the operational side of the service.

    Part 2 of the brief asks for high availability during business hours, and a
    load balancer cannot route around an instance that is up but broken. This
    deliberately touches neither AEMET nor SQLite: it reports that the process is
    serving, so it stays green during an upstream outage, which is exactly when an
    operator needs to tell "we are broken" apart from "AEMET is down".
    """
    return {"status": "ok"}