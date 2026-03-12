from fastapi import APIRouter

from app.services.mock_data import get_run_detail

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/{run_id}")
def run_detail(run_id: str) -> dict:
    return get_run_detail(run_id)


@router.get("/{run_id}/logs")
def run_logs(run_id: str) -> dict:
    return {"logs": get_run_detail(run_id)["logs"]}


@router.get("/{run_id}/artifacts")
def run_artifacts(run_id: str) -> dict:
    return {"artifacts": get_run_detail(run_id)["artifacts"]}
