from __future__ import annotations

from collections import Counter, defaultdict


AGENTS = [
    {
        "agent_id": "agent-webtoon-001",
        "agent_slug": "webtoon",
        "agent_name": "웹툰 자동화 에이전트",
        "description": "독일생활 고양이 네컷 웹툰 자동화 에이전트",
        "status": "active",
        "is_enabled": True,
        "last_run_at": "2026-03-12 09:20",
        "run_count_7d": 5,
        "failed_count_7d": 1,
        "cost_30d": 48.30,
    }
]

RUNS = [
    {
        "run_id": "2026-W11-run-001",
        "agent_slug": "webtoon",
        "agent_name": "웹툰 자동화 에이전트",
        "status": "posted",
        "trigger_type": "manual",
        "started_at": "2026-03-12 09:00",
        "ended_at": "2026-03-12 09:20",
        "total_duration_ms": 1200000,
        "total_cost": 4.86,
        "error_stage": None,
        "error_type": None,
        "error_message": None,
    },
    {
        "run_id": "2026-W12-run-001",
        "agent_slug": "webtoon",
        "agent_name": "웹툰 자동화 에이전트",
        "status": "script_review",
        "trigger_type": "manual",
        "started_at": "2026-03-17 08:30",
        "ended_at": None,
        "total_duration_ms": 420000,
        "total_cost": 1.92,
        "error_stage": None,
        "error_type": None,
        "error_message": None,
    },
    {
        "run_id": "2026-W10-run-002",
        "agent_slug": "webtoon",
        "agent_name": "웹툰 자동화 에이전트",
        "status": "failed",
        "trigger_type": "scheduled",
        "started_at": "2026-03-05 09:10",
        "ended_at": "2026-03-05 09:16",
        "total_duration_ms": 360000,
        "total_cost": 0.87,
        "error_stage": "image_generation",
        "error_type": "external_service_error",
        "error_message": "image model timeout",
    },
]

RUN_LOGS = {
    "2026-W11-run-001": [
        {
            "stage": "topic_selected",
            "status": "done",
            "message": "사용자 주제가 확정되었습니다.",
            "duration_ms": 60000,
        },
        {
            "stage": "script_review",
            "status": "done",
            "message": "스크립트 검토가 완료되었습니다.",
            "duration_ms": 180000,
        },
        {
            "stage": "posted",
            "status": "done",
            "message": "Instagram 게시가 완료되었습니다.",
            "duration_ms": 120000,
        },
    ],
    "2026-W12-run-001": [
        {
            "stage": "topic_selected",
            "status": "done",
            "message": "추천 후보에서 사용자가 주제를 선택했습니다.",
            "duration_ms": 90000,
        },
        {
            "stage": "script_review",
            "status": "waiting",
            "message": "스크립트 승인 대기 중",
            "duration_ms": 210000,
        },
    ],
    "2026-W10-run-002": [
        {
            "stage": "image_generation",
            "status": "failed",
            "message": "이미지 생성 타임아웃",
            "duration_ms": 240000,
        }
    ],
}

RUN_ARTIFACTS = {
    "2026-W11-run-001": [
        {
            "artifact_type": "image",
            "artifact_name": "webtoon_final_v2.png",
            "file_url": "#",
            "version": 2,
        },
        {
            "artifact_type": "json",
            "artifact_name": "script_v2.json",
            "file_url": "#",
            "version": 2,
        },
    ],
    "2026-W12-run-001": [
        {
            "artifact_type": "json",
            "artifact_name": "script_v1.json",
            "file_url": "#",
            "version": 1,
        }
    ],
}

COST_ROWS = [
    {
        "agent_slug": "webtoon",
        "provider": "OpenAI",
        "service_type": "llm",
        "usage_amount": 124000,
        "usage_unit": "tokens",
        "cost_amount": 19.20,
        "recorded_at": "2026-03-12",
        "run_id": "2026-W11-run-001",
    },
    {
        "agent_slug": "webtoon",
        "provider": "ImageAPI",
        "service_type": "image_generation",
        "usage_amount": 12,
        "usage_unit": "images",
        "cost_amount": 21.60,
        "recorded_at": "2026-03-12",
        "run_id": "2026-W11-run-001",
    },
    {
        "agent_slug": "webtoon",
        "provider": "VisionAPI",
        "service_type": "ocr",
        "usage_amount": 34,
        "usage_unit": "requests",
        "cost_amount": 7.50,
        "recorded_at": "2026-03-12",
        "run_id": "2026-W11-run-001",
    },
]

SETTINGS = {
    "timezone": "Europe/Berlin",
    "currency": "USD",
    "notification_channel": "email",
    "default_agent_order": "webtoon",
}


def _find_agent(agent_slug: str) -> dict:
    for agent in AGENTS:
        if agent["agent_slug"] == agent_slug:
            return agent
    raise KeyError(f"Unknown agent: {agent_slug}")


def _find_run(run_id: str) -> dict:
    for run in RUNS:
        if run["run_id"] == run_id:
            return run
    raise KeyError(f"Unknown run: {run_id}")


