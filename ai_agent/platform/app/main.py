from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.agents import router as agents_api_router
from app.api.costs import router as costs_api_router
from app.api.dashboard import router as dashboard_api_router
from app.api.runs import router as runs_api_router
from app.routes.pages import router as pages_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Agent Platform", version="0.1.0")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(pages_router)
app.include_router(dashboard_api_router)
app.include_router(agents_api_router)
app.include_router(runs_api_router)
app.include_router(costs_api_router)


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}
