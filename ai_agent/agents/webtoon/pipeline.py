from __future__ import annotations

import copy
import json
import logging
import mimetypes
import re
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import io
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

logger = logging.getLogger(__name__)

from .clients import (
    flatten_speaker_dialogues,
    GeminiOcrClient,
    GeminiTextClient,
    GeminiImageClient,
    GoogleWorkspaceClient,
    normalize_speaker_dialogues,
    convert_image_bytes,
    save_image,
    sanitize_public_text,
)
from .config import WebtoonSettings
from .text_renderer import render_thumbnail_card

PREFERRED_CHARACTER_REFERENCE_FILES = [
    "black_cat.png",
    "gray_cat.png",
    "character_sheet.png",
]
MAX_WEBTOON_PANEL_COUNT = 6
MIN_SOFT_SCORE = 0.72
MIN_AVERAGE_SOFT_SCORE = 0.82
MAX_TARGETED_REPLAN_ROUNDS = 1
PORTABLE_PROP_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("여권", ("여권", "passport", "reisepass", "pass")),
    ("티켓", ("티켓", "ticket", "boarding pass", "board pass", "fahrkarte")),
    ("탑승권", ("탑승권", "boarding", "boardingpass")),
    ("캐리어", ("캐리어", "suitcase", "luggage", "roller bag")),
    ("가방", ("가방", "bag", "backpack", "rucksack")),
    ("지도", ("지도", "map")),
    ("휴대폰", ("휴대폰", "phone", "smartphone", "mobile")),
    ("문서철", ("문서철", "folder", "document folder", "file holder")),
    ("도장", ("도장", "stamp", "stempel")),
    ("수하물 카트", ("카트", "cart", "trolley")),
    ("프레첼", ("프레첼", "pretzel")),
)


@dataclass
class PipelineArtifacts:
    run_dir: Path
    run_id: str
    week_key: str
    metadata_path: Path
    publish_result_path: Path


@dataclass(frozen=True)
class OutboundArtifact:
    source_path: Path
    bundle_name: str
    mime_type: str
    make_public: bool
    artifact_role: str
    slide_index: int | None = None
    slide_type: str = ""
    panel_no: int | None = None


@dataclass(frozen=True)
class OutboundBundle:
    run_id: str
    week_key: str
    drive_path_parts: tuple[str, ...]
    upload_artifacts: tuple[OutboundArtifact, ...]
    metadata_payload: dict[str, Any]
    json_payloads: tuple[tuple[str, dict[str, Any]], ...]
    quality_report: dict[str, Any]
    status: str
    approved_by: str
    approved_at: str
    approved_image_version: int | str
    slides: tuple[dict[str, Any], ...]


