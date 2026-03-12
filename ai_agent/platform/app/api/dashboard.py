from fastapi import APIRouter

from app.services.mock_data import get_dashboard_context

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary() -> dict:
    return {"summary_cards": get_dashboard_context()["summary_cards"]}


@router.get("/costs")
def dashboard_costs() -> dict:
    context = get_dashboard_context()
    return {"cost_by_agent": context["cost_by_agent"]}


@router.get("/recent-runs")
def dashboard_recent_runs() -> dict:
    context = get_dashboard_context()
    return {
        "recent_runs": context["recent_runs"],
        "failed_runs": context["failed_runs"],
        "approval_queue": context["approval_queue"],
    }
