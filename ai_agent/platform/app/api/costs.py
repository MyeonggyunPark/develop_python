from fastapi import APIRouter

from app.services.mock_data import get_costs_context

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/summary")
def costs_summary() -> dict:
    context = get_costs_context()
    return {"summary_cards": context["summary_cards"], "total_cost": context["total_cost"]}


@router.get("/by-agent")
def costs_by_agent() -> dict:
    return {"by_agent": get_costs_context()["by_agent"]}


@router.get("/by-provider")
def costs_by_provider() -> dict:
    return {"by_provider": get_costs_context()["by_provider"]}
