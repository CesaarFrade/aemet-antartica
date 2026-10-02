from fastapi import FastAPI
from api.routes import router

# Initialize the main FastAPI application
app = FastAPI(title="Antarctica Wind Farm AEMET API")

# --- CORS CONFIGURATION ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the application routes (endpoints) defined in the controllers layer
app.include_router(router)