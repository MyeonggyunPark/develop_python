from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re
from threading import RLock


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE_PATH = DATA_DIR / "webtoon_workspace.json"
STORE_LOCK = RLock()

RECOMMENDATION_POOL = [
    "독일 마트 셀프 계산대에서 생긴 해프닝",
    "겨울 아침 환기 타이밍 때문에 생긴 소동",
    "분리수거 규칙 때문에 벌어진 현실 토론",
    "기차 지연 앱을 보는 둘의 반응 차이",
    "독일 빵집에서 메뉴를 고르는 작은 문화 충격",
]


def _sanitize_public_text(text: str) -> str:
    cleaned = text.strip()
    replacements = (
        ("고양이 웹툰", "생활툰"),
        ("고양이웹툰", "생활툰"),
        ("고양이툰", "생활툰"),
        ("두 고양이", "둘"),
        ("고양이들", "둘"),
        ("고양이", ""),
    )
    for before, after in replacements:
        cleaned = cleaned.replace(before, after)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,")


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _ensure_store() -> dict:
    if not STORE_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STORE_PATH.write_text(json.dumps({"active_runs": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(STORE_PATH.read_text(encoding="utf-8"))


def _save_store(store: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(workspace: dict, stage: str, status: str, message: str) -> None:
    workspace.setdefault("logs", []).insert(
        0,
        {
            "stage": stage,
            "status": status,
            "message": message,
            "timestamp": _utcnow(),
        },
    )


def _build_run_key() -> tuple[str, str]:
    today = datetime.now().isocalendar()
    week_key = f"{today.year}-W{today.week:02d}"
    run_id = f"{week_key}-live"
    return week_key, run_id


def _build_script(topic: str, version: int) -> dict:
    public_topic = _sanitize_public_text(topic) or topic
    panels = [
        {
            "panel_no": 1,
            "title": "상황 시작",
            "dialogue": f"오늘 주제는 '{public_topic}' 이야.",
            "summary": "두 인물이 오늘의 독일생활 상황을 마주한다.",
        },
        {
            "panel_no": 2,
            "title": "첫 시도",
            "dialogue": "생각보다 규칙이 까다롭네?",
            "summary": "현지 생활 규칙이나 문화 차이로 작은 갈등이 생긴다.",
        },
        {
            "panel_no": 3,
            "title": "문제 발생",
            "dialogue": "어? 여기서 막힌다고?",
            "summary": "예상 못 한 변수가 등장하면서 분위기가 급격히 흔들린다.",
        },
        {
            "panel_no": 4,
            "title": "고군분투",
            "dialogue": "잠깐만, 다시 순서 맞춰 보자.",
            "summary": "당황하지만 해결 단서를 찾기 위해 다시 상황을 살핀다.",
        },
        {
            "panel_no": 5,
            "title": "해결",
            "dialogue": "이제 왜 안 됐는지 알겠다!",
            "summary": "문제 원인을 발견하고 흐름이 풀리기 시작한다.",
        },
        {
            "panel_no": 6,
            "title": "마무리",
            "dialogue": "오늘도 독일생활 레벨 업 완료!",
            "summary": "따뜻한 결론과 함께 에피소드가 마무리된다.",
        },
    ]
    return {
        "version": version,
        "title": f"{public_topic} 에피소드",
        "caption": f"{public_topic}을 주제로 한 독일생활 6컷 웹툰 캡션 초안입니다.",
        "panels": panels,
    }


def _get_workspace(agent_slug: str, store: dict) -> dict | None:
    return store["active_runs"].get(agent_slug)


def get_workspace(agent_slug: str) -> dict | None:
    with STORE_LOCK:
        store = _ensure_store()
        return _get_workspace(agent_slug, store)


def start_workspace(agent_slug: str) -> dict:
    with STORE_LOCK:
        store = _ensure_store()
        week_key, run_id = _build_run_key()
        workspace = {
            "agent_slug": agent_slug,
            "week_key": week_key,
            "run_id": run_id,
            "status": "awaiting_topic_input",
            "topic": None,
            "recommendations": [],
            "script": None,
            "artifacts": [],
            "logs": [],
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        _append_log(workspace, "run_started", "done", "새 실행이 시작되었습니다.")
        store["active_runs"][agent_slug] = workspace
        _save_store(store)
        return workspace


def _require_workspace(agent_slug: str, store: dict) -> dict:
    workspace = _get_workspace(agent_slug, store)
    if workspace is None:
        workspace = start_workspace(agent_slug)
        store = _ensure_store()
        return store["active_runs"][agent_slug]
    return workspace


def request_recommendations(agent_slug: str) -> dict:
    with STORE_LOCK:
        store = _ensure_store()
        workspace = _require_workspace(agent_slug, store)
        workspace["status"] = "topic_recommended"
        workspace["recommendations"] = RECOMMENDATION_POOL[:4]
        workspace["updated_at"] = _utcnow()
        _append_log(workspace, "topic_recommended", "done", "추천 주제 후보를 생성했습니다.")
        _save_store(store)
        return workspace


def submit_topic(agent_slug: str, topic: str) -> dict:
    with STORE_LOCK:
        store = _ensure_store()
        workspace = _require_workspace(agent_slug, store)
        normalized_topic = topic.strip()
        script_version = (workspace.get("script") or {}).get("version", 0) + 1

        workspace["topic"] = normalized_topic
        workspace["status"] = "script_review"
        workspace["recommendations"] = []
        workspace["script"] = _build_script(normalized_topic, script_version)
        workspace["updated_at"] = _utcnow()
        _append_log(workspace, "topic_selected", "done", f"주제 '{normalized_topic}'가 확정되었습니다.")
        _append_log(workspace, "script_review", "waiting", "스크립트 초안을 검토해주세요.")
        _save_store(store)
        return workspace


def select_recommendation(agent_slug: str, topic: str) -> dict:
    return submit_topic(agent_slug, topic)


def regenerate_script(agent_slug: str) -> dict:
    with STORE_LOCK:
        store = _ensure_store()
        workspace = _require_workspace(agent_slug, store)
        topic = workspace.get("topic")
        if not topic:
            raise ValueError("topic_required")
        script_version = (workspace.get("script") or {}).get("version", 0) + 1
        workspace["status"] = "script_review"
        workspace["script"] = _build_script(topic, script_version)
        workspace["updated_at"] = _utcnow()
        _append_log(workspace, "script_generated", "done", f"스크립트 v{script_version}를 다시 생성했습니다.")
        _save_store(store)
        return workspace


def finalize_run(agent_slug: str) -> dict:
    with STORE_LOCK:
        store = _ensure_store()
        workspace = _require_workspace(agent_slug, store)
        script = workspace.get("script")
        topic = workspace.get("topic")
        if not script or not topic:
            raise ValueError("script_required")

        version = script["version"]
        workspace["status"] = "approved"
        workspace["artifacts"] = [
            {
                "artifact_type": "json",
                "artifact_name": f"script_v{version}.json",
                "file_url": "#",
                "version": version,
            },
            {
                "artifact_type": "image",
                "artifact_name": f"webtoon_final_v{version}.png",
                "file_url": "#",
                "version": version,
            },
        ]
        workspace["updated_at"] = _utcnow()
        _append_log(workspace, "approved", "done", f"'{topic}' 주제의 최종 결과물을 생성했습니다.")
        _save_store(store)
        return workspace
