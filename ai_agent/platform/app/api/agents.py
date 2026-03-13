from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.mock_data import get_agent_detail, get_runs_for_agent, list_agents
from app.services.webtoon_workspace import (
    finalize_run,
    get_workspace,
    regenerate_script,
    request_recommendations,
    select_recommendation,
    start_workspace,
    submit_topic,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class TopicPayload(BaseModel):
    topic: str


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


@router.get("/{agent_slug}/workspace")
def agent_workspace(agent_slug: str) -> dict:
    return {"workspace": get_workspace(agent_slug)}


@router.post("/{agent_slug}/workspace/start")
def agent_workspace_start(agent_slug: str) -> dict:
    return {"workspace": start_workspace(agent_slug)}


@router.post("/{agent_slug}/workspace/request-recommendations")
def agent_workspace_request_recommendations(agent_slug: str) -> dict:
    return {"workspace": request_recommendations(agent_slug)}


@router.post("/{agent_slug}/workspace/submit-topic")
def agent_workspace_submit_topic(agent_slug: str, payload: TopicPayload) -> dict:
    if not payload.topic.strip():
        raise HTTPException(status_code=400, detail="topic_required")
    return {"workspace": submit_topic(agent_slug, payload.topic)}


@router.post("/{agent_slug}/workspace/select-topic")
def agent_workspace_select_topic(agent_slug: str, payload: TopicPayload) -> dict:
    if not payload.topic.strip():
        raise HTTPException(status_code=400, detail="topic_required")
    return {"workspace": select_recommendation(agent_slug, payload.topic)}


@router.post("/{agent_slug}/workspace/regenerate-script")
def agent_workspace_regenerate_script(agent_slug: str) -> dict:
    try:
        return {"workspace": regenerate_script(agent_slug)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/{agent_slug}/workspace/finalize")
def agent_workspace_finalize(agent_slug: str) -> dict:
    try:
        return {"workspace": finalize_run(agent_slug)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