def get_dashboard_context() -> dict:
    active_agents = sum(1 for agent in AGENTS if agent["is_enabled"])
    failed_runs = [run for run in RUNS if run["status"] in {"failed", "rejected"}]
    pending_runs = [run for run in RUNS if run["status"] in {"script_review", "approved"}]
    total_cost = round(sum(row["cost_amount"] for row in COST_ROWS), 2)
    total_runs = len(RUNS)
    avg_cost = round(total_cost / total_runs, 2) if total_runs else 0

    cost_by_agent = defaultdict(float)
    for row in COST_ROWS:
        cost_by_agent[row["agent_slug"]] += row["cost_amount"]

    cost_by_provider = defaultdict(float)
    for row in COST_ROWS:
        cost_by_provider[row["provider"]] += row["cost_amount"]

    usage_by_service = defaultdict(float)
    for row in COST_ROWS:
        usage_by_service[row["service_type"]] += row["usage_amount"]

    run_trend = [
        {"label": "03/06", "runs": 1, "failed": 0},
        {"label": "03/07", "runs": 0, "failed": 0},
        {"label": "03/08", "runs": 1, "failed": 0},
        {"label": "03/09", "runs": 0, "failed": 0},
        {"label": "03/10", "runs": 1, "failed": 1},
        {"label": "03/11", "runs": 0, "failed": 0},
        {"label": "03/12", "runs": 2, "failed": 0},
    ]
    max_runs = max(point["runs"] for point in run_trend) or 1
    max_failed = max(point["failed"] for point in run_trend) or 1
    trend_points = [
        {
            **point,
            "runs_height": max(14, int(point["runs"] / max_runs * 100)) if point["runs"] else 10,
            "failed_height": max(10, int(point["failed"] / max_failed * 58)) if point["failed"] else 6,
        }
        for point in run_trend
    ]

    provider_total = sum(cost_by_provider.values()) or 1
    provider_chart = [
        {
            "provider": provider,
            "amount": amount,
            "width": max(12, int(amount / provider_total * 100)),
        }
        for provider, amount in sorted(cost_by_provider.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "summary_cards": [
            {"label": "Active Agents", "value": active_agents, "delta": "1 online"},
            {"label": "Runs 7d", "value": total_runs, "delta": "+2 vs prev"},
            {"label": "Failed 7d", "value": len(failed_runs), "delta": "-1 vs prev"},
            {"label": "Cost 7d", "value": f"${total_cost:.2f}", "delta": "+8%"},
        ],
        "recent_runs": RUNS[:5],
        "failed_runs": failed_runs,
        "approval_queue": pending_runs,
        "cost_by_agent": dict(cost_by_agent),
        "provider_chart": provider_chart,
        "trend_points": trend_points,
        "usage_cards": {
            "llm": f"{int(usage_by_service['llm']):,} tokens",
            "image_generation": f"{int(usage_by_service['image_generation'])} images",
            "ocr": f"{int(usage_by_service['ocr'])} requests",
            "google_api": "18 calls",
        },
        "activity_stats": {
            "total_runs": total_runs,
            "run_requests": 12,
            "avg_cost": f"${avg_cost:.2f}",
            "pending_review": len(pending_runs),
        },
    }


def list_agents() -> list[dict]:
    return AGENTS


def get_agent_detail(agent_slug: str) -> dict:
    agent = _find_agent(agent_slug)
    agent_runs = [run for run in RUNS if run["agent_slug"] == agent_slug]
    cost_total = round(sum(row["cost_amount"] for row in COST_ROWS if row["agent_slug"] == agent_slug), 2)
    latest_run = agent_runs[0] if agent_runs else None
    artifacts = RUN_ARTIFACTS.get(latest_run["run_id"], []) if latest_run else []
    return {
        "agent": agent,
        "runs": agent_runs,
        "latest_run": latest_run,
        "cost_total": cost_total,
        "artifacts": artifacts,
    }


def get_runs_for_agent(agent_slug: str) -> list[dict]:
    return [run for run in RUNS if run["agent_slug"] == agent_slug]


def get_run_detail(run_id: str) -> dict:
    run = _find_run(run_id)
    return {
        "run": run,
        "logs": RUN_LOGS.get(run_id, []),
        "artifacts": RUN_ARTIFACTS.get(run_id, []),
        "costs": [row for row in COST_ROWS if row["run_id"] == run_id],
    }


def get_costs_context() -> dict:
    total_cost = round(sum(row["cost_amount"] for row in COST_ROWS), 2)
    by_provider = Counter()
    by_agent = Counter()
    for row in COST_ROWS:
        by_provider[row["provider"]] += row["cost_amount"]
        by_agent[row["agent_slug"]] += row["cost_amount"]
    return {
        "summary_cards": [
            {"label": "Today Cost", "value": "$4.86"},
            {"label": "Cost 7d", "value": "$19.20"},
            {"label": "Cost 30d", "value": "$48.30"},
            {"label": "Monthly Forecast", "value": "$62.00"},
        ],
        "rows": COST_ROWS,
        "total_cost": total_cost,
        "by_provider": dict(by_provider),
        "by_agent": dict(by_agent),
    }


def get_settings_context() -> dict:
    return {"settings": SETTINGS}
