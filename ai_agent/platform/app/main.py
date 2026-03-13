from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agents import router as agents_api_router
from app.api.costs import router as costs_api_router
from app.api.dashboard import router as dashboard_api_router
from app.api.runs import router as runs_api_router
from app.api.settings import router as settings_api_router

app = FastAPI(title="Agent Platform", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dashboard_api_router)
app.include_router(agents_api_router)
app.include_router(runs_api_router)
app.include_router(costs_api_router)
app.include_router(settings_api_router)


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}