def _write_json_artifact(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_pipeline_stage(
    artifact_paths: PipelineArtifacts,
    stage: str,
    *,
    topic: str = "",
    notes: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "run_id": artifact_paths.run_id,
        "week_key": artifact_paths.week_key,
        "run_dir": str(artifact_paths.run_dir),
        "stage": stage,
        "topic": topic,
        "notes": notes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload["extra"] = extra
    _write_json_artifact(artifact_paths.run_dir / "pipeline_status.json", payload)


def _write_pipeline_failure(
    artifact_paths: PipelineArtifacts,
    *,
    topic: str,
    notes: str,
    exc: BaseException,
) -> None:
    status_path = artifact_paths.run_dir / "pipeline_status.json"
    failed_stage = "unknown"
    if status_path.exists():
        try:
            failed_stage = json.loads(status_path.read_text(encoding="utf-8")).get("stage", failed_stage)
        except Exception:
            failed_stage = "unknown"
    _write_json_artifact(
        artifact_paths.run_dir / "pipeline_failure.json",
        {
            "run_id": artifact_paths.run_id,
            "week_key": artifact_paths.week_key,
            "run_dir": str(artifact_paths.run_dir),
            "topic": topic,
            "notes": notes,
            "failed_stage": failed_stage,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _build_uploaded_files_by_role(uploaded_files: list[dict[str, Any]]) -> dict[str, Any]:
    uploaded_files_by_role: dict[str, Any] = {"thumbnail": {}, "panels": []}
    for uploaded_entry in uploaded_files:
        slide_type = uploaded_entry.get("slide_type")
        if slide_type == "thumbnail":
            if uploaded_entry.get("artifact_role") == "thumbnail_base_png":
                uploaded_files_by_role["thumbnail"]["base_png"] = uploaded_entry["uploaded_file"]
            elif uploaded_entry.get("artifact_role") == "thumbnail_final_png":
                uploaded_files_by_role["thumbnail"]["final_png"] = uploaded_entry["uploaded_file"]
            elif uploaded_entry.get("artifact_role") == "thumbnail_publish_jpg":
                uploaded_files_by_role["thumbnail"]["publish_jpg"] = uploaded_entry["uploaded_file"]
        elif slide_type == "panel":
            panel_entry = next(
                (
                    item
                    for item in uploaded_files_by_role["panels"]
                    if item["slide_index"] == uploaded_entry.get("slide_index")
                ),
                None,
            )
            if panel_entry is None:
                panel_entry = {
                    "slide_index": uploaded_entry.get("slide_index"),
                    "slide_type": "panel",
                    "panel_no": uploaded_entry.get("panel_no"),
                }
                uploaded_files_by_role["panels"].append(panel_entry)
            if uploaded_entry.get("artifact_role") == "panel_final_png":
                panel_entry["final_png"] = uploaded_entry["uploaded_file"]
            elif uploaded_entry.get("artifact_role") == "panel_publish_jpg":
                panel_entry["publish_jpg"] = uploaded_entry["uploaded_file"]
    uploaded_files_by_role["panels"].sort(key=lambda item: item.get("slide_index", 0))
    return uploaded_files_by_role


def _materialize_panels_from_brief(brief: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "panel_no": int(panel.get("panel_no", index)),
            "story_role": str(panel.get("story_role", "")).strip(),
            "location": str(panel.get("location", "")).strip(),
            "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
            "key_props": list(panel.get("key_props", [])),
            "carryover_props": list(panel.get("carryover_props", [])),
            "speaker_dialogues": normalize_speaker_dialogues(panel),
            "dialogue_lines": flatten_speaker_dialogues(normalize_speaker_dialogues(panel)),
        }
        for index, panel in enumerate(brief.get("panels", [])[:MAX_WEBTOON_PANEL_COUNT], start=1)
    ]


def _build_panel_summaries(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "panel_no": panel.get("panel_no"),
            "story_role": panel.get("story_role", ""),
            "location": panel.get("location", ""),
            "scene_prompt": panel.get("scene_prompt", ""),
            "key_props": panel.get("key_props", []),
            "carryover_props": panel.get("carryover_props", []),
        }
        for panel in panels
    ]


def _speaker_display_name(speaker: str) -> str:
    mapping = {
        "kolla": "콜라",
        "cola": "콜라",
        "zero": "제로",
    }
    return mapping.get(str(speaker).strip().lower(), str(speaker).strip() or "알 수 없음")


def _speaker_export_id(speaker: str) -> str:
    normalized = str(speaker).strip().lower()
    if normalized == "kolla":
        return "cola"
    return normalized


def _build_dialogue_export_payload(*, topic: str, title: str, panels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "topic": topic,
        "title": title,
        "panels": [
            {
                "panel_no": int(panel.get("panel_no", 0) or 0),
                "story_role": str(panel.get("story_role", "")).strip(),
                "location": str(panel.get("location", "")).strip(),
                "dialogues": [
                    {
                        "speaker": _speaker_export_id(str(block.get("speaker", "")).strip()),
                        "character_name": _speaker_display_name(str(block.get("speaker", "")).strip()),
                        "dialogue_lines": [str(line).strip() for line in block.get("dialogue_lines", []) if str(line).strip()],
                    }
                    for block in panel.get("speaker_dialogues", [])
                    if str(block.get("speaker", "")).strip()
                ],
            }
            for panel in panels
        ],
    }


def _build_video_prompt_export_payload(*, topic: str, title: str, panels: list[dict[str, Any]]) -> dict[str, Any]:
    prompt_items: list[dict[str, Any]] = []
    previous_location = ""
    for panel in panels:
        panel_no = int(panel.get("panel_no", 0) or 0)
        location = str(panel.get("location", "")).strip()
        scene_prompt = str(panel.get("scene_prompt", "")).strip()
        story_role = str(panel.get("story_role", "")).strip()
        key_props = [str(item).strip() for item in panel.get("key_props", []) if str(item).strip()]
        carryover_props = [str(item).strip() for item in panel.get("carryover_props", []) if str(item).strip()]
        dialogue_summary = " / ".join(
            f"{_speaker_display_name(str(block.get('speaker', '')).strip())}: "
            + " ".join(str(line).strip() for line in block.get("dialogue_lines", []) if str(line).strip())
            for block in panel.get("speaker_dialogues", [])
            if str(block.get("speaker", "")).strip()
        )
        continuity_parts = []
        if previous_location and previous_location != location:
            continuity_parts.append(f"Previous panel location was {previous_location}.")
        if carryover_props:
            continuity_parts.append(f"Keep the same recurring props visible and consistent: {', '.join(carryover_props)}.")
        continuity_parts.append("Keep exactly two recurring cat protagonists only: Kolla on the left, Zero on the right.")
        continuity_parts.append("Keep both characters upright and bipedal, with Kolla slightly larger than Zero.")
        if key_props:
            continuity_parts.append(f"Key props in this scene: {', '.join(key_props)}.")
        if dialogue_summary:
            continuity_parts.append(f"Dialogue context for the scene: {dialogue_summary}.")
        continuity_parts.append("No speech bubbles, captions, subtitles, or on-screen text overlays.")
        prompt_items.append(
            {
                "panel_no": panel_no,
                "story_role": story_role,
                "location": location,
                "scene_prompt": scene_prompt,
                "video_prompt": " ".join(
                    part for part in [
                        "Animate this exact webtoon panel as a short video scene.",
                        f"Scene setting: {location}." if location else "",
                        f"Core action: {scene_prompt}." if scene_prompt else "",
                        *continuity_parts,
                    ]
                    if part
                ),
            }
        )
        previous_location = location

    return {
        "topic": topic,
        "title": title,
        "panels": prompt_items,
    }


def _build_slide_outputs(
    *,
    artifact_paths: PipelineArtifacts,
    thumbnail_final_png_bytes: bytes,
    thumbnail_final_path: Path,
    thumbnail_publish_path: Path,
    panel_base_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slide_outputs: list[dict[str, Any]] = [
        {
            "slide_index": 1,
            "slide_type": "thumbnail",
            "png_bytes": thumbnail_final_png_bytes,
            "jpg_bytes": thumbnail_publish_path.read_bytes(),
            "final_path": thumbnail_final_path,
            "publish_path": thumbnail_publish_path,
        }
    ]
    for panel_base in panel_base_items:
        panel_no = int(panel_base.get("panel_no", 0) or 0)
        panel_png_bytes = panel_base["base_bytes"]
        panel_final_path = build_versioned_path(artifact_paths.run_dir, f"panel_{panel_no:02d}_final", 1, "png")
        panel_publish_path = build_versioned_path(artifact_paths.run_dir, f"panel_{panel_no:02d}_publish", 1, "jpg")
        panel_publish_jpg_bytes = convert_image_bytes(panel_png_bytes, "JPEG")
        save_image(panel_png_bytes, panel_final_path)
        save_image(panel_publish_jpg_bytes, panel_publish_path)
        slide_outputs.append(
            {
                "slide_index": panel_no + 1,
                "slide_type": "panel",
                "panel_no": panel_no,
                "png_bytes": panel_png_bytes,
                "jpg_bytes": panel_publish_jpg_bytes,
                "final_path": panel_final_path,
                "publish_path": panel_publish_path,
            }
        )
    return slide_outputs


def _build_panel_dialogue_payload(
    *,
    run_id: str,
    topic: str,
    title: str,
    panels: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "topic": topic,
        "title": title,
        "panels": [
            {
                "panel_no": int(panel.get("panel_no", 0) or 0),
                "story_role": str(panel.get("story_role", "")).strip(),
                "location": str(panel.get("location", "")).strip(),
                "speaker_dialogues": normalize_speaker_dialogues(panel),
            }
            for panel in panels
        ],
    }


def _build_video_scene_prompt_payload(
    *,
    run_id: str,
    topic: str,
    title: str,
    subtitle: str,
    scope_summary: str,
    panels: list[dict[str, Any]],
    panel_base_items: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_items: list[dict[str, Any]] = []
    for panel, panel_base in zip(panels, panel_base_items, strict=True):
        panel_no = int(panel.get("panel_no", 0) or 0)
        key_props = ", ".join(str(item).strip() for item in panel.get("key_props", []) if str(item).strip()) or "없음"
        carryover_props = (
            ", ".join(str(item).strip() for item in panel.get("carryover_props", []) if str(item).strip()) or "없음"
        )
        dialogue_beats = []
        for block in normalize_speaker_dialogues(panel):
            speaker = str(block.get("speaker", "")).strip() or "unknown"
            lines = [str(line).strip() for line in block.get("dialogue_lines", []) if str(line).strip()]
            dialogue_beats.append({"speaker": speaker, "lines": lines})
        video_prompt = (
            f"독일생활 적응기 에피소드 장면 {panel_no}. "
            f"제목은 '{title}'이고 부제는 '{subtitle}'이다. "
            f"장소는 {str(panel.get('location', '')).strip()}이며 장면 핵심은 {str(panel.get('scene_prompt', '')).strip()}이다. "
            f"서사 역할은 {str(panel.get('story_role', '')).strip()}이다. "
            f"콜라(남자)는 제로(여자)보다 약간 크게 유지하고 두 캐릭터 모두 직립 이족보행을 유지한다. "
            f"핵심 소품은 {key_props}, 연속 유지 소품은 {carryover_props}. "
            "첨부된 패널 이미지를 기준 프레임으로 사용하고 캐릭터 외형, 손/소품 주체, 배경의 핵심 구조를 유지한 채 "
            "짧은 영상 한 장면으로 자연스럽게 움직임과 표정 변화만 추가한다. "
            "새로운 등장인물, 의상, 말풍선, 자막, UI, 과한 카메라 점프는 추가하지 않는다."
        )
        prompt_items.append(
            {
                "panel_no": panel_no,
                "image_file": panel_base["base_path"].name,
                "story_role": str(panel.get("story_role", "")).strip(),
                "location": str(panel.get("location", "")).strip(),
                "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
                "key_props": [str(item).strip() for item in panel.get("key_props", []) if str(item).strip()],
                "carryover_props": [str(item).strip() for item in panel.get("carryover_props", []) if str(item).strip()],
                "dialogue_beats": dialogue_beats,
                "video_prompt": video_prompt,
            }
        )
    return {
        "run_id": run_id,
        "topic": topic,
        "title": title,
        "thumbnail_subtitle": subtitle,
        "scope_summary": scope_summary,
        "panels": prompt_items,
    }


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value).strip())
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def _derive_background_text_plan(
    *,
    slide_type: str,
    location: str,
    scene_prompt: str,
    key_props: list[str] | None = None,
) -> dict[str, Any]:
    combined = " ".join(
        [
            str(slide_type or ""),
            str(location or ""),
            str(scene_prompt or ""),
            " ".join(str(item) for item in (key_props or [])),
        ]
    ).lower()
    exact_lines: list[str] = []

    if any(token in combined for token in ("passport control", "passkontrolle", "입국 심사", "여권 심사", "심사대")):
        exact_lines.append("PASSPORT CONTROL")
    if any(token in combined for token in ("arrivals", "arrival", "입국장", "도착 홀")):
        exact_lines.append("ARRIVALS")
    if any(token in combined for token in ("baggage", "수하물", "carousel", "belt", "claim")):
        exact_lines.append("BAGGAGE CLAIM")
    if any(token in combined for token in ("exit", "출구")):
        exact_lines.append("EXIT")
    if any(token in combined for token in ("customs", "세관")):
        exact_lines.append("CUSTOMS")
    if any(token in combined for token in ("duty free", "면세", "shop", "store", "마트")):
        exact_lines.append("DUTY FREE" if "duty free" in combined or "면세" in combined else "AISLE 3")
    if any(token in combined for token in ("gate", "flight", "board", "departure", "전광판", "탑승구")):
        exact_lines.extend(["INFO", "FLIGHTS"])
    if any(token in combined for token in ("passport", "reisepass", "여권")):
        exact_lines.append("PASSPORT")
    elif any(token in combined for token in ("document", "boarding pass", "ticket", "문서", "티켓", "탑승권")):
        exact_lines.append("DOCUMENT")
    if any(token in combined for token in ("stamp", "stempel", "도장")):
        exact_lines.extend(["STAMP", "2026-03-20"])

    exact_lines = _dedupe_preserve_order(exact_lines)[:2]
    if slide_type == "thumbnail" and not exact_lines:
        return {
            "mode": "minimal",
            "lines": [],
            "policy": "장면 이해에 필요한 표지판이 있더라도 1개의 짧은 실제 단어만 허용하고, 나머지는 아이콘/막대/빈 줄로 처리한다.",
        }
    if not exact_lines:
        return {
            "mode": "none",
            "lines": [],
            "policy": "읽을 수 있는 배경 텍스트를 만들지 말고, 필요 요소는 아이콘/색 막대/빈 줄로만 표현한다.",
        }
    return {
        "mode": "exact",
        "lines": exact_lines,
        "policy": "아래에 명시한 텍스트만 정확한 철자로 허용하고, 나머지 읽을 수 있는 텍스트는 만들지 않는다.",
    }


def _build_thumbnail_shot_plan(brief: dict[str, Any]) -> str:
    return (
        f"대표 장소: {brief.get('scope_summary', '').strip() or brief.get('thumbnail_scene_prompt', '').strip()} | "
        f"핵심 장면: {brief.get('thumbnail_scene_prompt', '').strip()}"
    ).strip(" |")


def _build_panel_shot_plan(panel: dict[str, Any], previous_panel: dict[str, Any] | None = None) -> str:
    details: list[str] = []
    location = str(panel.get("location", "")).strip()
    if location:
        details.append(f"장소={location}")
    story_role = str(panel.get("story_role", "")).strip()
    if story_role:
        details.append(f"서사역할={story_role}")
    scene_prompt = str(panel.get("scene_prompt", "")).strip()
    if scene_prompt:
        details.append(f"핵심행동={scene_prompt}")
    carryover_props = _panel_carryover_props(panel, previous_panel)
    if carryover_props:
        details.append(f"연속소품={', '.join(carryover_props)}")
    key_props = _panel_key_props(panel)
    if key_props:
        details.append(f"핵심소품={', '.join(key_props)}")
    return " | ".join(details)


def _build_episode_prop_registry(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for panel in panels:
        panel_no = int(panel.get("panel_no", 0) or 0)
        for prop in _merge_prop_lists(_panel_key_props(panel), _panel_carryover_props(panel)):
            key = prop.casefold()
            entry = registry.setdefault(
                key,
                {
                    "name": prop,
                    "first_panel_no": panel_no,
                    "panel_numbers": [],
                    "carryover_panel_numbers": [],
                },
            )
            if panel_no not in entry["panel_numbers"]:
                entry["panel_numbers"].append(panel_no)
            if prop in _panel_carryover_props(panel) and panel_no not in entry["carryover_panel_numbers"]:
                entry["carryover_panel_numbers"].append(panel_no)

    result = list(registry.values())
    result.sort(key=lambda item: (item["first_panel_no"], item["name"]))
    return result


def _build_panel_prop_plan(
    panel: dict[str, Any],
    *,
    previous_panel: dict[str, Any] | None = None,
    episode_prop_registry: list[dict[str, Any]] | None = None,
) -> list[str]:
    current_props = _panel_key_props(panel)
    carryover_props = _panel_carryover_props(panel, previous_panel)
    lines: list[str] = []
    if current_props:
        lines.append(f"이번 컷에 실제로 보여야 하는 핵심 소품: {', '.join(current_props)}")
        lines.append("핵심 소품은 종류뿐 아니라 주된 색상, 형태, 크기감, 열림/닫힘 상태, 보이는 면을 그대로 유지한다.")
    if carryover_props:
        lines.append(
            "연속 소품은 이전 컷과 같은 물건으로 유지: "
            + ", ".join(carryover_props)
            + " (종류, 주된 색상, 크기, 손에 든 방향, 열림/닫힘 상태를 유지)"
        )
    if previous_panel:
        previous_props = _panel_key_props(previous_panel)
        if previous_props:
            lines.append(f"직전 컷 기준 소품 기준선: {', '.join(previous_props)}")

    registry_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in (episode_prop_registry or [])
        if str(item.get("name", "")).strip()
    }
    for prop in current_props:
        registry_item = registry_by_name.get(prop.casefold())
        if not registry_item:
            continue
        recurring_panels = [int(no) for no in registry_item.get("panel_numbers", []) if int(no) > 0]
        if len(recurring_panels) <= 1:
            continue
        lines.append(
            f"{prop}: 패널 {', '.join(str(no) for no in recurring_panels)}에서 같은 물건으로 반복된다. "
            "중간에 다른 소품명이나 다른 형태로 바꾸지 않는다."
        )
    return lines


def _build_panel_pose_plan(panel: dict[str, Any]) -> list[str]:
    scene_prompt = str(panel.get("scene_prompt", "")).strip()
    lower_scene = scene_prompt.lower()
    lines = [
        "두 캐릭터 모두 골반과 상체를 세운 완전한 직립 이족보행 자세를 유지한다.",
        "앞발은 손처럼만 사용하고 바닥, 책상, 카운터, 캐리어, 레일 위에 체중을 싣지 않는다.",
        "배경 인물은 필요하면 인간형만 허용하고, 추가 고양이형 캐릭터는 절대 만들지 않는다.",
        "배경 인간은 항상 주인공보다 작고 흐리며 뒤쪽에 둔다. 인간 배경 인물이 콜라나 제로보다 크게 보이거나 전면을 차지하면 실패다.",
    ]
    if any(token in lower_scene for token in ("show", "open", "stamp", "passport", "document", "ticket", "hold", "point")):
        lines.append("핵심 소품을 보여주는 장면이면 두 캐릭터 모두 직립 자세를 유지한 채 손동작으로만 소품을 들거나 가리킨다.")
    if any(token in lower_scene for token in ("search", "looking for", "find", "frantic", "bag", "suitcase")):
        lines.append("무언가를 찾는 장면이어도 네 발로 엎드리거나 캐리어 위에 기대지 말고, 직립한 채 몸만 숙여 손으로 찾는다.")
    if any(token in lower_scene for token in ("counter", "desk", "booth", "screen", "kiosk", "gate")):
        lines.append("카운터/데스크/기기 앞 장면이어도 상판 위에 올라타지 말고 바닥에 선 채 손으로만 조작한다.")
    return lines


def _build_thumbnail_blueprint(
    brief: dict[str, Any],
    *,
    panels: list[dict[str, Any]],
    episode_prop_registry: list[dict[str, Any]],
) -> list[str]:
    panel_1 = panels[0] if panels else {}
    panel_1_location = str(panel_1.get("location", "")).strip()
    panel_1_scene = str(panel_1.get("scene_prompt", "")).strip()
    panel_1_props = _panel_key_props(panel_1)
    recurring_props = [
        str(item.get("name", "")).strip()
        for item in episode_prop_registry
        if len([int(no) for no in item.get("panel_numbers", []) if int(no) > 0]) > 1 and str(item.get("name", "")).strip()
    ]
    lines = [
        "썸네일은 패널 1의 확대판이 아니라 별도 teaser establishing shot으로 설계한다.",
        "카메라는 정면 아이레벨을 피하고, 넓은 하이앵글 또는 사선 establishing shot으로 잡아 본문 첫 컷과 즉시 구분되게 한다.",
        "썸네일 전용 구조적 배경 지문을 사용한다. 천장 형태, 바닥 패턴, 중앙 안내판, 기둥/출구 프레임, 카운터 배치는 본문 어느 컷과도 겹치지 않게 한다.",
        "썸네일에서는 두 캐릭터를 완전히 동일한 나란히 서기 포즈로 두지 말고, 시선 방향과 손동작을 비대칭으로 설계한다.",
        "썸네일에 recurring prop을 노출할 때는 본문과 같은 물건만 허용하고, 색상/형태/상태를 임의로 바꾸지 않는다.",
        "이야기 흐름상 꼭 필요한 후면/측후면이 아닌 이상 두 캐릭터의 얼굴과 눈이 읽히는 정면 또는 3/4 시점으로 설계한다.",
    ]
    if panel_1_location:
        lines.append(f"패널 1 주요 장소 '{panel_1_location}'의 동일 서브로케이션, 동일 바닥 패턴, 동일 카메라 거리는 썸네일에서 금지한다.")
    if panel_1_scene:
        lines.append(f"패널 1의 핵심행동 '{panel_1_scene}'를 그대로 반복하지 말고, 상위 개념의 대표 사건으로 재설계한다.")
    if panel_1_props:
        lines.append(
            "썸네일에 본문 핵심 소품이 등장한다면 패널 1 기준 같은 물건만 허용: "
            + ", ".join(panel_1_props)
            + ". 다른 색상/다른 종류의 대체 소품은 금지한다."
        )
    if recurring_props:
        lines.append(
            "에피소드 반복 소품 후보: "
            + ", ".join(recurring_props[:4])
            + ". 썸네일에 이들을 넣을 때는 본문과 같은 정체성으로만 사용한다."
        )
    return lines


def _build_character_identity_contract() -> list[str]:
    return [
        "콜라는 매우 짙은 검은 단색 털, 줄무늬 없음, 둥근 노란 눈, 둥글게 말리는 꼬리 끝을 유지한다. 남색/청회색/먹색처럼 다른 계열로 바뀌면 실패다.",
        "제로는 밝은 회색 바탕+진한 회색 태비 소용돌이 무늬, 이마 M자 무늬, 줄무늬 꼬리, 갈색 눈을 유지한다. 무늬가 희미해지거나 다른 패턴으로 바뀌면 실패다.",
        "두 캐릭터 모두 참조 이미지처럼 일반적인 집고양이 기반의 둥글고 자연스러운 체형을 유지한다. 과장된 근육질, 보디빌더형 상체, 사람처럼 갈라진 복근은 금지한다.",
        "참조 이미지는 캐릭터 외형의 불변 기준이다. 얼굴형, 귀 모양, 눈 비율, 꼬리 실루엣, 털색 계열을 임의로 재해석하거나 다른 화풍처럼 바꾸면 실패다.",
        "콜라가 더 크게 보여야 한다는 규칙은 몸집 비율 차이만 의미한다. 근육량이나 벌크를 키워 위압적인 근육질 체형으로 만드는 것은 금지한다.",
        "콜라의 검은 단색 털에 제로의 회색 줄무늬가 섞이면 실패다.",
        "제로의 눈 색이 갈색이 아닌 노란색/호박색으로 바뀌면 실패다.",
        "두 캐릭터의 눈색, 털무늬, 꼬리 패턴, 얼굴형을 서로 섞거나 교차 복사하지 않는다.",
        "참조 이미지보다 얼굴형이 갑자기 길어지거나 각지게 변하면 실패다. 두 캐릭터 모두 둥글고 부드러운 참조 얼굴형을 유지한다.",
        "각 캐릭터는 해부학적으로 자연스러운 사지 수를 유지한다. 손처럼 쓰는 앞발은 좌우 한 쌍만 보여야 하며, 여분의 손/팔/앞발 또는 사라진 팔은 실패다.",
    ]


def _build_scale_contract(*, slide_type: str) -> list[str]:
    lines = [
        "콜라는 언제나 제로보다 약간 더 크게 읽혀야 하며 목표 격차는 약 12%, 허용 범위는 8~15%다.",
        "비교 기준은 머리 꼭대기 높이, 어깨선 높이, 몸통 폭, 전신 실루엣이다. 네 기준 모두에서 콜라가 약간 더 크게 읽혀야 한다.",
        "원근 때문에 제로가 더 크게 읽히지 않게 두 캐릭터를 비슷한 거리 평면에 두고, 한쪽만 과한 전경 배치를 하지 않는다.",
        "콜라를 과하게 키워 성인과 아기처럼 보이게 만들지 않는다. 18%를 넘는 차이는 실패다.",
        "제로가 콜라와 같거나 더 크게 읽히는 구도도 실패다.",
    ]
    if slide_type == "thumbnail":
        lines.append("썸네일에서도 제로를 뒤로 숨기거나 사족보행으로 낮춰서 비율을 맞추지 말고, 둘 다 직립한 상태에서 자연스럽게 12% 차이를 유지한다.")
    else:
        lines.append("패널에서는 카운터/문서/가방 상호작용 때문에 한쪽이 과도하게 숙여져 비율이 붕괴되지 않게, 두 캐릭터의 골반 높이와 어깨선이 비슷한 레벨을 유지하도록 설계한다.")
    return lines


def _build_character_accuracy_contract(*, slide_type: str, scene_prompt: str = "") -> list[str]:
    lower_scene = scene_prompt.lower()
    lines = [
        "등장인물 정확성이 배경 디테일보다 항상 우선이다. 충돌이 생기면 배경과 소품 디테일을 단순화하고 콜라/제로의 외형, 포즈, 손동작을 먼저 맞춘다.",
        "콜라는 참조 이미지의 검은 단색 털, 노란 눈, 둥근 얼굴형, 말린 꼬리 끝을 그대로 유지한다. 제로는 밝은 회색+진한 소용돌이 무늬, 갈색 눈, 둥근 얼굴형을 그대로 유지한다.",
        "두 캐릭터 모두 정확히 한 개의 왼팔과 한 개의 오른팔만 가진 자연스러운 의인화 이족보행으로 묘사한다. 여분의 팔/손/앞발, 누락된 팔, 사족보행, 앞발 체중 지지는 금지한다.",
        "콜라는 모든 컷에서 제로보다 약 12% 크게 유지한다. 머리, 어깨선, 몸통 폭, 전신 실루엣 모두에서 콜라가 약간 더 크게 읽혀야 하며, 과도한 거대화도 금지한다.",
        "손/소품 주체는 scene prompt 그대로 고정한다. 누가 들고, 누가 가리키고, 누가 끌고, 어느 손을 쓰는지 바꾸지 않는다.",
    ]
    if any(token in lower_scene for token in ("two passports", "two documents", "two tickets", "two blue passports", "two blue documents")):
        lines.append("scene prompt가 2개의 동일 소품을 요구하면 두 개가 분리된 개별 물건으로 명확히 보여야 한다. 한 개만 보이거나 한 덩어리 책자처럼 합쳐지면 실패다.")
    if any(token in lower_scene for token in ("left paw", "right paw", "both paws", "one paw", "tucked under", "upside down")):
        lines.append("손 방향, 양손/한손 여부, 겨드랑이에 끼움, 뒤집힌 방향 같은 세부 액션은 눈에 띄게 구분되도록 정확히 묘사한다.")
    if slide_type == "thumbnail":
        lines.append("썸네일도 예외 없이 같은 캐릭터 계약을 따른다. 썸네일에서 틀린 외형/비율/이족보행은 본문 전체 실패로 간주한다.")
    return lines


def _build_thumbnail_pose_plan(brief: dict[str, Any]) -> list[str]:
    lower_scene = str(brief.get("thumbnail_scene_prompt", "")).strip().lower()
    lines = [
        "두 캐릭터 모두 골반과 척추를 세운 완전한 직립 이족보행으로 선다.",
        "앞발은 손처럼만 사용하고 바닥이나 소품에 체중을 싣지 않는다.",
        "썸네일은 establishing shot이어도 제로를 사족보행 자세나 웅크린 동물형 실루엣으로 두지 않는다.",
        "이야기 흐름상 꼭 필요한 이유가 없으면 두 캐릭터를 뒷모습이나 완전한 측후면으로만 그리지 말고, 얼굴과 눈이 읽히는 정면 또는 3/4 시점으로 그린다.",
    ]
    if any(token in lower_scene for token in ("board", "sign", "screen", "check", "look")):
        lines.append("표지판/전광판을 보는 장면이면 두 캐릭터 모두 직립 상태에서 시선과 손짓으로만 안내물을 가리킨다.")
    return lines


def _build_panel_action_contract(panel: dict[str, Any], previous_panel: dict[str, Any] | None = None) -> list[str]:
    scene_prompt = str(panel.get("scene_prompt", "")).strip()
    lower_scene = scene_prompt.lower()
    lines = [
        "장면 설명에 지정된 행동 주체를 바꾸지 않는다. 콜라가 해야 할 행동을 제로에게 넘기거나, 제로가 들고 있어야 할 소품을 콜라가 들면 실패다.",
        "장면 설명에 left/right paw가 있으면 그 손 방향을 정확히 지킨다.",
        "손처럼 쓰는 앞발은 좌우 한 쌍만 자연스럽게 보이게 유지한다. 여분의 손/팔이 생기거나, 들어야 할 손이 사라져 팔이 없는 것처럼 보이면 실패다.",
        "소품을 쥔 손, 가리키는 손, 카운터에 올린 손은 동일 동작 안에서 서로 충돌하면 안 된다. 한 캐릭터가 동시에 같은 소품을 두 손으로 중복 소유하면 실패다.",
        "이야기 흐름상 꼭 필요한 후면/측후면이 아닌 이상 얼굴과 표정이 읽히는 정면 또는 3/4 시점을 우선한다.",
    ]
    if any(token in lower_scene for token in ("hold", "holding", "carry", "present", "show", "hand", "passport", "document", "folder", "ticket")):
        lines.append("누가 어떤 소품을 들고 제시하는지 scene prompt의 주체를 그대로 지킨다. 연속 소품은 직전 컷과 같은 캐릭터가 같은 손 또는 자연스러운 동일 측 손으로 유지한다.")
    if any(token in lower_scene for token in ("two passports", "two documents", "two tickets", "two blue passports", "two blue documents")):
        lines.append("scene prompt가 소품 2개를 요구하면 두 개를 분리된 개별 물건으로 명확히 그린다. 두 권을 하나의 두꺼운 책자처럼 합치거나 한 개만 보이게 만들면 실패다.")
    if any(token in lower_scene for token in ("tucked under", "under his arm", "under her arm", "under the arm", "under one arm")):
        lines.append("겨드랑이에 끼우는 소품은 손으로 쥐지 말고 실제로 팔 아래에 끼운 상태로 보여야 한다.")
    if "upside down" in lower_scene:
        lines.append("scene prompt가 upside down을 요구하면 소품 방향이 눈에 띄게 뒤집혀 있어야 한다. 정방향으로 보이면 실패다.")
    if any(token in lower_scene for token in ("facepalm", "covers his face with one paw", "covers her face with one paw", "one paw over face")):
        lines.append("한 손 facepalm 장면이면 정확히 한 앞발만 얼굴에 닿아야 한다. 양손으로 얼굴을 가리면 실패다.")
    if any(token in lower_scene for token in ("point", "pointing", "gesture", "indicate")):
        lines.append("가리키는 장면이면 지정된 캐릭터가 지정된 손으로만 가리키고, 붙잡기나 양손 감싸기 같은 다른 동작으로 바꾸지 않는다.")
    if any(token in lower_scene for token in ("back view", "rear view", "from behind", "side-back", "over shoulder")):
        lines.append("후면/측후면 구도는 scene prompt에 명시된 경우에만 허용하고, 그렇지 않으면 금지한다.")
    elif not any(token in lower_scene for token in ("front view", "three-quarter", "3/4 view", "facing camera", "face visible")):
        lines.append("scene prompt에 후면 구도 지시가 없다면 얼굴과 눈이 읽히는 front-facing 또는 three-quarter view로 유지한다.")
    if previous_panel and _panel_carryover_props(panel, previous_panel):
        lines.append("직전 컷에서 이어진 핵심 소품은 이유 없이 캐릭터 사이에서 주인이 바뀌지 않는다. 전달 장면이 아니면 같은 캐릭터가 계속 들고 있어야 한다.")
    return lines


def _validate_prompt_plan_package(
    prompt_plan: dict[str, Any],
    *,
    panels: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[str] = []
    thumbnail_plan = prompt_plan.get("thumbnail", {})
    if not str(thumbnail_plan.get("final_prompt", "")).strip():
        issues.append("썸네일 최종 프롬프트가 비어 있습니다.")
    if not [str(line).strip() for line in thumbnail_plan.get("thumbnail_blueprint_lines", []) if str(line).strip()]:
        issues.append("썸네일 블루프린트 계획이 비어 있습니다.")
    if not [str(line).strip() for line in thumbnail_plan.get("pose_plan_lines", []) if str(line).strip()]:
        issues.append("썸네일 포즈 계획이 비어 있습니다.")
    if not [str(line).strip() for line in thumbnail_plan.get("character_accuracy_lines", []) if str(line).strip()]:
        issues.append("썸네일 등장인물 정확성 계약이 비어 있습니다.")
    if not [str(line).strip() for line in prompt_plan.get("character_identity_contract", []) if str(line).strip()]:
        issues.append("캐릭터 정체성 계약이 비어 있습니다.")
    if not [str(line).strip() for line in prompt_plan.get("scale_contract", []) if str(line).strip()]:
        issues.append("상대 크기 계약이 비어 있습니다.")

    panel_plans = prompt_plan.get("panels", [])
    if len(panel_plans) != len(panels):
        issues.append(f"패널 프롬프트 계획 수가 일치하지 않습니다. expected={len(panels)} actual={len(panel_plans)}")

    plan_map = {
        int(plan.get("panel_no", 0) or 0): plan
        for plan in panel_plans
        if int(plan.get("panel_no", 0) or 0) > 0
    }
    for panel in panels:
        panel_no = int(panel.get("panel_no", 0) or 0)
        plan = plan_map.get(panel_no, {})
        if not str(plan.get("final_prompt", "")).strip():
            issues.append(f"패널 {panel_no} 최종 프롬프트가 비어 있습니다.")
        plan_lines = [str(line).strip() for line in plan.get("dialogue_lines", []) if str(line).strip()]
        panel_lines = [str(line).strip() for line in panel.get("dialogue_lines", []) if str(line).strip()]
        if plan_lines != panel_lines:
            issues.append(f"패널 {panel_no} 대사 계획이 원본 패널 대사와 다릅니다.")
        background_lines = [str(line).strip() for line in plan.get("background_text_lines", []) if str(line).strip()]
        if len(background_lines) > 3:
            issues.append(f"패널 {panel_no} 배경 텍스트 계획이 3개를 초과합니다.")
        expected_carryover = _panel_carryover_props(panel, panels[panel_no - 2] if panel_no > 1 else None)
        planned_carryover = [str(line).strip() for line in plan.get("carryover_props", []) if str(line).strip()]
        if planned_carryover != expected_carryover:
            issues.append(f"패널 {panel_no} 연속 소품 계획이 정규화 결과와 다릅니다.")
        if not [str(line).strip() for line in plan.get("pose_plan_lines", []) if str(line).strip()]:
            issues.append(f"패널 {panel_no} 포즈 계획이 비어 있습니다.")
        if not [str(line).strip() for line in plan.get("character_accuracy_lines", []) if str(line).strip()]:
            issues.append(f"패널 {panel_no} 등장인물 정확성 계약이 비어 있습니다.")
        if not [str(line).strip() for line in plan.get("action_contract_lines", []) if str(line).strip()]:
            issues.append(f"패널 {panel_no} 액션 계약이 비어 있습니다.")
        expected_prop_plan = _build_panel_prop_plan(
            panel,
            previous_panel=panels[panel_no - 2] if panel_no > 1 else None,
            episode_prop_registry=prompt_plan.get("episode_prop_registry", []),
        )
        if expected_prop_plan and not [str(line).strip() for line in plan.get("prop_plan_lines", []) if str(line).strip()]:
            issues.append(f"패널 {panel_no} 소품 계획이 비어 있습니다.")
        if panel_no > 1 and expected_carryover:
            previous_props = _panel_key_props(panels[panel_no - 2])
            missing_from_previous = [prop for prop in expected_carryover if prop.casefold() not in {item.casefold() for item in previous_props}]
            if missing_from_previous:
                issues.append(
                    f"패널 {panel_no} 연속 소품이 직전 컷 핵심 소품과 이어지지 않습니다: {', '.join(missing_from_previous)}"
                )

    return {
        "has_issues": bool(issues),
        "issues": issues,
    }


def _derive_regeneration_targets(hard_blockers: list[str], *, panel_count: int) -> dict[str, Any]:
    thumbnail = any("썸네일" in str(blocker) for blocker in hard_blockers)
    explicit_panels: set[int] = set()
    expand_from: int | None = None
    global_keywords = (
        "장면과 프롬프트의 심각한 불일치",
        "배경 재사용",
        "배경을 그대로",
        "여정",
        "에피소드 범위",
        "결말",
    )

    for blocker in hard_blockers:
        text = str(blocker)
        panel_numbers = sorted({int(match) for match in re.findall(r"패널\s*(\d+)", text)})
        explicit_panels.update(number for number in panel_numbers if 1 <= number <= panel_count)
        if panel_numbers and (len(panel_numbers) > 1 or any(keyword in text for keyword in global_keywords)):
            start = min(panel_numbers)
            expand_from = start if expand_from is None else min(expand_from, start)

    if expand_from is not None:
        panel_numbers = list(range(expand_from, panel_count + 1))
    elif explicit_panels:
        panel_numbers = sorted(explicit_panels)
    elif hard_blockers:
        panel_numbers = list(range(1, panel_count + 1))
    else:
        panel_numbers = []

    return {"thumbnail": thumbnail, "panel_numbers": panel_numbers}


def _merge_replanned_brief(
    original_brief: dict[str, Any],
    replanned_brief: dict[str, Any],
    *,
    targets: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(original_brief)
    if targets.get("thumbnail"):
        for key in (
            "title",
            "thumbnail_subtitle",
            "episode_scope",
            "subtitle_scope",
            "scope_summary",
            "image_prompt",
            "thumbnail_scene_prompt",
            "caption",
            "hashtags",
            "character_notes",
            "story_review",
            "model",
        ):
            if key in replanned_brief:
                merged[key] = copy.deepcopy(replanned_brief[key])
    else:
        for key in ("story_review", "model"):
            if key in replanned_brief:
                merged[key] = copy.deepcopy(replanned_brief[key])

    target_panel_numbers = set(int(number) for number in targets.get("panel_numbers", []))
    replanned_panels = {
        int(panel.get("panel_no", index)): panel
        for index, panel in enumerate(replanned_brief.get("panels", []), start=1)
    }
    if not replanned_panels:
        return merged

    merged["panels"] = [
        copy.deepcopy(replanned_panels.get(int(panel.get("panel_no", index)), panel))
        if int(panel.get("panel_no", index)) in target_panel_numbers
        else copy.deepcopy(panel)
        for index, panel in enumerate(original_brief.get("panels", []), start=1)
    ]
    return merged


def execute_outbound_bundle(settings: WebtoonSettings, bundle: OutboundBundle) -> dict[str, Any]:
    google_client = GoogleWorkspaceClient(settings)
    drive_folder_chain = google_client.ensure_drive_path(*bundle.drive_path_parts)
    drive_folder = drive_folder_chain[-1]

    uploaded_files: list[dict[str, Any]] = []
    artifact_by_bundle_name = {artifact.bundle_name: artifact for artifact in bundle.upload_artifacts}
    for artifact in bundle.upload_artifacts:
        uploaded_file = google_client.upload_bytes(
            drive_folder["id"],
            artifact.bundle_name,
            artifact.source_path.read_bytes(),
            artifact.mime_type,
            make_public=artifact.make_public,
        )
        uploaded_files.append(
            {
                "artifact_role": artifact.artifact_role,
                "slide_index": artifact.slide_index,
                "slide_type": artifact.slide_type,
                "panel_no": artifact.panel_no,
                "uploaded_file": uploaded_file,
            }
        )

    uploaded_files_by_role = _build_uploaded_files_by_role(uploaded_files)

    metadata_payload = copy.deepcopy(bundle.metadata_payload)
    metadata_payload["drive_folder"] = drive_folder
    metadata_payload["uploaded_files"] = uploaded_files_by_role
    metadata_payload["approved_image_version"] = bundle.approved_image_version
    metadata_payload["status"] = bundle.status
    metadata_payload["approved_by"] = bundle.approved_by
    metadata_payload["approved_at"] = bundle.approved_at
    for filename, payload in (("run_metadata.json", metadata_payload), *bundle.json_payloads):
        google_client.upload_json(drive_folder["id"], filename, payload)

    return {
        "run_id": bundle.run_id,
        "week_key": bundle.week_key,
        "status": bundle.status,
        "drive_folder": drive_folder,
        "uploaded_files": uploaded_files_by_role,
        "character_reference_files": metadata_payload.get("character_reference_files", []),
        "correction_attempts": metadata_payload.get("correction_attempts", []),
        "quality_report": bundle.quality_report,
        "slides": list(bundle.slides),
    }


def build_run_identity(now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    iso = current.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    run_id = f"run-{uuid4().hex[:8]}"
    return week_key, run_id


def create_artifact_paths(run_id: str, week_key: str) -> PipelineArtifacts:
    run_dir = Path(tempfile.mkdtemp(prefix=f"webtoon-{run_id}-"))
    return PipelineArtifacts(
        run_dir=run_dir,
        run_id=run_id,
        week_key=week_key,
        metadata_path=run_dir / "run_metadata.json",
        publish_result_path=run_dir / "publish_result_v1.json",
    )


def build_versioned_path(run_dir: Path, prefix: str, version: int, suffix: str) -> Path:
    return run_dir / f"{prefix}_v{version}.{suffix}"


def list_character_reference_files(settings: WebtoonSettings) -> list[Path]:
    root = settings.character_assets_dir
    if not root.exists():
        return []

    found: list[Path] = []
    seen: set[Path] = set()
    for filename in PREFERRED_CHARACTER_REFERENCE_FILES:
        candidates = sorted(root.rglob(filename))
        if candidates:
            candidate = candidates[0]
            found.append(candidate)
            seen.add(candidate)

    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for candidate in sorted(root.rglob(pattern)):
            if candidate not in seen:
                found.append(candidate)
                seen.add(candidate)
    return found


WEBTOON_STYLE_PREFIX = (
    "Korean digital webtoon style, bold clean outlines, vibrant colors, "
    "expressive exaggerated facial expressions, dynamic character poses, "
    "anthropomorphic cats walking upright on two legs using front paws as hands, "
    "strictly bipedal posture with upright pelvis and upright torso, "
    "forepaws used like hands only and never used as weight-bearing front legs, "
    "never standing or walking on all fours, "
    "manga-style emotion effects (sweat drops, exclamation marks, sparkles, anger marks), "
    "detailed realistic background, high quality digital illustration, "
    "the characters do NOT wear any clothing shoes or accessories they have natural fur only, "
    "absolutely NO speech bubbles NO dialogue NO captions or editorial overlay UI in the image, "
    "but scene-required in-world signage labels screens and machine interfaces are allowed"
)

CHARACTER_REFERENCE_LOCK_BLOCK = (
    "- 함께 제공된 참조 이미지는 단순 참고가 아니라 고정된 마스터 레퍼런스 모델 시트다. 그림체가 아니라 캐릭터 정체성을 그대로 복사해야 한다.\n"
    "- 콜라 고정 특징: 매우 짙은 검은 털, 둥글고 부드러운 얼굴형, 큰 삼각 귀, 갈색빛 안쪽 귀, 둥근 노란(황금색) 눈, 줄무늬나 반점 없음, 매끈한 단색 몸통, 둥글게 말리는 꼬리 끝.\n"
    "- [CRITICAL] 제로 눈 색상: 반드시 갈색(brown)이어야 한다. 노란색, 황색, 호박색, 초록색은 절대 금지. 제로의 눈은 어떤 컷에서도 갈색이어야 한다.\n"
    "- 제로 고정 특징: 밝은 회색 바탕, 진한 회색 클래식 태비 소용돌이 무늬, 이마의 선명한 M자 무늬, 뺨 줄무늬, 옆구리의 큰 소용돌이 무늬, 줄무늬 꼬리, 갈색(brown) 눈, 분홍 안쪽 귀, 밝은 주둥이와 배.\n"
    "- 특히 제로의 옆구리 소용돌이 무늬와 이마 M자 무늬를 절대 단순화하거나 직선 줄무늬 몇 개로 바꾸지 않는다.\n"
    "- 캐릭터 얼굴형과 체형은 참조 이미지보다 더 날렵하거나 각지게 바꾸지 않는다. 참조처럼 둥글고 부드러운 인상을 유지한다.\n"
    "- 옷, 반다나, 목걸이, 모자, 신발, 액세서리를 절대 추가하지 않는다.\n"
)


def _build_compact_global_contract(
    brief: dict[str, Any],
) -> str:
    """Build a short (~400 char) global contract header for all slides."""
    outfit_plan = brief.get("outfit_plan", {})
    kolla_outfit = outfit_plan.get("kolla", {})
    zero_outfit = outfit_plan.get("zero", {})
    kolla_top = kolla_outfit.get("top", "")
    kolla_bottom = kolla_outfit.get("bottom", "")
    zero_top = zero_outfit.get("top", "")
    zero_bottom = zero_outfit.get("bottom", "")
    kolla_outfit_str = ", ".join(part for part in (kolla_top, kolla_bottom) if part)
    zero_outfit_str = ", ".join(part for part in (zero_top, zero_bottom) if part)
    lines = [
        "=== OUTFIT (draw EXACTLY this in EVERY image — thumbnail and all 6 panels identical) ===",
        f"KOLLA wears: {kolla_outfit_str}." if kolla_outfit_str else "",
        f"ZERO wears: {zero_outfit_str}." if zero_outfit_str else "",
        "NO variation between panels. Same colors, same materials, same details every time.",
        "Reference images show bare fur — IGNORE bare fur, ALWAYS draw the outfit above.",
        "",
        "=== SIZE RATIO (enforce in EVERY image) ===",
        "Kolla is ALWAYS 10-15% taller and broader than Zero. Keep this ratio consistent across all panels.",
        "",
        "=== CHARACTERS ===",
        "Korean digital webtoon style, bold outlines, vibrant colors, expressive faces.",
        "Two anthropomorphic cats, strictly bipedal, front paws used as hands only.",
        "KOLLA (always LEFT, male): solid black fur, round face, large triangle ears, golden-yellow eyes.",
        "ZERO (always RIGHT, female): light gray + dark tabby swirls, M-mark forehead, BROWN eyes (never yellow/amber), pink inner ears.",
        "",
        "RULES: No speech bubbles/dialogue/captions. No readable background text (use color bars/icons). Only Kolla and Zero. Bipedal only.",
    ]
    lines = [line for line in lines if line or line == ""]
    return "\n".join(lines)


def build_costume_sheet_prompt(
    brief: dict[str, Any],
) -> str:
    """Build a prompt to generate a character costume reference sheet."""
    outfit_plan = brief.get("outfit_plan", {})
    kolla = outfit_plan.get("kolla", {})
    zero = outfit_plan.get("zero", {})
    kolla_top = kolla.get("top", "casual top")
    kolla_bottom = kolla.get("bottom", "casual pants")
    zero_top = zero.get("top", "casual top")
    zero_bottom = zero.get("bottom", "casual pants")
    return (
        "CHARACTER COSTUME REFERENCE SHEET — plain white background, no scenery.\n"
        "Draw exactly two anthropomorphic cats standing side by side, facing the viewer, full body from head to toe.\n"
        "\n"
        "LEFT — KOLLA (male): solid black fur, round face, large triangle ears, golden-yellow eyes.\n"
        f"  Outfit: {kolla_top} on top, {kolla_bottom} on bottom.\n"
        "  Kolla is 10-15% TALLER and BROADER than Zero.\n"
        "\n"
        "RIGHT — ZERO (female): light gray fur with dark tabby swirl stripes, M-mark on forehead, BROWN eyes (never yellow), pink inner ears.\n"
        f"  Outfit: {zero_top} on top, {zero_bottom} on bottom.\n"
        "  Zero is SMALLER than Kolla.\n"
        "\n"
        "Both cats stand upright on two legs (bipedal), front paws used as hands.\n"
        "Korean digital webtoon style, bold outlines, vibrant colors.\n"
        "No speech bubbles, no text, no background objects. Pure white background only.\n"
        "This sheet will be used as the SOLE visual reference for all episode panels."
    )


def _build_outfit_reminder(brief: dict[str, Any]) -> str:
    """Build a short outfit reminder line to append at the end of each image prompt."""
    outfit_plan = brief.get("outfit_plan", {})
    kolla = outfit_plan.get("kolla", {})
    zero = outfit_plan.get("zero", {})
    kolla_str = ", ".join(part for part in (kolla.get("top", ""), kolla.get("bottom", "")) if part)
    zero_str = ", ".join(part for part in (zero.get("top", ""), zero.get("bottom", "")) if part)
    if not kolla_str and not zero_str:
        return ""
    parts = []
    if kolla_str:
        parts.append(f"Kolla: {kolla_str}")
    if zero_str:
        parts.append(f"Zero: {zero_str}")
    return "CRITICAL OUTFIT CHECK: " + " | ".join(parts) + ". Identical to all other panels. Any deviation is an error."


def build_compact_thumbnail_prompt(
    brief: dict[str, Any],
    *,
    global_contract: str,
) -> str:
    """Build a compact (~1200 char) thumbnail generation prompt."""
    direction = brief.get("thumbnail_direction", {})
    shot = direction.get("shot", "wide establishing shot")
    scene = direction.get("scene", "")
    background = direction.get("background", "")
    composition = direction.get("composition", "Characters in lower 60%. Upper 40% clean for title.")
    outfit_reminder = _build_outfit_reminder(brief)
    parts = [
        global_contract,
        "",
        "THUMBNAIL (cover image for this episode).",
        f"Shot: {shot}" if shot else "",
        f"Scene: {scene}" if scene else "",
        f"Background: {background}" if background else "",
        f"Composition: {composition}" if composition else "",
        "Upper 40% must be simple background (sky/wall) for title text overlay.",
        "",
        outfit_reminder,
    ]
    return "\n".join(part for part in parts if part)


def build_compact_panel_prompt(
    brief: dict[str, Any],
    panel: dict[str, Any],
    *,
    global_contract: str,
    panel_count: int = 6,
) -> str:
    """Build a compact (~1200 char) panel generation prompt."""
    panel_no = int(panel.get("panel_no", 1))
    direction = panel.get("direction", {})
    shot = direction.get("shot", "medium shot")
    scene = direction.get("scene", "")
    background = direction.get("background", "")
    composition = direction.get("composition", "")
    location = str(panel.get("location", "")).strip()
    story_role = str(panel.get("story_role", "")).strip()
    key_props = panel.get("key_props", [])
    prop_line = f"Props: {', '.join(str(p) for p in key_props)}" if key_props else ""
    outfit_reminder = _build_outfit_reminder(brief)
    parts = [
        global_contract,
        "",
        f"PANEL {panel_no}/{panel_count}. {location}.",
        f"Story role: {story_role}" if story_role else "",
        f"Shot: {shot}" if shot else "",
        f"Scene: {scene}" if scene else "",
        f"Background: {background}" if background else "",
        prop_line,
        f"Composition: {composition}" if composition else "",
        "",
        outfit_reminder,
    ]
    return "\n".join(part for part in parts if part)


def _extract_portable_props(*texts: str) -> list[str]:
    combined = " ".join(str(text or "") for text in texts).lower()
    found: list[str] = []
    for canonical, aliases in PORTABLE_PROP_ALIASES:
        if any(alias in combined for alias in aliases):
            found.append(canonical)
    return found


def _normalize_prop_list(raw_props: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(raw_props, (list, tuple, set)):
        for item in raw_props:
            candidates.extend(_normalize_prop_list(item))
        return candidates
    if raw_props is None:
        return []
    text = str(raw_props).strip()
    if not text:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,/\n|]+", text):
        cleaned = re.sub(r"\s+", " ", str(part).strip())
        cleaned = re.sub(r"^[0-9]+\.\s*", "", cleaned).strip(" -*•")
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        normalized.append(cleaned)
        seen.add(lowered)
    return normalized


def _merge_prop_lists(*prop_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in prop_groups:
        for item in group:
            text = re.sub(r"\s+", " ", str(item).strip()).strip(" -*•")
            if not text:
                continue
            lowered = text.casefold()
            if lowered in seen:
                continue
            merged.append(text)
            seen.add(lowered)
    return merged


def _panel_key_props(panel: dict[str, Any] | None) -> list[str]:
    if not panel:
        return []
    structured = _merge_prop_lists(_normalize_prop_list(panel.get("key_props", [])))
    if structured:
        return structured
    return _extract_portable_props(
        str(panel.get("scene_prompt", "")),
        str(panel.get("location", "")),
        *[str(line) for line in panel.get("dialogue_lines", [])],
    )


def _panel_carryover_props(panel: dict[str, Any], previous_panel: dict[str, Any] | None = None) -> list[str]:
    current_props = _panel_key_props(panel)
    current_lookup = {prop.casefold(): prop for prop in current_props}

    structured_carryover = _normalize_prop_list(panel.get("carryover_props", []))
    if structured_carryover:
        resolved: list[str] = []
        for item in structured_carryover:
            resolved.append(current_lookup.get(item.casefold(), item))
        return _merge_prop_lists(resolved)

    previous_props = _panel_key_props(previous_panel)
    shared_props = [prop for prop in previous_props if prop.casefold() in current_lookup]
    previous_location = str((previous_panel or {}).get("location", "")).strip()
    current_location = str(panel.get("location", "")).strip()
    if shared_props:
        return shared_props
    if previous_props and previous_location and current_location and previous_location == current_location:
        return previous_props
    return []


def _build_prop_continuity_block(
    panel: dict[str, Any],
    previous_panel: dict[str, Any] | None = None,
) -> str:
    current_props = _panel_key_props(panel)
    previous_props = _panel_key_props(previous_panel)
    carryover_props = _panel_carryover_props(panel, previous_panel)

    lines = [
        "- 직접 들고 이동 중인 여권, 티켓, 캐리어, 가방, 지도, 휴대폰, 문서철 같은 연속 소품은 이유 없이 다른 종류로 바꾸지 않는다.",
        "- 연속 소품은 종류뿐 아니라 주된 색상, 크기, 손에 든 쪽, 휴대 방식까지 유지한다.",
        "- 이전 컷과 직접 이어지는 흐름이면, scene_prompt에 명시된 핵심 소품이 사라지거나 다른 소품으로 치환되면 실패다.",
        "- 이번 컷에서 새 핵심 소품을 도입하려면 장면 설명에 직접 명시된 것만 허용한다.",
    ]
    if previous_props:
        lines.insert(0, f"- 이전 컷 핵심 소품: {', '.join(previous_props)}.")
    if current_props:
        lines.insert(1 if previous_props else 0, f"- 이번 컷 핵심 소품: {', '.join(current_props)}.")
    if carryover_props:
        lines.insert(
            2 if previous_props and current_props else 1 if (previous_props or current_props) else 0,
            f"- 이번 컷에서 연속 유지해야 할 소품: {', '.join(carryover_props)}.",
        )
    return "\n".join(lines) + "\n"


def _build_panel_scene_context(panel: dict[str, Any], previous_panel: dict[str, Any] | None = None) -> str:
    current_props = _panel_key_props(panel)
    previous_props = _panel_key_props(previous_panel)
    carryover_props = _panel_carryover_props(panel, previous_panel)
    parts = [
        f"현재 컷 장소: {str(panel.get('location', '')).strip()}",
        f"현재 컷 장면 설명: {str(panel.get('scene_prompt', '')).strip()}",
    ]
    if current_props:
        parts.append(f"현재 컷 key_props: {', '.join(current_props)}")
    if carryover_props:
        parts.append(f"현재 컷 carryover_props: {', '.join(carryover_props)}")
    previous_location = str((previous_panel or {}).get("location", "")).strip()
    previous_scene_prompt = str((previous_panel or {}).get("scene_prompt", "")).strip()
    if previous_location:
        parts.append(f"이전 컷 장소: {previous_location}")
    if previous_scene_prompt:
        parts.append(f"이전 컷 장면 설명: {previous_scene_prompt}")
    if previous_props:
        parts.append(f"이전 컷 key_props: {', '.join(previous_props)}")
    parts.append(_build_prop_continuity_block(panel, previous_panel).strip())
    return "\n".join(part for part in parts if part).strip()


def _should_reference_previous_panel(panel: dict[str, Any], previous_panel: dict[str, Any] | None = None) -> bool:
    _ = previous_panel
    return bool(panel.get("use_previous_panel_image_reference"))

BACKGROUND_TEXT_SIMPLIFICATION_BLOCK = (
    "- [CRITICAL] 배경/소품에 텍스트를 넣을 때는 정확히 쓸 수 있는 영어 단어 1개만 허용한다. 두 단어 이상, 외국어 문장, 긴 라벨, 기기 화면 본문은 모두 아이콘/막대/빈 면으로 대체한다.\n"
    "- [CRITICAL] 독일어, 한국어, 아랍어, 기타 외국어 단어를 배경에 직접 쓰지 않는다. 철자 오류가 생기기 때문이다. 해당 언어 표지판은 색 막대와 아이콘으로만 표현한다.\n"
    "- [CRITICAL] 스마트폰/태블릿 화면, 전광판, 항공편 안내판의 본문 텍스트는 절대 쓰지 않는다. 큰 아이콘 1개와 색 막대만 허용한다.\n"
    "- [CRITICAL] 의미 없는 알파벳 덩어리, 모자이크처럼 깨진 텍스트, 읽을 수 없는 미세 글자는 절대 만들지 않는다. 빈 면이나 단순 선으로 처리한다.\n"
    "- 간판, 안내판, 메뉴판, 라벨에 꼭 필요한 경우 영어 단일 단어 라벨 1개만 쓴다. 예: 'EXIT', 'INFO', 'TICKET', 'GATE'.\n"
    "- 여권, 비자, 문서철, 입국 도장은 'PASSPORT' 또는 'VISA' 라벨 1개만 쓰고 나머지는 빈 줄이나 단순 선으로 둔다.\n"
    "- 도장/스탬프/원형 인장에는 간단한 날짜 한 줄만 허용한다. 둘레 미세문구는 금지한다.\n"
    "- 버튼, 키오스크, 기기 UI는 아이콘과 짧은 영어 라벨만 허용한다.\n"
)


def build_thumbnail_generation_prompt(
    base_prompt: str,
    *,
    title: str,
    subtitle: str,
    topic: str,
    caption: str,
    episode_scope: str,
    subtitle_scope: str,
    scope_summary: str,
    character_notes: str,
    thumbnail_scene_prompt: str = "",
    shot_plan: str = "",
    background_text_mode: str = "",
    background_text_lines: list[str] | None = None,
    thumbnail_blueprint_lines: list[str] | None = None,
    pose_plan_lines: list[str] | None = None,
    character_identity_contract_lines: list[str] | None = None,
    scale_contract_lines: list[str] | None = None,
    character_accuracy_lines: list[str] | None = None,
) -> str:
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
    scene_block = f"- 표지 핵심 장면: {thumbnail_scene_prompt}\n" if thumbnail_scene_prompt else ""
    subtitle_block = f"- 썸네일 부제목: {subtitle.strip()}\n" if subtitle.strip() else ""
    caption_block = f"- 인스타 캡션 핵심 문맥: {caption.strip()}\n" if caption.strip() else ""
    shot_plan_block = f"- 사전 확정 샷 플랜: {shot_plan}\n" if shot_plan.strip() else ""
    thumbnail_blueprint_lines = [str(line).strip() for line in (thumbnail_blueprint_lines or []) if str(line).strip()]
    thumbnail_blueprint_block = ""
    if thumbnail_blueprint_lines:
        thumbnail_blueprint_block = "- 사전 확정 썸네일 블루프린트:\n" + "\n".join(
            f"  - {line}" for line in thumbnail_blueprint_lines
        ) + "\n"
    pose_plan_lines = [str(line).strip() for line in (pose_plan_lines or []) if str(line).strip()]
    pose_plan_block = ""
    if pose_plan_lines:
        pose_plan_block = "- 사전 확정 포즈 계획:\n" + "\n".join(f"  - {line}" for line in pose_plan_lines) + "\n"
    character_identity_contract_lines = [
        str(line).strip() for line in (character_identity_contract_lines or []) if str(line).strip()
    ]
    character_identity_block = ""
    if character_identity_contract_lines:
        character_identity_block = "- 사전 확정 캐릭터 정체성 계약:\n" + "\n".join(
            f"  - {line}" for line in character_identity_contract_lines
        ) + "\n"
    scale_contract_lines = [str(line).strip() for line in (scale_contract_lines or []) if str(line).strip()]
    scale_contract_block = ""
    if scale_contract_lines:
        scale_contract_block = "- 사전 확정 상대 크기 계약:\n" + "\n".join(
            f"  - {line}" for line in scale_contract_lines
        ) + "\n"
    character_accuracy_lines = [str(line).strip() for line in (character_accuracy_lines or []) if str(line).strip()]
    character_accuracy_block = ""
    if character_accuracy_lines:
        character_accuracy_block = "- 사전 확정 등장인물 정확성 계약:\n" + "\n".join(
            f"  - {line}" for line in character_accuracy_lines
        ) + "\n"
    background_text_lines = [str(line).strip() for line in (background_text_lines or []) if str(line).strip()]
    if background_text_mode == "exact" and background_text_lines:
        background_plan_block = (
            "- 배경 텍스트 계획: 아래 텍스트만 정확한 철자로 허용한다.\n"
            + "\n".join(f"  - {line}" for line in background_text_lines)
            + "\n- 위 목록 외의 읽을 수 있는 배경 텍스트는 만들지 않는다.\n"
        )
    elif background_text_mode == "minimal":
        background_plan_block = (
            "- 배경 텍스트 계획: 장면 이해에 필요한 짧은 실제 단어 1개 정도만 허용하고, 나머지는 아이콘/막대/빈 줄로 처리한다.\n"
        )
    else:
        background_plan_block = "- 배경 텍스트 계획: 읽을 수 있는 배경 텍스트를 만들지 말고 아이콘/막대/빈 줄로만 표현한다.\n"
    scope_block = (
        f"- 에피소드 범위 타입: {(episode_scope or 'single_location').strip()}\n"
        f"- 부제목 범위 타입: {(subtitle_scope or episode_scope or 'single_location').strip()}\n"
        f"- 범위 요약: {scope_summary.strip()}\n"
    )
    return (
        f"{WEBTOON_STYLE_PREFIX}.\n\n"
        f"{base_prompt}\n\n"
        "추가 지시:\n"
        "- 이 이미지는 웹툰 시리즈 표지(커버) 이미지이다. 본문 컷이 아니다.\n"
        f"- 공개 카피 맥락: 제목 '{title.strip()}', 부제목 '{subtitle.strip()}', 캡션 '{caption.strip()}'.\n"
        f"{scope_block}"
        f"{shot_plan_block}"
        f"{thumbnail_blueprint_block}"
        f"{pose_plan_block}"
        f"{character_identity_block}"
        f"{scale_contract_block}"
        f"{character_accuracy_block}"
        "- title, subtitle, caption, subtitle_scope, scope_summary가 약속한 같은 에피소드 범위를 시각적으로 보여줘야 한다.\n"
        "- episode_scope가 single_location이면 표지도 그 단일 현장/상황의 대표 장면이어야 한다. episode_scope가 journey면 이동 전체를 대표하는 출발 사건 또는 핵심 전환 장면이어야 한다.\n"
        "- subtitle_scope가 single_location이면 부제목이 말하는 현장/상황을 벗어나면 안 된다. subtitle_scope가 journey면 여러 장소 이동이 한 여정으로 읽혀야 한다.\n"
        "- 표지는 주제의 출발 사건과 핵심 장소를 한눈에 보여줘야 한다. 예쁜 일반 풍경만 그리면 안 된다.\n"
        "- 주제의 핵심 행동, 공간, 소품 단서가 배경이나 장면 연출에 직접 드러나야 한다.\n"
        "- 썸네일 배경은 주제 전체를 한눈에 설명하는 포괄적 대표 배경이어야 한다. 특정 패널 하나의 좁은 부분 장면처럼 보이면 실패다.\n"
        "- 썸네일은 본문 6컷보다 상위 개념의 대표 공간과 대표 사건을 압축한 establishing shot이어야 한다.\n"
        "- 함께 제공된 참조 이미지의 두 캐릭터를 의인화하여 그린다.\n"
        "- 두 캐릭터는 반드시 두 발로 서서, 앞발을 손처럼 사용하는 의인화 캐릭터로 그린다.\n"
        "- 두 캐릭터 모두 골반과 상체가 사람처럼 직립해야 하며, 앞발로 바닥을 짚어 체중을 싣는 자세는 금지한다.\n"
        "- 각 캐릭터의 팔/앞발은 좌우 한 쌍만 자연스럽게 보여야 한다. 여분의 손, 세 번째 앞발, 끊긴 팔, 팔이 없는 실루엣은 전부 실패다.\n"
        "- 캐릭터는 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단처럼 사람이 올라서면 안 되는 표면 위에 서지 않는다. 이런 물체는 배경 소품이고, 캐릭터는 주변의 정상적인 바닥, 계단, 좌석, 플랫폼 면 위에 선다.\n"
        "- 계단, 경사면, 이동 장치가 있는 장면이어도 두 캐릭터는 끝까지 완전한 이족보행을 유지한다. 한 캐릭터라도 네 발로 기어오르거나 앞발로 체중을 지탱하는 자세는 금지한다.\n"
        "- thumbnail_scene_prompt나 장면 설명에 소품, 가방, 캐리어, 지도, 티켓, 여권, 휴대폰 같은 핵심 물건이 지정되면 그 물건을 정확히 그대로 그린다. 비슷한 다른 물건으로 바꾸지 않는다.\n"
        "- 캐릭터가 무엇을 손에 들고 있는지, 어떤 손으로 잡고 있는지, 무엇을 가리키는지는 scene prompt의 지시를 우선한다.\n"
        "- thumbnail_scene_prompt에 문서철, 지도, 여권, 티켓, 휴대폰, 캐리어, 가방처럼 핵심 소품이 지정되면 종류와 주된 색상까지 그대로 지킨다. 비슷한 다른 소품으로 바꾸면 실패다.\n"
        "- scene prompt가 두 개 이상의 동일 소품을 요구하면 그 개수를 정확히 지켜 분리된 개별 물건으로 보이게 그린다. 두 권 여권을 한 권짜리 두꺼운 책자처럼 합치면 실패다.\n"
        f"{BACKGROUND_TEXT_SIMPLIFICATION_BLOCK}"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 검은 캐릭터(콜라): 자신감 있는 포즈, 노란(황금색) 눈, 검은 단색 털. 왼쪽에 배치.\n"
        "- [CRITICAL] 회색 줄무늬 캐릭터(제로): 약간 걱정하는 표정, 반드시 갈색(brown) 눈 — 노란색/황색/호박색 절대 금지, 회색+검은 줄무늬, 분홍 귀. 오른쪽에 배치.\n"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 선글라스, 모자, 목걸이, 배지, 목도리 같은 모든 액세서리는 금지한다. 참조 이미지에 없는 장식은 어떤 장면에서도 추가하지 않는다.\n"
        "- 두 캐릭터의 체형은 참조 이미지처럼 일반적인 집고양이 기반의 둥글고 자연스러운 의인화 체형이어야 한다. 근육질 상체, 보디빌더형 팔/가슴, 과장된 식스팩은 금지한다.\n"
        "- 콜라는 제로보다 전신 기준으로 약간 더 크게 보이게 그린다. 대략 10~15% 크게 유지한다.\n"
        "- 목표 비율은 약 12%다. 허용 범위는 8~15%이며, 18%를 넘는 과장은 실패다.\n"
        "- 콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다. 머리, 몸통, 전체 키에서 콜라가 항상 더 크게 읽혀야 한다.\n"
        "- 이 크기 서열은 썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙이다. 어느 컷에서도 제로가 더 크거나 같게 읽히면 실패다.\n"
        "- 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지해야 한다. 어떤 컷에서는 10~15% 차이였는데 다른 컷에서 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아지면 실패다.\n"
        "- 원근이나 포즈 때문에 제로가 더 커 보일 수 있으면 카메라 거리와 배치를 조정해서라도 콜라가 더 크게 보이게 만든다.\n"
        "- 주연 캐릭터는 정확히 콜라와 제로 둘뿐이다. 추가 고양이형 캐릭터, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 공간 설명에 필요한 비주요 인간 배경 인물이나 대기 줄은 필요할 때만 배경 요소로 허용한다.\n"
        "- 인간 배경 인물은 항상 주인공보다 작고 흐리게, 뒤쪽에 배치한다. 인간 배경 인물이 콜라나 제로보다 크거나 전면에 오면 실패다.\n"
        "- 한 장 안에 같은 장면을 두 번 반복하거나 위아래/좌우로 분할된 멀티패널 구도를 절대 만들지 않는다.\n"
        "- 상단 하늘이나 빈 여백에 반응용 얼굴 컷인, 추가 전신, 잘린 캐릭터 일부를 넣지 않는다.\n"
        "- 배경은 이번 주제의 대표 공간과 사건 순간을 분명히 보여주는 단일 티저 장면이어야 한다.\n"
        "- 썸네일은 1컷의 반복이 아니라 별도의 예고 장면이어야 한다. 1컷과 같은 나란히 서기 포즈, 같은 시선, 같은 캐리어 배치, 같은 거리감은 피한다.\n"
        "- 본문 1~6컷 어느 곳에서도 썸네일의 배경 구조, 서브로케이션, 카메라 거리, 대표 간판, 대표 소품 배치를 다시 사용하면 안 된다. 같은 메인 홀/복도/대기 공간 구조에 간판만 바꾼 버전도 금지다.\n"
        "- 이 금지는 특정 한 컷에만 적용되는 것이 아니라 패널 1부터 패널 6 전체에 적용된다. 어느 컷에서든 썸네일 대표 배경이 재등장하면 실패다.\n"
        "- 썸네일은 구조적 지문까지 본문과 달라야 한다. 천장 형태, 중앙 홀 구조, 복도 깊이, 출구 프레임, 카운터 배치, 바닥 패턴, 대표 간판 군집이 본문 어느 컷과도 겹치면 실패다.\n"
        "- journey 에피소드라면 썸네일을 특정 패널 장소의 넓은 버전으로 만들지 말고, 전체 여정을 소개하는 별도 전환 공간 또는 대표 출발 장면으로 만든다.\n"
        "- 감정 표현은 얼굴과 몸짓으로만 처리한다. 배경 위에 떠 있는 단독 !, ?, 스티커형 반응 표시, 별도 미니 얼굴 컷인은 절대 넣지 않는다.\n"
        "- 이미지 상단 40%는 후처리에서 제목 텍스트를 올릴 공간이므로 단색 또는 하늘/벽 같은 단순한 배경으로 비워둔다.\n"
        "- 이미지 하단 60%에 두 캐릭터가 크고 선명하게 배치.\n"
        "- 만화적 이펙트는 장면 이해에 꼭 필요한 경우에만 아주 절제해서 사용한다. 스티커처럼 보이는 별도 얼굴, 미니 캐릭터, 과한 장식 이펙트는 금지한다.\n"
        "- 말풍선, 자막, 대사 텍스트, 제목 텍스트는 이미지 안에 절대 넣지 않는다.\n"
        f"{background_plan_block}"
        f"- 썸네일 제목: {title.strip()}\n"
        f"{subtitle_block}"
        f"{caption_block}"
        f"{scene_block}"
        f"- 주제 분위기: {topic.strip()}\n"
        f"{notes_block}"
    )


def build_panel_generation_prompt(
    base_prompt: str,
    panel: dict[str, Any],
    title: str,
    subtitle: str,
    caption: str,
    episode_scope: str,
    subtitle_scope: str,
    scope_summary: str,
    character_notes: str,
    dialogue_lines: list[str],
    previous_panel: dict[str, Any] | None = None,
    shot_plan: str = "",
    background_text_mode: str = "",
    background_text_lines: list[str] | None = None,
    prop_plan_lines: list[str] | None = None,
    pose_plan_lines: list[str] | None = None,
    action_contract_lines: list[str] | None = None,
    character_identity_contract_lines: list[str] | None = None,
    scale_contract_lines: list[str] | None = None,
    character_accuracy_lines: list[str] | None = None,
) -> str:
    text_block = "\n".join(f"- {line}" for line in dialogue_lines) if dialogue_lines else "- 텍스트 없음"
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
    shot_plan_block = f"- 사전 확정 샷 플랜: {shot_plan}\n" if shot_plan.strip() else ""
    background_text_lines = [str(line).strip() for line in (background_text_lines or []) if str(line).strip()]
    if background_text_mode == "exact" and background_text_lines:
        background_plan_block = (
            "- 배경 텍스트 계획: 아래 텍스트만 정확한 철자로 허용한다.\n"
            + "\n".join(f"  - {line}" for line in background_text_lines)
            + "\n- 위 목록 외의 읽을 수 있는 배경 텍스트는 만들지 않는다.\n"
        )
    elif background_text_mode == "minimal":
        background_plan_block = (
            "- 배경 텍스트 계획: 필요한 짧은 실제 라벨 1개만 허용하고, 나머지는 아이콘/막대/빈 줄로 처리한다.\n"
        )
    else:
        background_plan_block = "- 배경 텍스트 계획: 읽을 수 있는 배경 텍스트를 만들지 말고 아이콘/막대/빈 줄로만 표현한다.\n"
    prop_plan_lines = [str(line).strip() for line in (prop_plan_lines or []) if str(line).strip()]
    prop_plan_block = ""
    if prop_plan_lines:
        prop_plan_block = "- 사전 확정 소품 계획:\n" + "\n".join(f"  - {line}" for line in prop_plan_lines) + "\n"
    pose_plan_lines = [str(line).strip() for line in (pose_plan_lines or []) if str(line).strip()]
    pose_plan_block = ""
    if pose_plan_lines:
        pose_plan_block = "- 사전 확정 포즈 계획:\n" + "\n".join(f"  - {line}" for line in pose_plan_lines) + "\n"
    action_contract_lines = [str(line).strip() for line in (action_contract_lines or []) if str(line).strip()]
    action_contract_block = ""
    if action_contract_lines:
        action_contract_block = "- 사전 확정 액션 계약:\n" + "\n".join(f"  - {line}" for line in action_contract_lines) + "\n"
    character_identity_contract_lines = [
        str(line).strip() for line in (character_identity_contract_lines or []) if str(line).strip()
    ]
    character_identity_block = ""
    if character_identity_contract_lines:
        character_identity_block = "- 사전 확정 캐릭터 정체성 계약:\n" + "\n".join(
            f"  - {line}" for line in character_identity_contract_lines
        ) + "\n"
    scale_contract_lines = [str(line).strip() for line in (scale_contract_lines or []) if str(line).strip()]
    scale_contract_block = ""
    if scale_contract_lines:
        scale_contract_block = "- 사전 확정 상대 크기 계약:\n" + "\n".join(
            f"  - {line}" for line in scale_contract_lines
        ) + "\n"
    character_accuracy_lines = [str(line).strip() for line in (character_accuracy_lines or []) if str(line).strip()]
    character_accuracy_block = ""
    if character_accuracy_lines:
        character_accuracy_block = "- 사전 확정 등장인물 정확성 계약:\n" + "\n".join(
            f"  - {line}" for line in character_accuracy_lines
        ) + "\n"
    panel_no = int(panel.get("panel_no", 1))
    story_role = str(panel.get("story_role", "")).strip()
    location = str(panel.get("location", "")).strip()
    previous_location = str((previous_panel or {}).get("location", "")).strip()
    continuity_block = _build_prop_continuity_block(panel, previous_panel)
    if panel_no % 2 == 1:
        composition_hint = (
            "- 두 캐릭터를 이미지 하단 중앙에 배치 (콜라 왼쪽, 제로 오른쪽). 상단 좌우에 말풍선 공간 확보.\n"
            "- 캐릭터의 머리와 귀는 패널 상단 18% 영역을 침범하지 않게 두어 상단 말풍선이 얼굴을 가리지 않게 한다.\n"
        )
    else:
        composition_hint = (
            "- 두 캐릭터를 이미지 상단~중앙에 배치 (콜라 왼쪽, 제로 오른쪽). 하단 좌우에 말풍선 공간 확보.\n"
            "- 캐릭터의 발과 꼬리 끝은 패널 하단 18% 영역에 과하게 겹치지 않게 두어 하단 말풍선이 몸을 가리지 않게 한다.\n"
        )
    return (
        f"{WEBTOON_STYLE_PREFIX}.\n\n"
        f"{base_prompt}\n\n"
        "추가 지시:\n"
        "- 이 이미지는 6컷 웹툰의 단일 패널이다.\n"
        f"- 현재는 {panel_no}컷 장면만 그린다.\n"
        f"- 공개 카피 기준: 제목 '{title.strip()}', 부제목 '{subtitle.strip()}', 캡션 '{caption.strip()}'.\n"
        f"- 에피소드 범위 타입: {(episode_scope or 'single_location').strip()}\n"
        f"- 부제목 범위 타입: {(subtitle_scope or episode_scope or 'single_location').strip()}\n"
        f"- 범위 요약: {scope_summary.strip()}\n"
        f"{shot_plan_block}"
        f"{prop_plan_block}"
        f"{pose_plan_block}"
        f"{action_contract_block}"
        f"{character_identity_block}"
        f"{scale_contract_block}"
        f"{character_accuracy_block}"
        "- 이 컷은 위 공개 카피와 범위 요약이 약속한 이야기 범위 안에 있어야 한다.\n"
        "- episode_scope가 single_location이면 패널 변화가 있더라도 같은 현장/상황권 안의 전개로 읽혀야 한다. episode_scope가 journey면 여러 장소를 이동해도 같은 여정 안의 비트로 이어져야 한다.\n"
        "- subtitle_scope가 single_location이면 부제목이 약속한 현장/상황을 벗어난 다른 에피소드처럼 보이면 안 된다.\n"
        "- 함께 제공된 참조 이미지의 캐릭터를 의인화하여 그린다.\n"
        "- 두 캐릭터는 반드시 두 발로 서서 걷고 앞발을 손처럼 사용하는 의인화 캐릭터로 묘사한다.\n"
        "- 두 캐릭터 모두 골반과 상체가 사람처럼 직립해야 한다.\n"
        "- 앞발로 바닥을 짚어 체중을 싣는 일반 동물형 자세는 금지한다.\n"
        "- 각 캐릭터의 팔/앞발은 좌우 한 쌍만 자연스럽게 보여야 한다. 여분의 손, 세 번째 앞발, 잘린 추가 팔, 팔이 통째로 사라진 실루엣은 전부 실패다.\n"
        "- 썸네일부터 마지막 6컷까지 공통 규칙으로, 네 발 보행이나 앞발 체중 지지는 한 번이라도 허용되지 않는다.\n"
        "- 앉는 장면도 골반이 좌석에 닿은 상태에서 상체는 세워 두고, 앞발은 손처럼 사용한다.\n"
        "- 계단, 경사면, 이동 장치가 있는 장면에서도 두 캐릭터는 끝까지 완전한 이족보행을 유지한다. 한 캐릭터라도 네 발로 기어오르거나 앞발로 체중을 지탱하는 자세는 금지한다.\n"
        "- 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단은 배경 장비다. 캐릭터를 그 위에 올려놓지 말고, 항상 옆 바닥/플랫폼/계단 디딤면 위에 세운다.\n"
        "- 장면 설명에 지정된 핵심 소품과 손동작은 정확히 지킨다. 캐리어, 가방, 표, 지도, 여권, 휴대폰, 문서, 버튼 조작 같은 오브젝트를 임의의 다른 물건으로 바꾸지 않는다.\n"
        "- 같은 소품이 여러 컷에 반복되면 반드시 같은 물건으로 그린다. 중간에 다른 색상, 다른 크기, 다른 형태, 다른 종류로 바꾸지 않는다.\n"
        "- 반복 소품은 여권을 다른 서류로, 서류 파일을 다른 종이 묶음으로, 캐리어를 다른 가방으로 바꾸는 식의 치환이 절대 허용되지 않는다.\n"
        "- scene prompt가 두 개의 여권/문서/티켓처럼 수량을 지정하면 그 정확한 개수를 지키고, 각 물건의 테두리와 겹침을 분명히 보여 하나의 합쳐진 물체처럼 보이지 않게 한다.\n"
        "- 소품의 실제성도 유지한다. 열려 있어야 하는 여권은 열린 채로, 보이기로 한 도장은 실제로 보이게, 가리키기로 한 문서는 정확한 면을 향해 가리켜야 한다.\n"
        f"{continuity_block}"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 선글라스, 모자, 목걸이, 배지, 목도리 같은 모든 액세서리는 금지한다. 참조 이미지에 없는 장식은 어떤 컷에서도 추가하지 않는다.\n"
        "- 콜라(검은색)는 항상 화면 왼쪽, 제로(회색 줄무늬)는 항상 화면 오른쪽에 배치.\n"
        "- 콜라의 전신 크기는 제로보다 늘 약간 더 크게 보이게 그린다. 대략 10~15% 더 크게 유지한다.\n"
        "- 목표 비율은 약 12%다. 허용 범위는 8~15%이며, 18%를 넘는 과장은 실패다.\n"
        "- 콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다. 머리, 몸통, 전체 키에서 콜라가 항상 더 크게 읽혀야 한다.\n"
        "- 이 크기 서열은 썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙이다. 어느 컷에서도 제로가 더 크거나 같게 읽히면 실패다.\n"
        "- 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지해야 한다. 이번 컷에서만 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아져서는 안 된다.\n"
        "- 원근이나 포즈 때문에 제로가 더 커 보일 수 있으면 배치와 카메라를 조정해서라도 콜라가 더 크게 보이게 만든다.\n"
        "- 주연 캐릭터는 정확히 콜라와 제로 둘뿐이다. 추가 고양이형 인물, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 공공장소를 설명하기 위한 비주요 인간 배경 인물, 승객, 점원, 직원, 대기 줄은 필요하면 배경 요소로 허용한다.\n"
        "- 인간 배경 인물은 항상 주인공보다 작고 흐리게, 뒤쪽에 배치한다. 인간 배경 인물이 콜라나 제로보다 크거나 전면에 오면 실패다.\n"
        "- 두 주인공은 같은 장면 안의 같은 바닥 평면에 한 번씩만 등장한다. 상단 하늘이나 빈 여백에 별도의 캐릭터 컷인을 만들지 않는다.\n"
        "- 이 이미지는 단일 패널 한 컷만 담아야 한다. 위아래 두 장면, 좌우 분할, 반복된 동일 장면, 만화 페이지처럼 여러 컷이 섞인 구도를 절대 만들지 않는다.\n"
        "- 물건 잡기, 기계 조작, 제스처 등 사람처럼 행동하는 모습을 자연스럽게 표현한다.\n"
        "- 캐릭터 얼굴형과 털색은 참조 이미지와 일치시키되, 자세는 반드시 인간형 직립 이족보행으로 유지한다.\n"
        "- 이전 컷들과 같은 외형을 유지한다. 털색, 줄무늬, 체형, 꼬리 길이를 임의로 바꾸지 않는다.\n"
        "- [CRITICAL] 제로의 눈은 반드시 갈색(brown)이어야 한다. 노란색, 황색, 호박색, 초록색은 절대 금지이며 어느 컷에서도 예외가 없다.\n"
        "- 콜라가 더 커 보여야 한다는 이유로 근육질 상체나 과도한 벌크를 추가하지 않는다. 둘 다 참조 이미지처럼 자연스러운 일반 체형을 유지한다.\n"
        "- 앉아 있는 장면이라면 최소 한 앞발은 무릎, 좌석, 테이블, 휴대폰 같은 물체와 상호작용하며 손처럼 보여야 한다.\n"
        "- 만화적 이펙트는 감정 전달에 꼭 필요할 때만 최소한으로 사용한다. 스티커처럼 분리된 !, ?, 미니 얼굴, 과한 땀방울 장식은 만들지 않는다.\n"
        "- 감정 표현은 얼굴과 몸짓으로만 전달하고, 배경 위에 떠 있는 단독 !, ?, 번쩍 표시, 반응용 컷인을 절대 만들지 않는다.\n"
        "- 표정은 과장되게, 감정이 한눈에 읽히도록.\n"
        "- 배경은 이번 주제의 생활 공간과 사건 맥락을 구체적으로.\n"
        "- 배경은 이번 컷의 사건 진행에 맞게 반드시 변화해야 한다. 이전 컷과 다른 장소면 구도와 소품까지 명확히 달라야 한다.\n"
        "- 참조 이미지나 이전 컷의 배경 구도를 복사하지 말고, 캐릭터 외형만 유지한 채 이번 컷 장소를 새로 그린다.\n"
        "- 이번 패널 배경은 썸네일의 대표 배경 구조나 서브로케이션을 재사용하면 안 된다. 같은 메인 홀/복도/대기 공간 구조에 간판만 바꾼 버전도 금지다.\n"
        "- 썸네일 대표 배경은 본문 여섯 컷 전체에서 금지된 전용 배경이다. 어느 패널 번호든 예외 없이 중복되면 실패다.\n"
        "- 썸네일과 다른 구조적 지문을 반드시 만든다. 천장 형태, 중앙 안내판 배치, 아트리움/복도/게이트 프레임, 카운터 구조, 바닥 패턴, 출구문 형태가 썸네일과 같아 보이면 실패다.\n"
        "- location이 바뀌는 컷은 구조적 배경도 확실히 바꾼다. 같은 건물을 넓게/좁게만 다시 그리거나, 같은 홀에 간판만 바꾼 버전은 금지한다.\n"
        "- 절대로 말풍선, 자막, 대사 텍스트를 이미지 안에 그리지 않는다. 장면 안의 실제 간판/기계 화면은 허용되지만 편집용 UI 오버레이는 없어야 한다.\n"
        f"{BACKGROUND_TEXT_SIMPLIFICATION_BLOCK}"
        f"{background_plan_block}"
        f"{composition_hint}"
        "- 배경에 상품명, 브랜드, 간판 텍스트를 최소화한다. 외국어 단어보다 아이콘/색 막대를 우선한다.\n"
        "- 가상의 브랜드명, 의미 없는 텍스트, 철자 불확실한 외국어를 배경에 절대 넣지 않는다.\n"
        "- 패널 테두리, 컷 번호, 분할선도 그리지 않는다.\n"
        f"{f'- 이 컷의 서사 역할: {story_role}\\n' if story_role else ''}"
        f"{f'- 이 컷의 주요 장소: {location}\\n' if location else ''}"
        f"{f'- 이전 컷 장소: {previous_location}\\n' if previous_location else ''}"
        f"{'- 이번 컷은 이전 컷과 장소가 다르므로 배경이 확실히 바뀌어야 한다.\\n' if previous_location and location and previous_location != location else ''}"
        f"{'- 1컷은 썸네일의 직후 비트여야 하므로, 캐릭터 포즈와 소품 배치를 썸네일과 다르게 바꾼다.\\n' if panel_no == 1 else ''}"
        f"- 장면 설명: {panel.get('scene_prompt', '').strip()}\n"
        "- 이 컷의 말풍선 대사(참고용, 이미지에 넣지 않음):\n"
        f"{text_block}"
        f"{notes_block}"
    )


def _build_prompt_plan_package(
    brief: dict[str, Any],
    *,
    sanitized_topic: str,
) -> dict[str, Any]:
    panels = _materialize_panels_from_brief(brief)
    episode_prop_registry = _build_episode_prop_registry(panels)
    character_identity_contract = _build_character_identity_contract()
    scale_contract = _build_scale_contract(slide_type="panel")
    thumbnail_scale_contract = _build_scale_contract(slide_type="thumbnail")
    thumbnail_character_accuracy = _build_character_accuracy_contract(
        slide_type="thumbnail",
        scene_prompt=str(brief.get("thumbnail_scene_prompt", "")).strip(),
    )
    thumbnail_blueprint_lines = _build_thumbnail_blueprint(
        brief,
        panels=panels,
        episode_prop_registry=episode_prop_registry,
    )
    thumbnail_pose_plan_lines = _build_thumbnail_pose_plan(brief)
    thumbnail_background_plan = _derive_background_text_plan(
        slide_type="thumbnail",
        location=str(brief.get("scope_summary", "")).strip(),
        scene_prompt=str(brief.get("thumbnail_scene_prompt", "")).strip(),
        key_props=[],
    )
    thumbnail_plan = {
        "shot_plan": _build_thumbnail_shot_plan(brief),
        "background_text_mode": thumbnail_background_plan["mode"],
        "background_text_lines": thumbnail_background_plan["lines"],
        "thumbnail_blueprint_lines": thumbnail_blueprint_lines,
        "pose_plan_lines": thumbnail_pose_plan_lines,
        "character_accuracy_lines": thumbnail_character_accuracy,
        "dialogue_lines": [],
        "final_prompt": build_thumbnail_generation_prompt(
            brief["image_prompt"],
            title=brief["title"],
            subtitle=brief.get("thumbnail_subtitle", ""),
            topic=sanitized_topic,
            caption=brief["caption"],
            episode_scope=brief.get("episode_scope", ""),
            subtitle_scope=brief.get("subtitle_scope", ""),
            scope_summary=brief.get("scope_summary", ""),
            character_notes=brief.get("character_notes", ""),
            thumbnail_scene_prompt=brief.get("thumbnail_scene_prompt", ""),
            shot_plan=_build_thumbnail_shot_plan(brief),
            background_text_mode=thumbnail_background_plan["mode"],
            background_text_lines=thumbnail_background_plan["lines"],
            thumbnail_blueprint_lines=thumbnail_blueprint_lines,
            pose_plan_lines=thumbnail_pose_plan_lines,
            character_identity_contract_lines=character_identity_contract,
            scale_contract_lines=thumbnail_scale_contract,
            character_accuracy_lines=thumbnail_character_accuracy,
        ),
    }

    panel_plans: list[dict[str, Any]] = []
    for index, panel in enumerate(panels):
        previous_panel = panels[index - 1] if index > 0 else None
        background_plan = _derive_background_text_plan(
            slide_type="panel",
            location=str(panel.get("location", "")).strip(),
            scene_prompt=str(panel.get("scene_prompt", "")).strip(),
            key_props=_panel_key_props(panel),
        )
        shot_plan = _build_panel_shot_plan(panel, previous_panel)
        prop_plan_lines = _build_panel_prop_plan(
            panel,
            previous_panel=previous_panel,
            episode_prop_registry=episode_prop_registry,
        )
        pose_plan_lines = _build_panel_pose_plan(panel)
        action_contract_lines = _build_panel_action_contract(panel, previous_panel)
        character_accuracy_lines = _build_character_accuracy_contract(
            slide_type="panel",
            scene_prompt=str(panel.get("scene_prompt", "")).strip(),
        )
        panel_plans.append(
            {
                "panel_no": int(panel.get("panel_no", index + 1)),
                "shot_plan": shot_plan,
                "background_text_mode": background_plan["mode"],
                "background_text_lines": background_plan["lines"],
                "key_props": _panel_key_props(panel),
                "carryover_props": _panel_carryover_props(panel, previous_panel),
                "prop_plan_lines": prop_plan_lines,
                "pose_plan_lines": pose_plan_lines,
                "character_accuracy_lines": character_accuracy_lines,
                "action_contract_lines": action_contract_lines,
                "dialogue_lines": list(panel.get("dialogue_lines", [])),
                "final_prompt": build_panel_generation_prompt(
                    brief["image_prompt"],
                    panel,
                    brief["title"],
                    brief.get("thumbnail_subtitle", ""),
                    brief["caption"],
                    brief.get("episode_scope", ""),
                    brief.get("subtitle_scope", ""),
                    brief.get("scope_summary", ""),
                    brief.get("character_notes", ""),
                    panel.get("dialogue_lines", []),
                    previous_panel=previous_panel,
                    shot_plan=shot_plan,
                    background_text_mode=background_plan["mode"],
                    background_text_lines=background_plan["lines"],
                    prop_plan_lines=prop_plan_lines,
                    pose_plan_lines=pose_plan_lines,
                    action_contract_lines=action_contract_lines,
                    character_identity_contract_lines=character_identity_contract,
                    scale_contract_lines=scale_contract,
                    character_accuracy_lines=character_accuracy_lines,
                ),
            }
        )

    prompt_plan = {
        "thumbnail": thumbnail_plan,
        "character_identity_contract": character_identity_contract,
        "scale_contract": scale_contract,
        "episode_prop_registry": episode_prop_registry,
        "panels": panel_plans,
    }
    prompt_plan["validation"] = _validate_prompt_plan_package(prompt_plan, panels=panels)
    return prompt_plan


def _lookup_panel_prompt_plan(prompt_plan: dict[str, Any], panel_no: int) -> dict[str, Any]:
    for item in prompt_plan.get("panels", []):
        if int(item.get("panel_no", 0) or 0) == panel_no:
            return item
    return {}


def _apply_corrected_dialogues(
    panel: dict[str, Any],
    corrected_lines: list[str],
    *,
    corrected_speaker_dialogues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    original_blocks = normalize_speaker_dialogues(panel)
    if corrected_speaker_dialogues:
        normalized_blocks = normalize_speaker_dialogues({"speaker_dialogues": corrected_speaker_dialogues})
        if any(block.get("dialogue_lines") for block in normalized_blocks):
            return {
                **panel,
                "speaker_dialogues": normalized_blocks,
                "dialogue_lines": flatten_speaker_dialogues(normalized_blocks),
            }

    if not corrected_lines:
        return {
            **panel,
            "speaker_dialogues": original_blocks,
            "dialogue_lines": flatten_speaker_dialogues(original_blocks),
        }

    kolla_count = max(1, len(original_blocks[0].get("dialogue_lines", []))) if original_blocks else 1
    zero_count = max(1, len(original_blocks[1].get("dialogue_lines", []))) if len(original_blocks) > 1 else 1
    minimum_needed = kolla_count + zero_count
    padded = list(corrected_lines)
    if len(padded) < minimum_needed:
        padded.extend(panel.get("dialogue_lines", [])[len(padded):minimum_needed])
    kolla_lines = [line for line in padded[:kolla_count] if str(line).strip()]
    zero_lines = [line for line in padded[kolla_count : kolla_count + zero_count] if str(line).strip()]
    speaker_dialogues = [
        {"speaker": "kolla", "dialogue_lines": kolla_lines},
        {"speaker": "zero", "dialogue_lines": zero_lines},
    ]
    return {
        **panel,
        "speaker_dialogues": speaker_dialogues,
        "dialogue_lines": flatten_speaker_dialogues(speaker_dialogues),
    }


def build_bubble_only_ocr_image(image_bytes: bytes, render_layout: list[dict[str, Any]]) -> bytes:
    boxes = []
    for item in render_layout:
        if item.get("bubble_boxes"):
            boxes.extend(item["bubble_boxes"])
        elif item.get("bubble_box"):
            boxes.append(item["bubble_box"])
    if not boxes:
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as image:
        source = image.convert("RGB")
        crops: list[Image.Image] = []
        max_width = 0
        total_height = 0
        gutter = 18

        for raw_box in boxes:
            x1, y1, x2, y2 = [int(value) for value in raw_box]
            padding = 12
            crop = source.crop(
                (
                    max(0, x1 - padding),
                    max(0, y1 - padding),
                    min(source.width, x2 + padding),
                    min(source.height, y2 + padding),
                )
            )
            crops.append(crop)
            max_width = max(max_width, crop.width)
            total_height += crop.height

        total_height += gutter * max(0, len(crops) - 1)
        canvas = Image.new("RGB", (max_width, total_height), color=(255, 252, 248))
        cursor_y = 0
        for crop in crops:
            canvas.paste(crop, (0, cursor_y))
            cursor_y += crop.height + gutter

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()


def correct_background_text(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    base_image_bytes: bytes,
    scene_prompt: str,
    *,
    reference_images: list[tuple[bytes, str]] | None = None,
    reference_image_paths: list[Path] | None = None,
    max_attempts: int = 2,
) -> tuple[bytes, dict[str, Any]]:
    current_bytes = base_image_bytes
    history: list[dict[str, Any]] = []
    last_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}

    for attempt in range(1, max_attempts + 1):
        try:
            check = ocr_client.check_background_text(current_bytes, scene_prompt)
        except Exception as exc:
            logger.warning("배경 텍스트 검사 실패, 건너뜁니다: %s", exc)
            return current_bytes, {
                "has_errors": False,
                "background_texts": [],
                "corrections": [],
                "skipped": str(exc),
                "attempts": history,
            }

        last_check = check
        history.append({"attempt": attempt, **check})
        if not check["has_errors"] or not check["corrections"]:
            return current_bytes, {**check, "attempts": history}

        fix_parts = [
            f'"{c["found"]}" -> "{_simplify_background_correction(c["found"], c["correct"], c.get("reason", ""))}"'
            for c in check["corrections"]
        ]
        edit_prompt = (
            "Fix only the background signage and screen text legibility in this image as listed below.\n"
            "Do NOT change characters, composition, colors, or art style.\n"
            "Do NOT add speech bubbles or any new UI.\n"
            "Preserve every existing object except the exact text glyphs listed below.\n"
            "If the requested correction cannot be applied cleanly, regenerate only the sign, display, or label region so the final visible text becomes clearly readable.\n"
            "Use the smallest readable label set possible.\n"
            "For timetable boards or digital displays, keep only one header plus one to three short rows such as 'INFO', 'Gate A12', or 'On Time'. Remove every other microtext.\n"
            "For forms and documents, keep only one to three large labels such as 'DOCUMENT', 'NAME', or 'ID'. Leave the rest blank or as clean lines.\n"
            "For stamps or seals, keep only one short stamp label plus one short date line. Never draw circular microtext.\n"
            "For UI microtext, use icons, blank bars, or one short label only.\n"
            "Keep any story-critical label readable as real words when it drives the scene, such as aisle labels, counter signs, gate labels, exit signs, price labels, or status words.\n"
            "Corrections:\n" + "\n".join(fix_parts)
        )

        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
                reference_images=reference_images,
                reference_image_paths=reference_image_paths,
                edit_image_bytes=current_bytes,
                edit_image_mime_type="image/png",
            )
            current_bytes = fixed_bytes if fixed_mime == "image/png" else convert_image_bytes(fixed_bytes, "PNG")
        except Exception as exc:
            logger.warning("배경 텍스트 교정 실패 (시도 %d/%d): %s", attempt, max_attempts, exc)
            return current_bytes, {**check, "attempts": history, "edit_error": str(exc)}

    try:
        return current_bytes, {**last_check, "attempts": history, "max_attempts_reached": True}
    except Exception:
        return current_bytes, {**last_check, "attempts": history, "max_attempts_reached": True}


def correct_character_composition(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    base_image_bytes: bytes,
    scene_prompt: str,
    *,
    reference_images: list[tuple[bytes, str]] | None = None,
    reference_image_paths: list[Path] | None = None,
    max_attempts: int = 2,
) -> tuple[bytes, dict[str, Any]]:
    current_bytes = base_image_bytes
    history: list[dict[str, Any]] = []
    last_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}

    for attempt in range(1, max_attempts + 1):
        try:
            check = ocr_client.check_character_composition(current_bytes, scene_prompt)
        except Exception as exc:
            logger.warning("캐릭터 구성 검사 실패, 건너뜁니다: %s", exc)
            return current_bytes, {
                "has_issues": False,
                "issues": [],
                "edit_instruction": "",
                "skipped": str(exc),
                "attempts": history,
            }

        last_check = check
        history.append({"attempt": attempt, **check})
        if not check["has_issues"] or not check["edit_instruction"]:
            return current_bytes, {**check, "attempts": history}

        edit_prompt = (
            "Edit this existing webtoon panel image.\n"
            "Use the attached character reference images as immutable master references for the two protagonists whenever they are provided.\n"
            "Keep the same art style, color palette, and overall story beat.\n"
            "Preserve the scene intent, but if the current background contradicts the requested scene cues, adjust the key background elements as needed.\n"
            "Fix only character composition issues.\n"
            "Requirements:\n"
            "- Keep exactly one Kolla on the left and one Zero on the right.\n"
            "- Remove any extra cat-like duplicate characters, floating cut-in portraits, detached upper bodies, cropped partial character copies, or upper-corner silhouettes.\n"
            "- Background human extras may remain only as minor distant scene elements, never as extra protagonists.\n"
            "- Do not place any character in the upper sky area except the two main full-body characters in the main scene.\n"
            "- Keep Kolla slightly larger than Zero, about 10 to 15 percent bigger.\n"
            "- Target a visual gap of about 12 percent. Acceptable range is 8 to 15 percent. Anything above 18 percent is a failure.\n"
            "- This size rule is absolute from the thumbnail through panel 6. Kolla must remain visibly larger than Zero in head size, torso mass, and full-body silhouette. Never let Zero read as the larger or equal-sized character.\n"
            "- Keep the Kolla-to-Zero size ratio stable across the whole episode. Do not suddenly enlarge Kolla or shrink Zero beyond the same narrow 10 to 15 percent band.\n"
            "- Both protagonists must stay anthropomorphic bipeds with upright pelvis and upright torso.\n"
            "- Never let either protagonist stand, walk, run, crawl, or brace on all fours.\n"
            "- Forepaws must act like hands only and must never be weight-bearing front legs.\n"
            "- Keep exactly one natural left forelimb and one natural right forelimb per character. Never create extra hands, duplicated paws, detached arm stubs, or missing arms.\n"
            "- If seated, the pelvis may rest on a seat, but the torso must remain upright and the forepaws must still read as hands.\n"
            "- Never place either protagonist on top of a baggage belt, baggage carousel, train track, rail, checkout belt, or escalator handrail. Keep them on the proper floor, platform, or stair tread beside the machinery.\n"
            "- In stair or escalator scenes, Zero must remain fully bipedal and upright rather than dropping to four legs.\n"
            "- If the requested scene specifies key props or hand interactions, restore those exact props and actions instead of inventing substitutes.\n"
            "- Keep prop ownership and left-versus-right paw usage exactly as requested. Do not duplicate the same folder, passport, or ticket across both hands or both characters.\n"
            "- Do not add speech bubbles, captions, or UI elements.\n"
            "Specific fixes:\n"
            f"{check['edit_instruction']}"
        )
        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
                reference_images=reference_images,
                reference_image_paths=reference_image_paths,
                edit_image_bytes=current_bytes,
                edit_image_mime_type="image/png",
            )
            current_bytes = fixed_bytes if fixed_mime == "image/png" else convert_image_bytes(fixed_bytes, "PNG")
        except Exception as exc:
            logger.warning("캐릭터 구성 교정 실패 (시도 %d/%d): %s", attempt, max_attempts, exc)
            return current_bytes, {**check, "attempts": history, "edit_error": str(exc)}

    return current_bytes, {**last_check, "attempts": history, "max_attempts_reached": True}


def _build_reference_image_parts(reference_image_paths: list[Path]) -> list[tuple[bytes, str]]:
    parts: list[tuple[bytes, str]] = []
    for path in reference_image_paths:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append((path.read_bytes(), mime_type))
    return parts


def _build_character_priority_gate_errors(
    character_check: dict[str, Any],
    reference_check: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if reference_check.get("has_issues"):
        message = _quality_gate_error_message("캐릭터 참조 일관성", reference_check)
        if message:
            errors.append(message)

    biped_failed = any(
        [
            not character_check.get("bipedal_ok", True),
            not character_check.get("upright_pose_ok", True),
            not character_check.get("forepaws_used_as_hands_ok", True),
            character_check.get("unsafe_surface_pose_detected", False),
        ]
    )
    if biped_failed:
        errors.append(
            _quality_gate_error_message("이족보행 품질 게이트", character_check)
            or "이족보행 품질 게이트 실패"
        )

    if character_check.get("quadruped_detected", False):
        errors.append(
            _quality_gate_error_message("사족보행 방지 품질 게이트", character_check)
            or "사족보행 방지 품질 게이트 실패"
        )

    if (
        not character_check.get("kolla_larger_than_zero_ok", True)
        or not character_check.get("kolla_size_gap_band_ok", True)
    ):
        errors.append(
            _quality_gate_error_message("상대 크기 비율 품질 게이트", character_check)
            or "상대 크기 비율 품질 게이트 실패"
        )

    if (
        not character_check.get("action_owner_ok", True)
        or not character_check.get("left_right_action_ok", True)
    ):
        errors.append(
            _quality_gate_error_message("손/소품 주체 품질 게이트", character_check)
            or "손/소품 주체 품질 게이트 실패"
        )

    if (
        character_check.get("extra_limb_detected", False)
        or character_check.get("missing_limb_detected", False)
    ):
        errors.append(
            _quality_gate_error_message("해부학/팔다리 수 품질 게이트", character_check)
            or "해부학/팔다리 수 품질 게이트 실패"
        )

    if not errors and character_check.get("has_issues"):
        message = _quality_gate_error_message("캐릭터 구성", character_check)
        if message:
            errors.append(message)
    return errors


def _build_progressive_anchor_bundle(
    master_reference_parts: list[tuple[bytes, str]],
    approved_anchor_images: list[dict[str, Any]],
) -> tuple[list[tuple[bytes, str]], list[str]]:
    anchor_refs: list[tuple[bytes, str]] = []
    anchor_descriptions: list[str] = []

    thumbnail_anchor = next(
        (item for item in approved_anchor_images if str(item.get("slide_type", "")).strip() == "thumbnail"),
        None,
    )
    selected_anchors: list[dict[str, Any]] = []
    if thumbnail_anchor is not None:
        selected_anchors.append(thumbnail_anchor)

    panel_anchors = [item for item in approved_anchor_images if str(item.get("slide_type", "")).strip() == "panel"]
    selected_anchors.extend(panel_anchors[-2:])

    for anchor in selected_anchors:
        image_bytes = anchor.get("image_bytes", b"")
        if not image_bytes:
            continue
        anchor_refs.append((image_bytes, str(anchor.get("mime_type", "image/png")) or "image/png"))
        if str(anchor.get("slide_type", "")).strip() == "thumbnail":
            anchor_descriptions.append(
                "승인된 썸네일 앵커: 캐릭터 얼굴형, 털색, 무늬, 눈색, 크기 서열만 유지하고 썸네일 배경/타이포는 복사하지 않는다."
            )
            continue
        panel_no = int(anchor.get("panel_no", 0) or 0)
        location = str(anchor.get("location", "")).strip()
        scene_prompt = str(anchor.get("scene_prompt", "")).strip()
        description = f"승인된 패널 {panel_no} 앵커"
        if location:
            description += f": 장소={location}"
        if scene_prompt:
            description += f", 장면={scene_prompt}"
        description += ". 캐릭터 외형, 소품 상태, 손/발 주체만 이어받고 배경 구도를 복사하지 않는다."
        anchor_descriptions.append(description)

    return [*master_reference_parts, *anchor_refs], anchor_descriptions


def correct_character_reference_consistency(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    base_image_bytes: bytes,
    scene_prompt: str,
    *,
    reference_images: list[tuple[bytes, str]] | None = None,
    reference_image_paths: list[Path],
    max_attempts: int = 2,
) -> tuple[bytes, dict[str, Any]]:
    reference_parts = list(reference_images or _build_reference_image_parts(reference_image_paths))
    if not reference_parts:
        return base_image_bytes, {
            "has_issues": False,
            "issues": [],
            "edit_instruction": "",
            "skipped": "no_reference_images",
            "attempts": [],
        }

    current_bytes = base_image_bytes
    history: list[dict[str, Any]] = []
    last_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}

    for attempt in range(1, max_attempts + 1):
        try:
            check = ocr_client.check_character_reference_consistency(
                current_bytes,
                scene_prompt,
                reference_parts,
            )
        except Exception as exc:
            logger.warning("캐릭터 참조 일관성 검사 실패, 건너뜁니다: %s", exc)
            return current_bytes, {
                "has_issues": False,
                "issues": [],
                "edit_instruction": "",
                "skipped": str(exc),
                "attempts": history,
            }

        last_check = check
        history.append({"attempt": attempt, **check})
        if not check["has_issues"] or not check["edit_instruction"]:
            return current_bytes, {**check, "attempts": history}

        edit_prompt = (
            "Edit this existing webtoon image.\n"
            "Use the attached character reference images as immutable master references.\n"
            "Keep the same scene, pose intent, lighting, and background unless a character detail must change.\n"
            "Fix only recurring character consistency issues.\n"
            "Requirements:\n"
            "- Kolla must match the reference black character exactly in face shape, yellow eyes, ear shape, body proportions, and tail silhouette.\n"
            "- Zero must match the reference gray tabby character exactly in stripe pattern, face shape, pink ears, eye color, body proportions, and tail silhouette.\n"
            "- Keep Kolla truly black, not navy, blue-black, or gray-tinted. Keep Zero light gray with clear dark tabby stripes rather than faded or mismatched markings.\n"
            "- Keep both faces rounded and soft like the references. Do not make them more muscular, angular, sharp-jawed, or bodybuilder-like.\n"
            "- Keep exactly one natural left forelimb and one natural right forelimb per character. Never create extra hands, extra paws, duplicated arms, or missing arms.\n"
            "- Preserve the requested prop owner and left-versus-right paw action exactly. Do not hand the object to the wrong character or flip the paw assignment.\n"
            "- Keep exactly one Kolla on the left and one Zero on the right.\n"
            "- Keep Kolla visibly larger than Zero in every shot, with larger head size, torso mass, and overall silhouette. Never allow equal or reversed size reading.\n"
            "- Keep the Kolla-to-Zero size ratio stable across the thumbnail and panels. Target about 12 percent, keep it within 8 to 15 percent, and never exceed 18 percent.\n"
            "- Keep both protagonists strictly upright bipeds. Never allow all-fours walking, running, crouched quadruped posture, or weight-bearing forepaws.\n"
            "- Do not create extra copies, duplicate scenes, cut-in portraits, or multi-panel layouts.\n"
            "- Do not add speech bubbles, captions, or UI elements.\n"
            "Specific fixes:\n"
            f"{check['edit_instruction']}"
        )
        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
                reference_images=reference_parts,
                edit_image_bytes=current_bytes,
                edit_image_mime_type="image/png",
                reference_image_paths=reference_image_paths,
            )
            current_bytes = fixed_bytes if fixed_mime == "image/png" else convert_image_bytes(fixed_bytes, "PNG")
        except Exception as exc:
            logger.warning("캐릭터 참조 일관성 교정 실패 (시도 %d/%d): %s", attempt, max_attempts, exc)
            return current_bytes, {**check, "attempts": history, "edit_error": str(exc)}

    return current_bytes, {**last_check, "attempts": history, "max_attempts_reached": True}


def correct_thumbnail_panel_distinction(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    base_image_bytes: bytes,
    *,
    thumbnail_scene_prompt: str,
    panel_base_items: list[dict[str, Any]],
    panel_summaries: list[dict[str, Any]],
    reference_images: list[tuple[bytes, str]] | None = None,
    reference_image_paths: list[Path] | None = None,
    max_attempts: int = 2,
) -> tuple[bytes, dict[str, Any]]:
    if not panel_base_items:
        return base_image_bytes, {
            "has_issues": False,
            "issues": [],
            "edit_instruction": "",
            "duplicated_panel_numbers": [],
            "skipped": "no_panel_images",
            "attempts": [],
        }

    panel_parts = [(item["base_bytes"], "image/png") for item in panel_base_items if item.get("base_bytes")]
    if not panel_parts:
        return base_image_bytes, {
            "has_issues": False,
            "issues": [],
            "edit_instruction": "",
            "duplicated_panel_numbers": [],
            "skipped": "no_panel_bytes",
            "attempts": [],
        }

    current_bytes = base_image_bytes
    history: list[dict[str, Any]] = []
    last_check: dict[str, Any] = {
        "has_issues": False,
        "issues": [],
        "edit_instruction": "",
        "duplicated_panel_numbers": [],
    }

    panel_lines = [
        f"- 패널 {summary.get('panel_no')}: {summary.get('location', '')} | {summary.get('scene_prompt', '')}"
        for summary in panel_summaries
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            check = ocr_client.check_thumbnail_panel_distinction(
                current_bytes,
                panel_parts,
                thumbnail_scene_prompt=thumbnail_scene_prompt,
                panel_summaries=panel_summaries,
            )
        except Exception as exc:
            logger.warning("썸네일-패널 구분 검사 실패, 건너뜁니다: %s", exc)
            return current_bytes, {
                "has_issues": False,
                "issues": [],
                "edit_instruction": "",
                "duplicated_panel_numbers": [],
                "skipped": str(exc),
                "attempts": history,
            }

        last_check = check
        history.append({"attempt": attempt, **check})
        if not check["has_issues"] or not check["edit_instruction"]:
            return current_bytes, {**check, "attempts": history}

        edit_prompt = (
            "Edit this existing webtoon cover image.\n"
            "Keep the same episode topic, art style, color palette, and main characters.\n"
            "Fix only thumbnail duplication and teaser-shot issues.\n"
            "Requirements:\n"
            "- The thumbnail must be a separate teaser shot, not a duplicate, crop, or wider variant of any panel.\n"
            "- The thumbnail must use a broad representative background for the whole episode, not a local background reused from any one panel.\n"
            "- Compare against all six panels, not just one duplicated panel number. The thumbnail background must remain unique across the entire body sequence.\n"
            "- Change the location beat, action moment, and camera composition enough to separate it from all panels.\n"
            "- Use a clearly different sub-location or structural background from panel 1 and any duplicated panel, not the same hall with minor sign changes.\n"
            "- Do not reuse the thumbnail background structure, terminal hall, platform layout, gate zone, or signature sign arrangement in any panel-equivalent form.\n"
            "- Also change character posing, gaze direction, hand gestures, and prop placement so it does not resemble panel 1 at a glance.\n"
            "- Remove every extra silhouette, rear-view duplicate, sticker-like mini character, or cropped body in the upper corners.\n"
            "- Keep exactly one Kolla on the left and one Zero on the right.\n"
            "- Do not add speech bubbles, captions, or UI elements.\n"
            "Panel scenes to avoid matching:\n"
            f"{chr(10).join(panel_lines)}\n"
            "Specific fixes:\n"
            f"{check['edit_instruction']}"
        )
        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
                reference_images=reference_images,
                reference_image_paths=reference_image_paths,
                edit_image_bytes=current_bytes,
                edit_image_mime_type="image/png",
            )
            current_bytes = fixed_bytes if fixed_mime == "image/png" else convert_image_bytes(fixed_bytes, "PNG")
        except Exception as exc:
            logger.warning("썸네일-패널 구분 교정 실패 (시도 %d/%d): %s", attempt, max_attempts, exc)
            return current_bytes, {**check, "attempts": history, "edit_error": str(exc)}

    return current_bytes, {**last_check, "attempts": history, "max_attempts_reached": True}


def _run_generation_quality_checks(
    ocr_client: GeminiOcrClient,
    image_bytes: bytes,
    scene_prompt: str,
    *,
    reference_parts: list[tuple[bytes, str]] | None = None,
    include_background_in_errors: bool = True,
) -> dict[str, Any]:
    # Run all three OCR quality checks concurrently to reduce wall-clock time.
    character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": "", "skipped": "no_reference_images"}
    background_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}

    def _check_character() -> dict[str, Any]:
        try:
            return ocr_client.check_character_composition(image_bytes, scene_prompt)
        except Exception as exc:
            logger.warning("캐릭터 구성 검사 실패, 건너뜁니다: %s", exc)
            return {"has_issues": False, "issues": [], "edit_instruction": "", "skipped": str(exc)}

    def _check_reference() -> dict[str, Any]:
        if not reference_parts:
            return {"has_issues": False, "issues": [], "edit_instruction": "", "skipped": "no_reference_images"}
        try:
            return ocr_client.check_character_reference_consistency(image_bytes, scene_prompt, reference_parts)
        except Exception as exc:
            logger.warning("캐릭터 참조 일관성 검사 실패, 건너뜁니다: %s", exc)
            return {"has_issues": False, "issues": [], "edit_instruction": "", "skipped": str(exc)}

    def _check_background() -> dict[str, Any]:
        try:
            return ocr_client.check_background_text(image_bytes, scene_prompt)
        except Exception as exc:
            logger.warning("배경 텍스트 검사 실패, 건너뜁니다: %s", exc)
            return {"has_errors": False, "background_texts": [], "corrections": [], "skipped": str(exc)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_char = pool.submit(_check_character)
        fut_ref = pool.submit(_check_reference)
        fut_bg = pool.submit(_check_background)
        character_check = fut_char.result()
        reference_check = fut_ref.result()
        background_check = fut_bg.result()

    priority_errors = _build_character_priority_gate_errors(character_check, reference_check)
    quality_errors = [
        *priority_errors,
        *(
            [_background_gate_error_message("배경 텍스트", background_check)]
            if include_background_in_errors
            else []
        ),
    ]
    quality_errors = [message for message in quality_errors if message]
    return {
        "character_check": character_check,
        "reference_check": reference_check,
        "background_check": background_check,
        "priority_quality_errors": priority_errors,
        "quality_errors": quality_errors,
    }


def _stabilize_generated_image(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    *,
    image_bytes: bytes,
    scene_prompt: str,
    reference_parts: list[tuple[bytes, str]] | None,
    reference_files: list[Path] | None,
    max_rounds: int,
    apply_background_correction: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    current_bytes = image_bytes
    rounds: list[dict[str, Any]] = []
    last_quality_state = _run_generation_quality_checks(
        ocr_client,
        current_bytes,
        scene_prompt,
        reference_parts=reference_parts,
        include_background_in_errors=apply_background_correction,
    )
    if not last_quality_state["quality_errors"] or max_rounds <= 0:
        return current_bytes, {**last_quality_state, "stabilization_rounds": rounds}

    resolved_reference_files = list(reference_files or [])
    resolved_reference_parts = list(reference_parts or [])

    for round_no in range(1, max_rounds + 1):
        round_entry: dict[str, Any] = {
            "round": round_no,
            "before_quality_errors": list(last_quality_state["quality_errors"]),
        }
        if last_quality_state["character_check"].get("has_issues"):
            current_bytes, composition_check = correct_character_composition(
                image_client,
                ocr_client,
                current_bytes,
                scene_prompt,
                reference_images=resolved_reference_parts,
                reference_image_paths=resolved_reference_files,
                max_attempts=1,
            )
            round_entry["character_check"] = composition_check
        if last_quality_state["reference_check"].get("has_issues") and resolved_reference_parts:
            current_bytes, reference_check = correct_character_reference_consistency(
                image_client,
                ocr_client,
                current_bytes,
                scene_prompt,
                reference_images=resolved_reference_parts,
                reference_image_paths=resolved_reference_files,
                max_attempts=1,
            )
            round_entry["reference_check"] = reference_check
        if apply_background_correction and last_quality_state["background_check"].get("has_errors"):
            current_bytes, background_check = correct_background_text(
                image_client,
                ocr_client,
                current_bytes,
                scene_prompt,
                reference_images=resolved_reference_parts,
                reference_image_paths=resolved_reference_files,
                max_attempts=1,
            )
            round_entry["background_check"] = background_check

        last_quality_state = _run_generation_quality_checks(
            ocr_client,
            current_bytes,
            scene_prompt,
            reference_parts=resolved_reference_parts,
            include_background_in_errors=apply_background_correction,
        )
        round_entry["after_quality_errors"] = list(last_quality_state["quality_errors"])
        rounds.append(round_entry)
        if not last_quality_state["quality_errors"]:
            break

    return current_bytes, {**last_quality_state, "stabilization_rounds": rounds}


def _generate_thumbnail_base_image(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    settings: WebtoonSettings,
    *,
    brief: dict[str, Any],
    sanitized_topic: str,
    prompt_plan: dict[str, Any] | None,
    reference_parts: list[tuple[bytes, str]],
    reference_files: list[Path],
    extra_feedback: str = "",
) -> dict[str, Any]:
    thumbnail_prompt = ""
    thumbnail_retry_feedback = extra_feedback.strip()
    thumbnail_image_text = ""
    thumbnail_base_png_bytes = b""
    thumbnail_character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_bg_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}
    max_generation_attempts = max(2, settings.max_correction_attempts + 1)
    best_candidate: dict[str, Any] | None = None

    for generation_attempt in range(1, max_generation_attempts + 1):
        thumbnail_prompt = str((prompt_plan or {}).get("thumbnail", {}).get("final_prompt", "")).strip()
        if not thumbnail_prompt:
            thumbnail_prompt = build_thumbnail_generation_prompt(
                brief["image_prompt"],
                title=brief["title"],
                subtitle=brief.get("thumbnail_subtitle", ""),
                topic=sanitized_topic,
                caption=brief["caption"],
                episode_scope=brief.get("episode_scope", ""),
                subtitle_scope=brief.get("subtitle_scope", ""),
                scope_summary=brief.get("scope_summary", ""),
                character_notes=brief.get("character_notes", ""),
                thumbnail_scene_prompt=brief.get("thumbnail_scene_prompt", ""),
            )
        if thumbnail_retry_feedback:
            thumbnail_prompt += (
                "\n\n재생성 보정 지시:\n"
                "- 아래 실패 사유를 반드시 반영해 처음부터 다시 생성한다.\n"
                f"{thumbnail_retry_feedback}"
            )
        thumbnail_image_bytes, thumbnail_mime_type, thumbnail_image_text = image_client.generate_image(
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
        )
        thumbnail_base_png_bytes = (
            thumbnail_image_bytes
            if thumbnail_mime_type == "image/png"
            else convert_image_bytes(thumbnail_image_bytes, "PNG")
        )
        quality_state = _run_generation_quality_checks(
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_parts=reference_parts,
            include_background_in_errors=False,
        )
        if quality_state["quality_errors"] and settings.max_correction_attempts > 0:
            thumbnail_base_png_bytes, quality_state = _stabilize_generated_image(
                image_client,
                ocr_client,
                image_bytes=thumbnail_base_png_bytes,
                scene_prompt=thumbnail_prompt,
                reference_parts=reference_parts,
                reference_files=reference_files,
                max_rounds=max(1, settings.max_correction_attempts),
                apply_background_correction=False,
            )
        thumbnail_character_check = quality_state["character_check"]
        thumbnail_reference_check = quality_state["reference_check"]
        thumbnail_bg_check = quality_state["background_check"]
        candidate_state = {
            "base_bytes": thumbnail_base_png_bytes,
            "prompt": thumbnail_prompt,
            "image_text": thumbnail_image_text,
            "character_check": thumbnail_character_check,
            "reference_check": thumbnail_reference_check,
            "background_check": thumbnail_bg_check,
        }
        if _is_better_candidate(best_candidate, candidate_state):
            best_candidate = candidate_state
        if not quality_state["quality_errors"]:
            break
        retry_guidance: list[str] = []
        if thumbnail_character_check.get("has_issues") and not thumbnail_character_check.get("kolla_size_gap_band_ok", True):
            retry_guidance.append(_size_ratio_guidance(thumbnail_character_check))
        thumbnail_retry_feedback = "\n".join(
            [*(f"- {message}" for message in quality_state["quality_errors"]), *(f"- {message}" for message in retry_guidance)]
        )
        logger.warning(
            "썸네일 재생성 필요 (시도 %d/%d): %s",
            generation_attempt,
            max_generation_attempts,
            " | ".join(quality_state["quality_errors"]),
        )
    else:
        logger.warning("썸네일 품질 게이트를 통과하지 못했지만 최선의 결과로 진행합니다: %s", thumbnail_retry_feedback)
        if best_candidate is not None:
            thumbnail_base_png_bytes = best_candidate["base_bytes"]
            thumbnail_prompt = best_candidate["prompt"]
            thumbnail_image_text = best_candidate["image_text"]
            thumbnail_character_check = best_candidate["character_check"]
            thumbnail_reference_check = best_candidate["reference_check"]
            thumbnail_bg_check = best_candidate["background_check"]

    return {
        "base_bytes": thumbnail_base_png_bytes,
        "prompt": thumbnail_prompt,
        "image_text": thumbnail_image_text,
        "character_check": thumbnail_character_check,
        "reference_check": thumbnail_reference_check,
        "background_check": thumbnail_bg_check,
    }


def _generate_panel_base_image(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    settings: WebtoonSettings,
    *,
    brief: dict[str, Any],
    panel: dict[str, Any],
    prompt_plan: dict[str, Any] | None,
    reference_parts: list[tuple[bytes, str]],
    reference_files: list[Path],
    previous_panel: dict[str, Any] | None = None,
    anchor_reference_descriptions: list[str] | None = None,
    extra_feedback: str = "",
) -> dict[str, Any]:
    panel_prompt = ""
    panel_retry_feedback = extra_feedback.strip()
    panel_image_text = ""
    panel_base_png_bytes = b""
    panel_character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    panel_reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    panel_bg_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}
    max_generation_attempts = max(2, settings.max_correction_attempts + 1)
    best_candidate: dict[str, Any] | None = None

    for generation_attempt in range(1, max_generation_attempts + 1):
        panel_prompt = str(_lookup_panel_prompt_plan(prompt_plan or {}, int(panel.get("panel_no", 0) or 0)).get("final_prompt", "")).strip()
        if not panel_prompt:
            panel_prompt = build_panel_generation_prompt(
                brief["image_prompt"],
                panel,
                brief["title"],
                brief.get("thumbnail_subtitle", ""),
                brief["caption"],
                brief.get("episode_scope", ""),
                brief.get("subtitle_scope", ""),
                brief.get("scope_summary", ""),
                brief.get("character_notes", ""),
                panel.get("dialogue_lines", []),
                previous_panel=previous_panel,
            )
        if anchor_reference_descriptions:
            anchor_block = "\n".join(f"- {line}" for line in anchor_reference_descriptions if str(line).strip())
            if anchor_block:
                panel_prompt += (
                    "\n\n승인된 이전 컷 앵커 참조:\n"
                    "- 첨부된 마스터 참조 이미지 뒤에는 승인된 썸네일/이전 패널 앵커가 순서대로 포함된다.\n"
                    "- 아래 앵커는 배경 복사용이 아니라 등장인물 외형, 사족보행 방지, 상대 크기, 소품 상태, 손/발 주체를 이어받기 위한 기준이다.\n"
                    f"{anchor_block}\n"
                )
        if panel_retry_feedback:
            panel_prompt += (
                "\n\n재생성 보정 지시:\n"
                "- 아래 실패 사유를 반드시 반영해 처음부터 다시 생성한다.\n"
                f"{panel_retry_feedback}"
            )
        panel_image_bytes, panel_mime_type, panel_image_text = image_client.generate_image(
            panel_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
        )
        panel_base_png_bytes = panel_image_bytes if panel_mime_type == "image/png" else convert_image_bytes(panel_image_bytes, "PNG")
        quality_state = _run_generation_quality_checks(
            ocr_client,
            panel_base_png_bytes,
            _build_panel_scene_context(panel, previous_panel),
            reference_parts=reference_parts,
            include_background_in_errors=False,
        )
        if quality_state["quality_errors"] and settings.max_correction_attempts > 0:
            panel_base_png_bytes, quality_state = _stabilize_generated_image(
                image_client,
                ocr_client,
                image_bytes=panel_base_png_bytes,
                scene_prompt=_build_panel_scene_context(panel, previous_panel),
                reference_parts=reference_parts,
                reference_files=reference_files,
                max_rounds=max(1, settings.max_correction_attempts),
                apply_background_correction=False,
            )
        panel_character_check = quality_state["character_check"]
        panel_reference_check = quality_state["reference_check"]
        panel_bg_check = quality_state["background_check"]
        candidate_state = {
            "base_bytes": panel_base_png_bytes,
            "prompt": panel_prompt,
            "image_text": panel_image_text,
            "character_check": panel_character_check,
            "reference_check": panel_reference_check,
            "background_check": panel_bg_check,
        }
        if _is_better_candidate(best_candidate, candidate_state):
            best_candidate = candidate_state
        if not quality_state["quality_errors"]:
            break
        retry_guidance: list[str] = []
        if panel_character_check.get("has_issues") and not panel_character_check.get("kolla_size_gap_band_ok", True):
            retry_guidance.append(_size_ratio_guidance(panel_character_check))
        panel_retry_feedback = "\n".join(
            [*(f"- {message}" for message in quality_state["quality_errors"]), *(f"- {message}" for message in retry_guidance)]
        )
        logger.warning(
            "패널 %s 재생성 필요 (시도 %d/%d): %s",
            panel.get("panel_no"),
            generation_attempt,
            max_generation_attempts,
            " | ".join(quality_state["quality_errors"]),
        )
    else:
        logger.warning("패널 %s 품질 게이트를 통과하지 못했지만 최선의 결과로 진행합니다: %s", panel.get("panel_no"), panel_retry_feedback)
        if best_candidate is not None:
            panel_base_png_bytes = best_candidate["base_bytes"]
            panel_prompt = best_candidate["prompt"]
            panel_image_text = best_candidate["image_text"]
            panel_character_check = best_candidate["character_check"]
            panel_reference_check = best_candidate["reference_check"]
            panel_bg_check = best_candidate["background_check"]

    return {
        "base_bytes": panel_base_png_bytes,
        "prompt": panel_prompt,
        "image_text": panel_image_text,
        "character_check": panel_character_check,
        "reference_check": panel_reference_check,
        "background_check": panel_bg_check,
    }


def _generate_panels_progressively(
    image_client: GeminiImageClient,
    ocr_client: GeminiOcrClient,
    settings: WebtoonSettings,
    *,
    brief: dict[str, Any],
    current_panels: list[dict[str, Any]],
    prompt_plan: dict[str, Any] | None,
    master_reference_parts: list[tuple[bytes, str]],
    reference_files: list[Path],
    approved_anchor_images: list[dict[str, Any]],
    artifact_paths: ArtifactPaths,
    panel_feedback_map: dict[int, list[str]] | None = None,
    existing_panel_base_items: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    panel_base_items: list[dict[str, Any]] = list(existing_panel_base_items or [])
    panel_generation_notes: list[dict[str, Any]] = []
    mutable_anchors = list(approved_anchor_images)
    feedback_map = panel_feedback_map or {}

    for panel_index, panel in enumerate(current_panels):
        panel_no = int(panel.get("panel_no", 0) or 0)
        should_generate = not existing_panel_base_items or panel_no in feedback_map
        if not should_generate:
            existing_item = panel_base_items[panel_no - 1]
            mutable_anchors.append(
                {
                    "slide_type": "panel",
                    "panel_no": panel_no,
                    "image_bytes": existing_item["base_bytes"],
                    "mime_type": "image/png",
                    "scene_prompt": panel.get("scene_prompt", ""),
                    "location": panel.get("location", ""),
                }
            )
            panel_generation_notes.append(
                {
                    "slide_type": "panel",
                    "panel_no": panel_no,
                    "scene_prompt": panel.get("scene_prompt", ""),
                    "skipped": "reused_existing_panel_base",
                }
            )
            continue
        _mark_pipeline_stage(
            artifact_paths,
            "panel_generation_started",
            topic=brief.get("title", ""),
            notes=f"panel_no={panel_no}",
            extra={"panel_no": panel_no, "location": panel.get("location", "")},
        )
        combined_reference_parts, anchor_descriptions = _build_progressive_anchor_bundle(
            master_reference_parts,
            mutable_anchors,
        )
        panel_generation = _generate_panel_base_image(
            image_client,
            ocr_client,
            settings,
            brief=brief,
            panel=panel,
            prompt_plan=prompt_plan,
            reference_parts=combined_reference_parts,
            reference_files=reference_files,
            previous_panel=current_panels[panel_index - 1] if panel_index > 0 else None,
            anchor_reference_descriptions=anchor_descriptions,
            extra_feedback="\n".join(f"- 최종 하드 블로커: {item}" for item in feedback_map.get(panel_no, [])),
        )
        panel_base_path = artifact_paths.run_dir / f"panel_{panel_no:02d}_base_v1.png"
        save_image(panel_generation["base_bytes"], panel_base_path)
        panel_item = {
            "panel_no": panel_no,
            "base_path": panel_base_path,
            "base_bytes": panel_generation["base_bytes"],
        }
        panel_note = {
            "slide_type": "panel",
            "panel_no": panel_no,
            "scene_prompt": panel.get("scene_prompt", ""),
            "image_notes": panel_generation["image_text"],
            "file": panel_base_path.name,
            "character_composition_check": panel_generation["character_check"],
            "character_reference_check": panel_generation["reference_check"],
            "background_text_check": panel_generation["background_check"],
        }
        if len(panel_base_items) >= panel_no:
            panel_base_items[panel_no - 1] = panel_item
        else:
            panel_base_items.append(panel_item)
        panel_generation_notes.append(panel_note)
        mutable_anchors.append(
            {
                "slide_type": "panel",
                "panel_no": panel_no,
                "image_bytes": panel_generation["base_bytes"],
                "mime_type": "image/png",
                "scene_prompt": panel.get("scene_prompt", ""),
                "location": panel.get("location", ""),
            }
        )
        _mark_pipeline_stage(
            artifact_paths,
            "panel_generation_completed",
            topic=brief.get("title", ""),
            notes=f"panel_no={panel_no}",
            extra={"panel_no": panel_no, "file": panel_base_path.name},
        )

    return panel_base_items, panel_generation_notes, mutable_anchors


def _expand_panel_ranges(text: str, *, max_panel_no: int) -> set[int]:
    panel_numbers: set[int] = set()
    for start_text, end_text in re.findall(r"패널\s*(\d+)\s*(?:부터|~|-)\s*(\d+)", text):
        start = int(start_text)
        end = int(end_text)
        lo, hi = sorted((start, end))
        panel_numbers.update(range(max(1, lo), min(max_panel_no, hi) + 1))
    for single_text in re.findall(r"패널\s*(\d+)", text):
        panel_no = int(single_text)
        if 1 <= panel_no <= max_panel_no:
            panel_numbers.add(panel_no)
    return panel_numbers


def _collect_targeted_replan_feedback(
    final_package_review: dict[str, Any],
    *,
    panel_count: int,
) -> dict[str, Any]:
    hard_blockers = [str(item).strip() for item in final_package_review.get("hard_blockers", []) if str(item).strip()]
    soft_scores = final_package_review.get("soft_scores", {}) if isinstance(final_package_review.get("soft_scores", {}), dict) else {}
    summary = str(final_package_review.get("summary", "")).strip()
    panel_feedback: dict[int, list[str]] = {}
    thumbnail_feedback: list[str] = []
    global_panel_feedback: list[str] = []

    for blocker in hard_blockers:
        matched_panels = _expand_panel_ranges(blocker, max_panel_no=panel_count)
        if "썸네일" in blocker or "thumbnail" in blocker.lower():
            thumbnail_feedback.append(blocker)
        if matched_panels:
            for panel_no in matched_panels:
                panel_feedback.setdefault(panel_no, []).append(blocker)
            continue
        if any(
            keyword in blocker
            for keyword in (
                "배경 재사용",
                "장면과 프롬프트",
                "여정",
                "캐릭터 상대 크기 비율 붕괴",
                "모든 패널",
                "throughout all panels",
                "recurs throughout all panels",
                "Incorrect Zero Eye Color",
                "Incorrect Kolla Eye Color",
                "사족보행",
                "quadruped",
                "biped",
                "extra limb",
                "missing limb",
                "duplicated arms",
                "duplicated paws",
                "extra hands",
                "해부학",
                "팔다리",
                "여분의 팔",
                "여분 팔",
                "참조 이미지",
                "reference",
                "body proportions",
                "tail silhouette",
                "size ratio",
            )
        ):
            global_panel_feedback.append(blocker)

    if not panel_feedback and (
        soft_scores.get("story_flow", 1.0) < MIN_SOFT_SCORE
        or soft_scores.get("background_progression", 1.0) < MIN_SOFT_SCORE
    ):
        global_panel_feedback.append(summary or "최종 검수에서 이야기 흐름 또는 배경 진행 점수가 낮았습니다.")
    if soft_scores.get("ending_resolution", 1.0) < MIN_SOFT_SCORE:
        panel_feedback.setdefault(panel_count, []).append(summary or "최종 검수에서 마지막 컷 해소감 점수가 낮았습니다.")

    # Issues caused by model limitations cannot be fixed by regeneration — retrying
    # only wastes time and API cost.  Filter these from both global and per-panel
    # feedback so they do not trigger selective regeneration.
    _NON_FIXABLE_KEYWORDS = (
        # size ratio — model cannot control relative character scale
        "캐릭터 상대 크기 비율 붕괴",
        "상대 크기 비율",
        "size ratio",
        "size gap",
        # eye colour — model frequently ignores colour constraints
        "Incorrect Zero Eye Color",
        "Incorrect Kolla Eye Color",
        "eye color",
        "eye colour",
        "눈 색상",
        "눈 색깔",
        # background text spelling — model cannot spell foreign words correctly
        "배경 텍스트 품질 게이트 실패",
        "background text",
        # anatomy / posture — model limitation on bipedal posture and limb count
        "사족보행",
        "quadruped",
        "biped",
        "extra limb",
        "missing limb",
        "duplicated arms",
        "duplicated paws",
        "extra hands",
        "해부학",
        "팔다리",
        "여분의 팔",
        "여분 팔",
        "body proportions",
        "tail silhouette",
    )

    def _is_non_fixable(item: str) -> bool:
        return any(kw in item for kw in _NON_FIXABLE_KEYWORDS)

    fixable_global_feedback = [item for item in global_panel_feedback if not _is_non_fixable(item)]
    if fixable_global_feedback:
        for panel_no in range(1, panel_count + 1):
            panel_feedback.setdefault(panel_no, []).extend(fixable_global_feedback)

    # Also filter per-panel feedback for non-fixable issues
    filtered_panel_feedback = {
        panel_no: [item for item in items if not _is_non_fixable(item)]
        for panel_no, items in panel_feedback.items()
    }

    return {
        "thumbnail_feedback": [item for item in thumbnail_feedback if not _is_non_fixable(item)],
        "panel_feedback": {panel_no: items for panel_no, items in filtered_panel_feedback.items() if items},
    }

def _raise_if_quality_gate_failed(stage: str, check: dict[str, Any]) -> None:
    if not check.get("has_issues"):
        return
    issue_text = ", ".join(str(item) for item in check.get("issues", []) if str(item).strip())
    detail = issue_text or str(check.get("edit_instruction", "")).strip() or json.dumps(check, ensure_ascii=False)
    raise RuntimeError(f"{stage} 품질 게이트 실패: {detail}")


def _quality_gate_error_message(stage: str, check: dict[str, Any]) -> str:
    if not check.get("has_issues"):
        return ""
    issue_text = ", ".join(str(item) for item in check.get("issues", []) if str(item).strip())
    detail = issue_text or str(check.get("edit_instruction", "")).strip() or json.dumps(check, ensure_ascii=False)
    return f"{stage} 품질 게이트 실패: {detail}"


def _background_gate_error_message(stage: str, check: dict[str, Any]) -> str:
    if not check.get("has_errors"):
        return ""
    corrections = check.get("corrections", [])
    detail = ", ".join(
        f"{item.get('found', '')}->{item.get('correct', '')}"
        for item in corrections
        if str(item.get("found", "")).strip() and str(item.get("correct", "")).strip()
    )
    if not detail:
        detail = json.dumps(check, ensure_ascii=False)
    return f"{stage} 배경 텍스트 품질 게이트 실패: {detail}"


def _size_ratio_guidance(check: dict[str, Any]) -> str:
    try:
        estimated_gap = float(check.get("estimated_size_gap_percent", 12.0) or 12.0)
    except (TypeError, ValueError):
        estimated_gap = 12.0
    if estimated_gap > 18.0:
        return (
            f"현재 콜라가 제로보다 약 {estimated_gap:.0f}% 더 크게 보입니다. "
            "콜라를 줄이고 제로를 키워서 머리, 몸통, 전신 실루엣 기준 격차를 12% 전후(허용 8~15%)로 맞추세요."
        )
    if estimated_gap < 8.0:
        return (
            f"현재 콜라가 제로보다 약 {estimated_gap:.0f}%만 더 크게 보입니다. "
            "콜라를 약간만 키우거나 제로를 약간 뒤로 보내서 격차를 12% 전후(허용 8~15%)로 맞추세요."
        )
    return "콜라와 제로의 머리, 어깨, 몸통, 전신 실루엣 차이를 12% 전후로 유지하세요."


_BACKGROUND_DISPLAY_HINTS = (
    "display",
    "screen",
    "flight",
    "departure",
    "departures",
    "arrival",
    "arrivals",
    "board",
    "gate",
    "platform",
)
_BACKGROUND_DOCUMENT_HINTS = ("passport", "reisepass", "document", "form", "ticket", "name", "geburt", "holder")
_BACKGROUND_STAMP_HINTS = ("stamp", "stempel", "einreise", "seal")
_BACKGROUND_UI_HINTS = ("ui", "icon", "button", "menu", "placeholder", "bar")


def _matches_background_hint(haystack: str, hints: tuple[str, ...]) -> bool:
    normalized = f" {re.sub(r'[^a-z0-9]+', ' ', haystack.lower()).strip()} "
    return any(f" {hint} " in normalized for hint in hints)


def _simplify_background_correction(found: str, correct: str, reason: str = "") -> str:
    concise = str(correct).strip()
    haystack = " ".join(str(item).lower() for item in (found, correct, reason))
    lower_correct = concise.lower()
    if concise and len(concise) <= 48 and not any(
        token in lower_correct
        for token in ("placeholder", "unreadable", "legible", "should", "could", "bars", "icons")
    ):
        return concise
    if _matches_background_hint(haystack, _BACKGROUND_STAMP_HINTS):
        return "Use one short stamp label and one short date line only."
    if _matches_background_hint(haystack, _BACKGROUND_DOCUMENT_HINTS):
        return "Use one to three large document labels only; keep the rest blank."
    if _matches_background_hint(haystack, _BACKGROUND_DISPLAY_HINTS):
        return "Use one short header and up to three short rows only; no microtext."
    if _matches_background_hint(haystack, _BACKGROUND_UI_HINTS):
        return "Use icons, blank bars, or one short label only."
    return "Use one short readable label only; remove unreadable microtext."


def _check_requires_manual_review(check: dict[str, Any]) -> bool:
    return any(
        key in check
        for key in (
            "skipped",
            "edit_error",
            "review_unavailable",
        )
    )


def _collect_ocr_review_flags(panel_reviews: list[dict[str, Any]]) -> tuple[list[str], list[str], float]:
    hard_blockers: list[str] = []
    manual_review_reasons: list[str] = []
    scores: list[float] = []

    for entry in panel_reviews:
        panel_no = int(entry.get("panel_no", 0) or 0)
        review = entry.get("review", {})
        if review.get("rerender_required"):
            issues = ", ".join(str(item) for item in review.get("issues", []) if str(item).strip())
            hard_blockers.append(f"패널 {panel_no} 말풍선 텍스트 검수 미해결: {issues or 'OCR 재렌더 필요'}")
            scores.append(0.0)
            continue
        if review.get("skipped"):
            manual_review_reasons.append(f"패널 {panel_no} OCR 검수가 건너뛰어졌습니다: {review['skipped']}")
        confidence = float(review.get("confidence", 1.0) or 0.0)
        scores.append(max(0.0, min(1.0, confidence)))

    text_integrity_score = round(sum(scores) / len(scores), 3) if scores else 1.0
    return hard_blockers, manual_review_reasons, text_integrity_score


def _summarize_stage_gate_findings(
    *,
    thumbnail_checks: dict[str, dict[str, Any]],
    panel_generation_notes: list[dict[str, Any]],
) -> list[str]:
    findings: list[str] = []
    for label, check in (
        ("썸네일 캐릭터 구성", thumbnail_checks.get("character", {})),
        ("썸네일 캐릭터 참조 일관성", thumbnail_checks.get("reference", {})),
        ("썸네일-본문 구분", thumbnail_checks.get("distinction", {})),
    ):
        message = _quality_gate_error_message(label, check)
        if message:
            findings.append(message)

    thumbnail_background_message = _background_gate_error_message("썸네일", thumbnail_checks.get("background", {}))
    if thumbnail_background_message:
        findings.append(thumbnail_background_message)

    for note in panel_generation_notes:
        if note.get("slide_type") != "panel":
            continue
        panel_no = int(note.get("panel_no", 0) or 0)
        for label, check in (
            (f"패널 {panel_no} 캐릭터 구성", note.get("character_composition_check", {})),
            (f"패널 {panel_no} 캐릭터 참조 일관성", note.get("character_reference_check", {})),
        ):
            message = _quality_gate_error_message(label, check)
            if message:
                findings.append(message)
        background_message = _background_gate_error_message(
            f"패널 {panel_no}",
            note.get("background_text_check", {}),
        )
        if background_message:
            findings.append(background_message)
    return findings[:20]


def _build_publish_decision(
    *,
    publish_requested: bool,
    brief: dict[str, Any],
    thumbnail_checks: dict[str, dict[str, Any]],
    panel_generation_notes: list[dict[str, Any]],
    panel_reviews: list[dict[str, Any]],
    bubble_review: dict[str, Any],
    final_package_review: dict[str, Any],
) -> dict[str, Any]:
    hard_blockers: list[str] = []
    manual_review_reasons: list[str] = []

    thumbnail_character_check = thumbnail_checks.get("character", {})
    thumbnail_reference_check = thumbnail_checks.get("reference", {})
    thumbnail_background_check = thumbnail_checks.get("background", {})
    thumbnail_distinction_check = thumbnail_checks.get("distinction", {})

    for label, check in (
        ("썸네일 캐릭터 구성", thumbnail_character_check),
        ("썸네일 캐릭터 참조 일관성", thumbnail_reference_check),
        ("썸네일-본문 구분", thumbnail_distinction_check),
    ):
        message = _quality_gate_error_message(label, check)
        if message:
            hard_blockers.append(message)
        if _check_requires_manual_review(check):
            manual_review_reasons.append(f"{label} 검사 결과를 수동 확인해야 합니다.")

    # Background text spelling is a known model limitation — downgrade from hard
    # blocker to manual review so it does not trigger selective regeneration.
    background_message = _background_gate_error_message("썸네일", thumbnail_background_check)
    if background_message:
        manual_review_reasons.append(background_message)

    for note in panel_generation_notes:
        if note.get("slide_type") != "panel":
            continue
        panel_no = int(note.get("panel_no", 0) or 0)
        for label, check in (
            (f"패널 {panel_no} 캐릭터 구성", note.get("character_composition_check", {})),
            (f"패널 {panel_no} 캐릭터 참조 일관성", note.get("character_reference_check", {})),
        ):
            message = _quality_gate_error_message(label, check)
            if message:
                hard_blockers.append(message)
            if _check_requires_manual_review(check):
                manual_review_reasons.append(f"{label} 검사 결과를 수동 확인해야 합니다.")
        background_check = note.get("background_text_check", {})
        background_msg = _background_gate_error_message(f"패널 {panel_no}", background_check)
        if background_msg:
            manual_review_reasons.append(background_msg)

    ocr_hard_blockers, ocr_manual_review, text_integrity_score = _collect_ocr_review_flags(panel_reviews)
    hard_blockers.extend(ocr_hard_blockers)
    manual_review_reasons.extend(ocr_manual_review)

    if bubble_review.get("has_issues"):
        hard_blockers.extend(str(item).strip() for item in bubble_review.get("issues", []) if str(item).strip())

    if final_package_review.get("review_unavailable"):
        manual_review_reasons.append(f"최종 패키지 검수가 완료되지 않았습니다: {final_package_review['review_unavailable']}")
    else:
        hard_blockers.extend(
            f"최종 패키지 검수: {item}"
            for item in final_package_review.get("hard_blockers", [])
            if str(item).strip()
        )
        if hard_blockers and not final_package_review.get("hard_blockers"):
            final_notes = [str(item).strip() for item in final_package_review.get("notes", []) if str(item).strip()]
            final_notes.append("상위 stage gate 미해결 이슈가 남아 있어 최종 패키지 검수 요약과 게시 판정은 stage gate 기준을 우선합니다.")
            final_package_review["notes"] = final_notes
            final_summary = str(final_package_review.get("summary", "")).strip()
            if final_summary:
                final_package_review["summary"] = (
                    f"{final_summary} 단, 상위 stage gate에서 미해결 하드 블로커가 남아 게시는 차단되었습니다."
                )
            else:
                final_package_review["summary"] = "상위 stage gate에서 미해결 하드 블로커가 남아 게시는 차단되었습니다."

    story_review = brief.get("story_review", {})
    story_plan_score = 1.0 if not story_review.get("has_issues") else 0.55
    if story_review.get("has_issues"):
        manual_review_reasons.append("스토리 기획 검수에서 재검토 이슈가 남아 있습니다.")

    soft_scores = dict(final_package_review.get("soft_scores", {}))
    soft_scores["bubble_layout"] = float(bubble_review.get("soft_score", 1.0) or 0.0)
    soft_scores["text_integrity"] = text_integrity_score
    soft_scores["story_plan"] = story_plan_score
    normalized_soft_scores = {
        key: round(max(0.0, min(1.0, float(value))), 3)
        for key, value in soft_scores.items()
    }
    average_soft_score = round(
        sum(normalized_soft_scores.values()) / len(normalized_soft_scores),
        3,
    ) if normalized_soft_scores else 0.0

    if hard_blockers:
        quality_decision = "blocked"
    else:
        low_soft_scores = [
            f"{key}={value:.3f}"
            for key, value in normalized_soft_scores.items()
            if value < MIN_SOFT_SCORE
        ]
        if final_package_review.get("review_unavailable"):
            low_soft_scores.append("final_package_review=unavailable")
        if low_soft_scores or average_soft_score < MIN_AVERAGE_SOFT_SCORE or manual_review_reasons:
            quality_decision = "manual_review"
            if average_soft_score < MIN_AVERAGE_SOFT_SCORE:
                manual_review_reasons.append(
                    f"소프트 점수 평균이 기준 미만입니다 ({average_soft_score:.3f} < {MIN_AVERAGE_SOFT_SCORE:.2f})."
                )
            for item in low_soft_scores:
                manual_review_reasons.append(f"소프트 점수 재검토 필요: {item}")
        else:
            quality_decision = "allow"

    publish_decision = "skip" if not publish_requested else quality_decision
    return {
        "quality_decision": quality_decision,
        "publish_decision": publish_decision,
        "hard_blockers": hard_blockers,
        "manual_review_reasons": manual_review_reasons,
        "soft_scores": normalized_soft_scores,
        "average_soft_score": average_soft_score,
        "thresholds": {
            "min_soft_score": MIN_SOFT_SCORE,
            "min_average_soft_score": MIN_AVERAGE_SOFT_SCORE,
        },
        "bubble_review": bubble_review,
        "final_package_review": final_package_review,
    }

def _quality_issue_count(check: dict[str, Any]) -> int:
    return len([str(item).strip() for item in check.get("issues", []) if str(item).strip()])


def _background_issue_count(check: dict[str, Any]) -> int:
    return len(
        [
            item
            for item in check.get("corrections", [])
            if str(item.get("found", "")).strip() or str(item.get("correct", "")).strip()
        ]
    )


def _candidate_quality_errors(
    *,
    stage_label: str,
    character_check: dict[str, Any],
    reference_check: dict[str, Any],
    background_check: dict[str, Any],
) -> list[str]:
    errors = [
        message
        for message in (
            _quality_gate_error_message(f"{stage_label} 캐릭터 구성", character_check),
            _quality_gate_error_message(f"{stage_label} 캐릭터 참조 일관성", reference_check),
            _background_gate_error_message(stage_label, background_check),
        )
        if message
    ]
    return errors


def _candidate_quality_score(
    *,
    character_check: dict[str, Any],
    reference_check: dict[str, Any],
    background_check: dict[str, Any],
) -> tuple[int, int, int]:
    return (
        _quality_issue_count(character_check) + int(character_check.get("has_issues", False)),
        _quality_issue_count(reference_check) + int(reference_check.get("has_issues", False)),
        _background_issue_count(background_check) + int(background_check.get("has_errors", False)),
    )


def _is_better_candidate(
    current: dict[str, Any] | None,
    challenger: dict[str, Any],
) -> bool:
    if current is None:
        return True
    current_score = _candidate_quality_score(
        character_check=current["character_check"],
        reference_check=current["reference_check"],
        background_check=current["background_check"],
    )
    challenger_score = _candidate_quality_score(
        character_check=challenger["character_check"],
        reference_check=challenger["reference_check"],
        background_check=challenger["background_check"],
    )
    return challenger_score < current_score


def _parse_panel_range_targets(message: str) -> set[int]:
    panel_numbers: set[int] = set()
    for start, end in re.findall(r"패널\s*(\d+)\s*(?:부터|~|-)\s*(\d+)", message):
        start_no = int(start)
        end_no = int(end)
        lower = min(start_no, end_no)
        upper = max(start_no, end_no)
        panel_numbers.update(range(lower, upper + 1))
    for match in re.findall(r"패널\s*(\d+)", message):
        panel_numbers.add(int(match))
    return {panel_no for panel_no in panel_numbers if 1 <= panel_no <= MAX_WEBTOON_PANEL_COUNT}


def _extract_replan_targets(hard_blockers: list[str]) -> dict[str, Any]:
    rewrite_thumbnail = False
    panel_numbers: set[int] = set()
    full_episode_replan = False

    for item in hard_blockers:
        message = str(item).strip()
        if not message:
            continue
        if "썸네일" in message:
            rewrite_thumbnail = True
        panel_numbers.update(_parse_panel_range_targets(message))
        if any(
            token in message
            for token in (
                "최종 패키지 검수",
                "에피소드 범위",
                "전체 여정",
                "전개",
                "story_flow",
                "background_progression",
                "scope_alignment",
            )
        ):
            full_episode_replan = True

    if not rewrite_thumbnail and not panel_numbers:
        full_episode_replan = True
    return {
        "rewrite_thumbnail": rewrite_thumbnail,
        "panel_numbers": sorted(panel_numbers),
        "full_episode_replan": full_episode_replan,
    }


def run_webtoon_pipeline(
    settings: WebtoonSettings,
    *,
    topic: str,
    notes: str = "",
) -> dict[str, Any]:
    week_key, run_id = build_run_identity()
    artifact_paths = create_artifact_paths(run_id, week_key)
    cleanup_required = True
    _mark_pipeline_stage(artifact_paths, "initialized", topic=topic, notes=notes)
    try:
        result = _execute_webtoon_pipeline(
            settings,
            topic=topic,
            notes=notes,
            week_key=week_key,
            run_id=run_id,
            artifact_paths=artifact_paths,
        )
        _write_json_artifact(artifact_paths.publish_result_path, result)
        _mark_pipeline_stage(
            artifact_paths,
            "completed",
            topic=topic,
            notes=notes,
            extra={"status": result.get("status", ""), "drive_uploaded": True},
        )
        return result
    except Exception as exc:
        cleanup_required = False
        _write_pipeline_failure(artifact_paths, topic=topic, notes=notes, exc=exc)
        logger.exception(
            "웹툰 파이프라인 실행 실패 (run_id=%s, run_dir=%s, topic=%s)",
            run_id,
            artifact_paths.run_dir,
            topic,
        )
        raise
    finally:
        if cleanup_required:
            shutil.rmtree(artifact_paths.run_dir, ignore_errors=True)


def _execute_webtoon_pipeline(
    settings: WebtoonSettings,
    *,
    topic: str,
    notes: str,
    week_key: str,
    run_id: str,
    artifact_paths: PipelineArtifacts,
) -> dict[str, Any]:
    # ── Phase 1: Client initialization ──
    _mark_pipeline_stage(artifact_paths, "clients_initializing", topic=topic, notes=notes)
    llm_client = GeminiTextClient(settings)
    image_client = GeminiImageClient(settings)
    ocr_client = GeminiOcrClient(settings)
    _mark_pipeline_stage(artifact_paths, "clients_ready", topic=topic, notes=notes)

    # ── Phase 2: Creative brief with visual direction ──
    brief = llm_client.build_creative_brief(topic)
    _mark_pipeline_stage(
        artifact_paths,
        "creative_brief_ready",
        topic=topic,
        notes=notes,
        extra={"title": brief.get("title", ""), "panel_count": len(brief.get("panels", []))},
    )
    sanitized_topic = sanitize_public_text(topic, fallback=brief["title"])

    # ── Phase 3: Build compact prompts from visual direction ──
    global_contract = _build_compact_global_contract(brief)
    _mark_pipeline_stage(
        artifact_paths,
        "prompt_plan_ready",
        topic=topic,
        notes=notes,
        extra={"prompt_mode": "compact_visual_direction"},
    )

    # ── Phase 4: Character reference setup ──
    reference_files = list_character_reference_files(settings)
    reference_parts = _build_reference_image_parts(reference_files)
    _mark_pipeline_stage(
        artifact_paths,
        "references_ready",
        topic=topic,
        notes=notes,
        extra={"reference_file_count": len(reference_files)},
    )

    # ── Phase 5: Costume reference sheet generation ──
    _mark_pipeline_stage(artifact_paths, "costume_sheet_started", topic=topic, notes=notes)
    costume_prompt = build_costume_sheet_prompt(brief)
    logger.info("의상 시트 프롬프트 길이: %d자", len(costume_prompt))
    costume_bytes, costume_mime, _costume_text = image_client.generate_image(
        costume_prompt,
        reference_images=reference_parts,
        reference_image_paths=reference_files,
    )
    costume_png_bytes = costume_bytes if costume_mime == "image/png" else convert_image_bytes(costume_bytes, "PNG")
    costume_sheet_path = build_versioned_path(artifact_paths.run_dir, "costume_sheet", 1, "png")
    save_image(costume_png_bytes, costume_sheet_path)
    # Costume sheet becomes the PRIMARY visual reference for all subsequent images.
    # Original bare-fur references are dropped — they conflict with outfit instructions.
    costume_ref: list[tuple[bytes, str]] = [(costume_png_bytes, "image/png")]
    _mark_pipeline_stage(
        artifact_paths,
        "costume_sheet_ready",
        topic=topic,
        notes=notes,
        extra={"costume_sheet_file": costume_sheet_path.name},
    )

    # ── Phase 6: Single-pass thumbnail generation ──
    _mark_pipeline_stage(artifact_paths, "thumbnail_generation_started", topic=topic, notes=notes)
    thumbnail_prompt = build_compact_thumbnail_prompt(brief, global_contract=global_contract)
    logger.info("썸네일 프롬프트 길이: %d자", len(thumbnail_prompt))
    thumbnail_image_bytes, thumbnail_mime, thumbnail_image_text = image_client.generate_image(
        thumbnail_prompt,
        reference_images=costume_ref,
        reference_image_paths=reference_files,
    )
    thumbnail_base_png_bytes = (
        thumbnail_image_bytes if thumbnail_mime == "image/png" else convert_image_bytes(thumbnail_image_bytes, "PNG")
    )
    thumbnail_base_path = build_versioned_path(artifact_paths.run_dir, "thumbnail_base", 1, "png")
    save_image(thumbnail_base_png_bytes, thumbnail_base_path)
    thumbnail_render = render_thumbnail_card(
        thumbnail_base_png_bytes,
        title=brief["title"],
        subtitle=brief.get("thumbnail_subtitle", ""),
        topic=sanitized_topic,
        settings=settings,
    )
    thumbnail_final_png_bytes = thumbnail_render["image_bytes"]
    thumbnail_publish_jpg_bytes = convert_image_bytes(thumbnail_final_png_bytes, "JPEG")
    thumbnail_final_path = build_versioned_path(artifact_paths.run_dir, "thumbnail_final", 1, "png")
    thumbnail_publish_path = build_versioned_path(artifact_paths.run_dir, "thumbnail_publish", 1, "jpg")
    save_image(thumbnail_final_png_bytes, thumbnail_final_path)
    save_image(thumbnail_publish_jpg_bytes, thumbnail_publish_path)
    _mark_pipeline_stage(
        artifact_paths,
        "thumbnail_ready",
        topic=topic,
        notes=notes,
        extra={"thumbnail_base_file": thumbnail_base_path.name, "thumbnail_final_file": thumbnail_final_path.name},
    )

    # ── Phase 7: Single-pass panel generation (sequential with anchor) ──
    current_panels = _materialize_panels_from_brief(brief)
    panel_base_items: list[dict[str, Any]] = []
    anchor_images: list[tuple[bytes, str]] = [(thumbnail_base_png_bytes, "image/png")]
    for panel in current_panels:
        panel_no = int(panel.get("panel_no", len(panel_base_items) + 1))
        panel_prompt = build_compact_panel_prompt(brief, panel, global_contract=global_contract)
        logger.info("패널 %d 프롬프트 길이: %d자", panel_no, len(panel_prompt))
        # Costume sheet (primary) + latest anchor (for scene continuity)
        combined_refs = costume_ref + anchor_images[-1:]
        panel_image_bytes, panel_mime, panel_image_text = image_client.generate_image(
            panel_prompt,
            reference_images=combined_refs,
            reference_image_paths=reference_files,
        )
        panel_base_png = (
            panel_image_bytes if panel_mime == "image/png" else convert_image_bytes(panel_image_bytes, "PNG")
        )
        panel_base_path = build_versioned_path(artifact_paths.run_dir, f"panel_{panel_no:02d}_base", 1, "png")
        save_image(panel_base_png, panel_base_path)
        panel_base_items.append({"panel_no": panel_no, "base_path": panel_base_path, "base_bytes": panel_base_png})
        anchor_images.append((panel_base_png, "image/png"))
    _mark_pipeline_stage(
        artifact_paths,
        "panel_bases_ready",
        topic=topic,
        notes=notes,
        extra={"panel_count": len(panel_base_items)},
    )

    # ── Phase 8: Materialize slide outputs ──
    image_version = 1
    slide_outputs: list[dict[str, Any]] = [
        {
            "slide_index": 1,
            "slide_type": "thumbnail",
            "png_bytes": thumbnail_final_png_bytes,
            "jpg_bytes": thumbnail_publish_jpg_bytes,
            "final_path": thumbnail_final_path,
            "publish_path": thumbnail_publish_path,
            "layout": thumbnail_render["layout"],
        }
    ]
    for panel, panel_base in zip(current_panels, panel_base_items, strict=True):
        panel_no = int(panel["panel_no"])
        panel_final_png_bytes = panel_base["base_bytes"]
        panel_publish_jpg_bytes = convert_image_bytes(panel_final_png_bytes, "JPEG")
        panel_final_path = build_versioned_path(
            artifact_paths.run_dir, f"panel_{panel_no:02d}_final", image_version, "png"
        )
        panel_publish_path = build_versioned_path(
            artifact_paths.run_dir, f"panel_{panel_no:02d}_publish", image_version, "jpg"
        )
        save_image(panel_final_png_bytes, panel_final_path)
        save_image(panel_publish_jpg_bytes, panel_publish_path)
        slide_outputs.append(
            {
                "slide_index": panel_no + 1,
                "slide_type": "panel",
                "panel_no": panel_no,
                "png_bytes": panel_final_png_bytes,
                "jpg_bytes": panel_publish_jpg_bytes,
                "final_path": panel_final_path,
                "publish_path": panel_publish_path,
                "layout": [],
            }
        )
    _mark_pipeline_stage(
        artifact_paths,
        "slide_outputs_ready",
        topic=topic,
        notes=notes,
        extra={"slide_count": len(slide_outputs)},
    )

    # ── Phase 9: Lightweight recording report (no blocking / no regeneration) ──
    quality_report: dict[str, Any] = {
        "quality_decision": "allow",
        "hard_blockers": [],
        "manual_review_reasons": [],
        "soft_scores": {},
        "notes": ["design-first single-pass — quality gates disabled"],
        "summary": "설계 우선 단일 패스 생성 완료",
    }
    status = "approved"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    approved_by = settings.approval_default_user
    approved_at = now.isoformat()
    approved_image_version: int | str = image_version
    _mark_pipeline_stage(
        artifact_paths,
        "quality_report_ready",
        topic=topic,
        notes=notes,
        extra={"quality_decision": quality_report["quality_decision"]},
    )

    # ── Phase 10: Metadata, JSON exports, and Drive upload ──
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "week_key": week_key,
        "topic": topic,
        "title": brief["title"],
        "thumbnail_subtitle": brief.get("thumbnail_subtitle", ""),
        "episode_scope": brief.get("episode_scope", ""),
        "subtitle_scope": brief.get("subtitle_scope", ""),
        "scope_summary": brief.get("scope_summary", ""),
        "caption": brief["caption"],
        "hashtags": brief["hashtags"],
        "image_prompt": brief["image_prompt"],
        "outfit_plan": brief.get("outfit_plan", {}),
        "prop_registry": brief.get("prop_registry", {}),
        "panels": current_panels,
        "character_notes": brief.get("character_notes", ""),
        "character_reference_files": [
            str(path.relative_to(settings.character_assets_dir.parent)) for path in reference_files
        ],
        "llm_model": brief["model"],
        "llm_thinking_level": settings.llm_thinking_level,
        "image_model": settings.image_model,
        "font_file": thumbnail_render["font_path"],
        "costume_sheet_file": costume_sheet_path.name,
        "thumbnail_base_file": thumbnail_base_path.name,
        "thumbnail_final_file": thumbnail_final_path.name,
        "panel_base_files": [item["base_path"].name for item in panel_base_items],
        "quality_report": quality_report,
        "notes": notes,
        "approved_image_version": approved_image_version,
        "dialogue_export_file": "panel_dialogues.json",
        "video_prompt_export_file": "panel_video_prompts.json",
        "slides": [
            {
                "slide_index": slide["slide_index"],
                "slide_type": slide["slide_type"],
                "panel_no": slide.get("panel_no"),
                "file": slide["final_path"].name,
                "publish_file": slide["publish_path"].name,
            }
            for slide in slide_outputs
        ],
    }

    notes_parts = [notes] if notes else []
    notes_parts.append(f"quality_decision={quality_report['quality_decision']}")
    metadata["processing_notes"] = notes_parts

    outbound_artifacts = [
        OutboundArtifact(
            source_path=thumbnail_base_path,
            bundle_name=thumbnail_base_path.name,
            mime_type="image/png",
            make_public=False,
            artifact_role="thumbnail_base_png",
            slide_index=0,
            slide_type="thumbnail",
        )
    ]
    for slide in slide_outputs:
        outbound_artifacts.append(
            OutboundArtifact(
                source_path=slide["final_path"],
                bundle_name=slide["final_path"].name,
                mime_type="image/png",
                make_public=False,
                artifact_role="thumbnail_final_png" if slide["slide_type"] == "thumbnail" else "panel_final_png",
                slide_index=slide["slide_index"],
                slide_type=slide["slide_type"],
                panel_no=slide.get("panel_no"),
            )
        )
        outbound_artifacts.append(
            OutboundArtifact(
                source_path=slide["publish_path"],
                bundle_name=slide["publish_path"].name,
                mime_type="image/jpeg",
                make_public=True,
                artifact_role="thumbnail_publish_jpg" if slide["slide_type"] == "thumbnail" else "panel_publish_jpg",
                slide_index=slide["slide_index"],
                slide_type=slide["slide_type"],
                panel_no=slide.get("panel_no"),
            )
        )

    outbound_bundle = OutboundBundle(
        run_id=run_id,
        week_key=week_key,
        drive_path_parts=(f"{now.year}년", f"{now.month:02d}월", run_id),
        upload_artifacts=tuple(outbound_artifacts),
        metadata_payload=metadata,
        json_payloads=(
            (
                "panel_dialogues.json",
                _build_panel_dialogue_payload(
                    run_id=run_id,
                    topic=topic,
                    title=brief["title"],
                    panels=current_panels,
                ),
            ),
            (
                "panel_video_prompts.json",
                _build_video_scene_prompt_payload(
                    run_id=run_id,
                    topic=topic,
                    title=brief["title"],
                    subtitle=brief.get("thumbnail_subtitle", ""),
                    scope_summary=brief.get("scope_summary", ""),
                    panels=current_panels,
                    panel_base_items=panel_base_items,
                ),
            ),
        ),
        quality_report=quality_report,
        status=status,
        approved_by=approved_by,
        approved_at=approved_at,
        approved_image_version=approved_image_version,
        slides=tuple(metadata["slides"]),
    )

    _mark_pipeline_stage(
        artifact_paths,
        "drive_upload_started",
        topic=topic,
        notes=notes,
        extra={"artifact_count": len(outbound_artifacts), "json_payload_count": len(outbound_bundle.json_payloads)},
    )
    return execute_outbound_bundle(settings, outbound_bundle)
