from fastapi import APIRouter

from app.services.mock_data import get_agent_detail, get_runs_for_agent, list_agents

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def agents_list() -> dict:
    return {"agents": list_agents()}


@router.get("/{agent_slug}")
def agent_detail(agent_slug: str) -> dict:
    return get_agent_detail(agent_slug)


@router.post("/{agent_slug}/runs")
def trigger_agent_run(agent_slug: str) -> dict:
    agent = get_agent_detail(agent_slug)["agent"]
    return {"message": f"{agent['agent_name']} manual run requested", "agent_slug": agent_slug}


@router.get("/{agent_slug}/runs")
def agent_runs(agent_slug: str) -> dict:
    return {"runs": get_runs_for_agent(agent_slug)}
