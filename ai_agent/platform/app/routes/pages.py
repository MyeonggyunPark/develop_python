from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.mock_data import (
    get_agent_detail,
    get_costs_context,
    get_dashboard_context,
    get_run_detail,
    get_runs_for_agent,
    get_settings_context,
    list_agents,
)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

STATUS_LABELS = {
    "active": "Active",
    "idle": "Idle",
    "script_review": "In Review",
    "approved": "Approved",
    "posted": "Posted",
    "failed": "Failed",
    "rejected": "Rejected",
    "disabled": "Disabled",
}

TRIGGER_LABELS = {
    "manual": "Manual",
    "scheduled": "Scheduled",
}


def render(request: Request, template_name: str, context: dict) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "request": request,
            "page_title": context.get("page_title", "Agent Platform"),
            "active_nav": context.get("active_nav", "dashboard"),
            "status_labels": STATUS_LABELS,
            "trigger_labels": TRIGGER_LABELS,
            **context,
        },
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    context = get_dashboard_context()
    context.update({"page_title": "Dashboard", "active_nav": "dashboard"})
    return render(request, "dashboard.html", context)


@router.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "agents/list.html",
        {
            "page_title": "Agents",
            "active_nav": "agents",
            "agents": list_agents(),
        },
    )


@router.get("/agents/{agent_slug}", response_class=HTMLResponse)
def agent_detail_page(request: Request, agent_slug: str) -> HTMLResponse:
    context = get_agent_detail(agent_slug)
    context.update({"page_title": f"{context['agent']['agent_name']} Overview", "active_nav": "agents"})
    return render(request, "agents/detail.html", context)


@router.get("/agents/{agent_slug}/runs", response_class=HTMLResponse)
def agent_runs_page(request: Request, agent_slug: str) -> HTMLResponse:
    agent = get_agent_detail(agent_slug)["agent"]
    return render(
        request,
        "agents/runs.html",
        {
            "page_title": f"{agent['agent_name']} Run History",
            "active_nav": "agents",
            "agent": agent,
            "runs": get_runs_for_agent(agent_slug),
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(request: Request, run_id: str) -> HTMLResponse:
    context = get_run_detail(run_id)
    context.update({"page_title": f"Run Detail {run_id}", "active_nav": "agents"})
    return render(request, "runs/detail.html", context)


@router.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request) -> HTMLResponse:
    context = get_costs_context()
    context.update({"page_title": "Costs", "active_nav": "costs"})
    return render(request, "costs/index.html", context)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    context = get_settings_context()
    context.update({"page_title": "Settings", "active_nav": "settings"})
    return render(request, "settings/index.html", context)
