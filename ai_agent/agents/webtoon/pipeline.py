from __future__ import annotations

import copy
import json
import logging
import mimetypes
import re
import shutil
import tempfile
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
    InstagramGraphClient,
    normalize_speaker_dialogues,
    convert_image_bytes,
    normalize_dialogue_text,
    save_image,
    sanitize_public_text,
    upload_image_for_instagram,
)
from .config import WebtoonSettings
from .text_renderer import render_text_boxes, render_thumbnail_card, review_bubble_layout


WEEKLY_PLANNING_HEADERS = [
    "week_key",
    "run_id",
    "attempt_no",
    "input_mode",
    "generator_model",
    "ocr_model",
    "topic",
    "caption",
    "drive_folder_url",
    "composited_image_file_url",
    "final_image_file_url",
    "is_active",
    "status",
    "approved_by",
    "approved_at",
    "approved_image_version",
    "instagram_post_id",
    "instagram_post_url",
    "published_file_url",
    "posted_at",
    "last_updated_at",
    "notes",
]

PREFERRED_CHARACTER_REFERENCE_FILES = [
    "black_cat.png",
    "gray_cat.png",
    "character_sheet.png",
]
MAX_WEBTOON_PANEL_COUNT = 6
MIN_SOFT_SCORE = 0.72
MIN_AVERAGE_SOFT_SCORE = 0.82
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
class PublishRequest:
    caption: str
    image_bundle_names: tuple[str, ...]


@dataclass(frozen=True)
class OutboundBundle:
    run_id: str
    week_key: str
    drive_path_parts: tuple[str, ...]
    upload_artifacts: tuple[OutboundArtifact, ...]
    metadata_payload: dict[str, Any]
    metadata_filename: str
    ocr_payload: dict[str, Any]
    ocr_filename: str
    correction_payloads: tuple[tuple[str, dict[str, Any]], ...]
    sheet_name: str
    sheet_headers: tuple[str, ...]
    sheet_row: dict[str, Any]
    quality_report: dict[str, Any]
    publish_decision: str
    initial_status: str
    approved_by: str
    approved_at: str
    approved_image_version: int | str
    slides: tuple[dict[str, Any], ...]
    publish_request: PublishRequest | None


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


def execute_outbound_bundle(settings: WebtoonSettings, bundle: OutboundBundle) -> dict[str, Any]:
    google_client = GoogleWorkspaceClient(settings)
    instagram_client = (
        InstagramGraphClient(settings)
        if bundle.publish_request is not None and bundle.publish_decision == "allow"
        else None
    )

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

    publish_result: dict[str, Any] | None = None
    status = bundle.initial_status
    posted_at = ""
    instagram_post_id = ""
    instagram_post_url = ""
    published_file_url = ""

    if bundle.publish_request is not None and bundle.publish_decision == "allow":
        if instagram_client is None:
            raise RuntimeError("Publish requested but Instagram client was not initialized.")
        slide_urls: list[str] = []
        for bundle_name in bundle.publish_request.image_bundle_names:
            artifact = artifact_by_bundle_name[bundle_name]
            hosted_url = upload_image_for_instagram(
                artifact.source_path.read_bytes(),
                filename=artifact.bundle_name,
            )
            logger.info("이미지 호스팅 완료: %s -> %s", artifact.bundle_name, hosted_url)
            slide_urls.append(hosted_url)
        publish_result = instagram_client.publish_carousel(
            image_urls=slide_urls,
            caption=bundle.publish_request.caption,
        )
        status = "posted"
        posted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        media = publish_result.get("media", {})
        instagram_post_id = str(media.get("id", ""))
        instagram_post_url = str(media.get("permalink", ""))
        published_file_url = slide_urls[0] if slide_urls else ""
    elif bundle.publish_request is not None and bundle.publish_decision != "allow":
        logger.warning(
            "Instagram 게시를 건너뜁니다. publish_decision=%s, hard_blockers=%s, manual_review=%s",
            bundle.publish_decision,
            bundle.quality_report.get("hard_blockers", []),
            bundle.quality_report.get("manual_review_reasons", []),
        )

    metadata_payload = copy.deepcopy(bundle.metadata_payload)
    metadata_payload["drive_folder"] = drive_folder
    metadata_payload["uploaded_files"] = uploaded_files_by_role
    metadata_payload["approved_image_version"] = bundle.approved_image_version
    metadata_payload["status"] = status
    metadata_payload["approved_by"] = bundle.approved_by
    metadata_payload["approved_at"] = bundle.approved_at
    metadata_payload["publish_result"] = publish_result

    google_client.upload_json(drive_folder["id"], bundle.metadata_filename, metadata_payload)
    google_client.upload_json(drive_folder["id"], bundle.ocr_filename, bundle.ocr_payload)
    for filename, payload in bundle.correction_payloads:
        google_client.upload_json(drive_folder["id"], filename, payload)
    if publish_result is not None:
        google_client.upload_json(drive_folder["id"], "publish_result_v1.json", publish_result)

    sheet_row = dict(bundle.sheet_row)
    thumbnail_link = uploaded_files_by_role["thumbnail"].get("final_png", {}).get("webViewLink", "")
    sheet_row["drive_folder_url"] = drive_folder.get("webViewLink", "")
    sheet_row["composited_image_file_url"] = thumbnail_link
    sheet_row["final_image_file_url"] = thumbnail_link
    sheet_row["status"] = status
    sheet_row["approved_by"] = bundle.approved_by
    sheet_row["approved_at"] = bundle.approved_at
    sheet_row["approved_image_version"] = bundle.approved_image_version
    sheet_row["instagram_post_id"] = instagram_post_id
    sheet_row["instagram_post_url"] = instagram_post_url
    sheet_row["published_file_url"] = published_file_url
    sheet_row["posted_at"] = posted_at
    sheet_row["last_updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    sheet_result = google_client.append_row(bundle.sheet_name, list(bundle.sheet_headers), sheet_row)

    return {
        "run_id": bundle.run_id,
        "week_key": bundle.week_key,
        "status": status,
        "drive_folder": drive_folder,
        "sheet_append_result": sheet_result,
        "instagram": publish_result,
        "uploaded_files": uploaded_files_by_role,
        "character_reference_files": metadata_payload.get("character_reference_files", []),
        "correction_attempts": metadata_payload.get("correction_attempts", []),
        "quality_report": bundle.quality_report,
        "publish_decision": bundle.publish_decision,
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
    "- 콜라 고정 특징: 매우 짙은 검은 털, 둥글고 부드러운 얼굴형, 큰 삼각 귀, 갈색빛 안쪽 귀, 둥근 노란 눈, 줄무늬나 반점 없음, 매끈한 단색 몸통, 둥글게 말리는 꼬리 끝.\n"
    "- 제로 고정 특징: 밝은 회색 바탕, 진한 회색 클래식 태비 소용돌이 무늬, 이마의 선명한 M자 무늬, 뺨 줄무늬, 옆구리의 큰 소용돌이 무늬, 줄무늬 꼬리, 갈색 눈, 분홍 안쪽 귀, 밝은 주둥이와 배.\n"
    "- 특히 제로의 옆구리 소용돌이 무늬와 이마 M자 무늬를 절대 단순화하거나 직선 줄무늬 몇 개로 바꾸지 않는다.\n"
    "- 캐릭터 얼굴형과 체형은 참조 이미지보다 더 날렵하거나 각지게 바꾸지 않는다. 참조처럼 둥글고 부드러운 인상을 유지한다.\n"
    "- 옷, 반다나, 목걸이, 모자, 신발, 액세서리를 절대 추가하지 않는다.\n"
)


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
    if previous_panel is None:
        return False
    previous_location = str(previous_panel.get("location", "")).strip()
    current_location = str(panel.get("location", "")).strip()
    if previous_location and current_location and previous_location == current_location:
        return True

    if _panel_carryover_props(panel, previous_panel):
        return True

    current_props = {prop.casefold() for prop in _panel_key_props(panel)}
    previous_props = _panel_key_props(previous_panel)
    return any(prop.casefold() in current_props for prop in previous_props)

BACKGROUND_TEXT_SIMPLIFICATION_BLOCK = (
    "- 간판, 안내판, 디지털 화면, 메뉴판, 라벨처럼 텍스트가 많은 배경 요소는 글자를 과하게 넣지 않는다. 꼭 필요하면 1~3개의 짧고 정확한 실제 단어만 쓰고, 나머지는 색 막대, 아이콘, 단순 선으로 표현한다.\n"
    "- 여권, 비자, 문서철, 입국 도장, 항공편 전광판처럼 원래 미세 글자가 많은 영역은 긴 문장이나 빽빽한 본문을 쓰지 않는다. 제목급 짧은 라벨 1~3개만 선명하게 남기고 나머지는 빈 줄, 박스, 아이콘, 단순 선으로 처리한다.\n"
    "- 철자를 자신 있게 정확히 쓸 수 없는 배경 텍스트는 쓰지 않는다. 장면 이해에 꼭 필요한 핵심 라벨 1개만 남기고 나머지는 빈 막대, 아이콘, 단순 선으로 처리한다.\n"
    "- 장소 종류와 무관하게 표지판이 많은 장면이라도 한 표지판당 큰 라벨 하나를 우선한다. 여러 문장, 여러 줄의 미세 안내문, 빽빽한 표 목록은 금지한다.\n"
    "- 장면 이해에 꼭 필요한 표지판은 가능하면 단일 단어 또는 매우 짧은 2단어 라벨로 제한한다. 예: 'INFO', 'EXIT', 'OPEN', 'TICKET', 'Gate A12', 'Aisle 3'.\n"
    "- 전광판/디지털 화면은 큰 헤더 1개와 짧은 행 1~3개만 허용한다. 예: 'INFO', 'Gate A12', 'On Time'. 작은 시간표 미세문구는 쓰지 않는다.\n"
    "- 문서/서류/티켓은 본문 미세글자를 쓰지 말고 큰 라벨 1~3개만 허용한다. 예: 'DOCUMENT', 'NAME', 'ID'. 나머지는 빈 줄이나 단순 선으로 둔다.\n"
    "- 도장/스탬프/원형 인장에는 짧은 인장 라벨 1개와 짧은 날짜 한 줄만 허용한다. 둘레 미세문구나 원형 장식 텍스트는 금지한다.\n"
    "- 버튼, 키오스크, 기기 UI 보조 텍스트는 아이콘, 짧은 라벨, 빈 막대로만 표현한다.\n"
    "- 의미 없는 알파벳 덩어리, 모자이크처럼 깨진 텍스트, 읽을 수 없는 미세 글자는 절대 만들지 않는다.\n"
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
) -> str:
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
    scene_block = f"- 표지 핵심 장면: {thumbnail_scene_prompt}\n" if thumbnail_scene_prompt else ""
    subtitle_block = f"- 썸네일 부제목: {subtitle.strip()}\n" if subtitle.strip() else ""
    caption_block = f"- 인스타 캡션 핵심 문맥: {caption.strip()}\n" if caption.strip() else ""
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
        "- 캐릭터는 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단처럼 사람이 올라서면 안 되는 표면 위에 서지 않는다. 이런 물체는 배경 소품이고, 캐릭터는 주변의 정상적인 바닥, 계단, 좌석, 플랫폼 면 위에 선다.\n"
        "- 계단, 경사면, 이동 장치가 있는 장면이어도 두 캐릭터는 끝까지 완전한 이족보행을 유지한다. 한 캐릭터라도 네 발로 기어오르거나 앞발로 체중을 지탱하는 자세는 금지한다.\n"
        "- thumbnail_scene_prompt나 장면 설명에 소품, 가방, 캐리어, 지도, 티켓, 여권, 휴대폰 같은 핵심 물건이 지정되면 그 물건을 정확히 그대로 그린다. 비슷한 다른 물건으로 바꾸지 않는다.\n"
        "- 캐릭터가 무엇을 손에 들고 있는지, 어떤 손으로 잡고 있는지, 무엇을 가리키는지는 scene prompt의 지시를 우선한다.\n"
        "- thumbnail_scene_prompt에 문서철, 지도, 여권, 티켓, 휴대폰, 캐리어, 가방처럼 핵심 소품이 지정되면 종류와 주된 색상까지 그대로 지킨다. 비슷한 다른 소품으로 바꾸면 실패다.\n"
        f"{BACKGROUND_TEXT_SIMPLIFICATION_BLOCK}"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 검은 캐릭터(콜라): 자신감 있는 포즈, 노란 눈, 검은 단색 털. 왼쪽에 배치.\n"
        "- 회색 줄무늬 캐릭터(제로): 약간 걱정하는 표정, 갈색 눈, 회색+검은 줄무늬, 분홍 귀. 오른쪽에 배치.\n"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 콜라는 제로보다 전신 기준으로 약간 더 크게 보이게 그린다. 대략 10~15% 크게 유지한다.\n"
        "- 콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다. 머리, 몸통, 전체 키에서 콜라가 항상 더 크게 읽혀야 한다.\n"
        "- 이 크기 서열은 썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙이다. 어느 컷에서도 제로가 더 크거나 같게 읽히면 실패다.\n"
        "- 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지해야 한다. 어떤 컷에서는 10~15% 차이였는데 다른 컷에서 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아지면 실패다.\n"
        "- 원근이나 포즈 때문에 제로가 더 커 보일 수 있으면 카메라 거리와 배치를 조정해서라도 콜라가 더 크게 보이게 만든다.\n"
        "- 주연 캐릭터는 정확히 콜라와 제로 둘뿐이다. 추가 고양이형 캐릭터, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 공간 설명에 필요한 비주요 인간 배경 인물이나 대기 줄은 필요할 때만 배경 요소로 허용한다.\n"
        "- 한 장 안에 같은 장면을 두 번 반복하거나 위아래/좌우로 분할된 멀티패널 구도를 절대 만들지 않는다.\n"
        "- 상단 하늘이나 빈 여백에 반응용 얼굴 컷인, 추가 전신, 잘린 캐릭터 일부를 넣지 않는다.\n"
        "- 배경은 이번 주제의 대표 공간과 사건 순간을 분명히 보여주는 단일 티저 장면이어야 한다.\n"
        "- 썸네일은 1컷의 반복이 아니라 별도의 예고 장면이어야 한다. 1컷과 같은 나란히 서기 포즈, 같은 시선, 같은 캐리어 배치, 같은 거리감은 피한다.\n"
        "- 본문 1~6컷 어느 곳에서도 썸네일의 배경 구조, 서브로케이션, 카메라 거리, 대표 간판, 대표 소품 배치를 다시 사용하면 안 된다. 같은 메인 홀/복도/대기 공간 구조에 간판만 바꾼 버전도 금지다.\n"
        "- 이 금지는 특정 한 컷에만 적용되는 것이 아니라 패널 1부터 패널 6 전체에 적용된다. 어느 컷에서든 썸네일 대표 배경이 재등장하면 실패다.\n"
        "- 썸네일은 구조적 지문까지 본문과 달라야 한다. 천장 형태, 중앙 홀 구조, 복도 깊이, 출구 프레임, 카운터 배치, 바닥 패턴, 대표 간판 군집이 본문 어느 컷과도 겹치면 실패다.\n"
        "- journey 에피소드라면 썸네일을 특정 패널 장소의 넓은 버전으로 만들지 말고, 전체 여정을 소개하는 별도 전환 공간 또는 대표 출발 장면으로 만든다.\n"
        "- 이미지 상단 40%는 후처리에서 제목 텍스트를 올릴 공간이므로 단색 또는 하늘/벽 같은 단순한 배경으로 비워둔다.\n"
        "- 이미지 하단 60%에 두 캐릭터가 크고 선명하게 배치.\n"
        "- 만화적 이펙트(반짝임, 하트, 별 등)를 적절히 활용하여 웹툰 느낌을 강조.\n"
        "- 말풍선, 자막, 대사 텍스트, 제목 텍스트는 이미지 안에 절대 넣지 않는다.\n"
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
) -> str:
    text_block = "\n".join(f"- {line}" for line in dialogue_lines) if dialogue_lines else "- 텍스트 없음"
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
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
        "- 이 컷은 위 공개 카피와 범위 요약이 약속한 이야기 범위 안에 있어야 한다.\n"
        "- episode_scope가 single_location이면 패널 변화가 있더라도 같은 현장/상황권 안의 전개로 읽혀야 한다. episode_scope가 journey면 여러 장소를 이동해도 같은 여정 안의 비트로 이어져야 한다.\n"
        "- subtitle_scope가 single_location이면 부제목이 약속한 현장/상황을 벗어난 다른 에피소드처럼 보이면 안 된다.\n"
        "- 함께 제공된 참조 이미지의 캐릭터를 의인화하여 그린다.\n"
        "- 두 캐릭터는 반드시 두 발로 서서 걷고 앞발을 손처럼 사용하는 의인화 캐릭터로 묘사한다.\n"
        "- 두 캐릭터 모두 골반과 상체가 사람처럼 직립해야 한다.\n"
        "- 앞발로 바닥을 짚어 체중을 싣는 일반 동물형 자세는 금지한다.\n"
        "- 썸네일부터 마지막 6컷까지 공통 규칙으로, 네 발 보행이나 앞발 체중 지지는 한 번이라도 허용되지 않는다.\n"
        "- 앉는 장면도 골반이 좌석에 닿은 상태에서 상체는 세워 두고, 앞발은 손처럼 사용한다.\n"
        "- 계단, 경사면, 이동 장치가 있는 장면에서도 두 캐릭터는 끝까지 완전한 이족보행을 유지한다. 한 캐릭터라도 네 발로 기어오르거나 앞발로 체중을 지탱하는 자세는 금지한다.\n"
        "- 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단은 배경 장비다. 캐릭터를 그 위에 올려놓지 말고, 항상 옆 바닥/플랫폼/계단 디딤면 위에 세운다.\n"
        "- 장면 설명에 지정된 핵심 소품과 손동작은 정확히 지킨다. 캐리어, 가방, 표, 지도, 여권, 휴대폰, 문서, 버튼 조작 같은 오브젝트를 임의의 다른 물건으로 바꾸지 않는다.\n"
        f"{continuity_block}"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 콜라(검은색)는 항상 화면 왼쪽, 제로(회색 줄무늬)는 항상 화면 오른쪽에 배치.\n"
        "- 콜라의 전신 크기는 제로보다 늘 약간 더 크게 보이게 그린다. 대략 10~15% 더 크게 유지한다.\n"
        "- 콜라가 제로와 비슷한 크기이거나 더 작아 보이면 안 된다. 머리, 몸통, 전체 키에서 콜라가 항상 더 크게 읽혀야 한다.\n"
        "- 이 크기 서열은 썸네일부터 패널 6까지 모든 컷에 공통으로 적용되는 절대 규칙이다. 어느 컷에서도 제로가 더 크거나 같게 읽히면 실패다.\n"
        "- 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지해야 한다. 이번 컷에서만 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아져서는 안 된다.\n"
        "- 원근이나 포즈 때문에 제로가 더 커 보일 수 있으면 배치와 카메라를 조정해서라도 콜라가 더 크게 보이게 만든다.\n"
        "- 주연 캐릭터는 정확히 콜라와 제로 둘뿐이다. 추가 고양이형 인물, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 공공장소를 설명하기 위한 비주요 인간 배경 인물, 승객, 점원, 직원, 대기 줄은 필요하면 배경 요소로 허용한다.\n"
        "- 두 주인공은 같은 장면 안의 같은 바닥 평면에 한 번씩만 등장한다. 상단 하늘이나 빈 여백에 별도의 캐릭터 컷인을 만들지 않는다.\n"
        "- 이 이미지는 단일 패널 한 컷만 담아야 한다. 위아래 두 장면, 좌우 분할, 반복된 동일 장면, 만화 페이지처럼 여러 컷이 섞인 구도를 절대 만들지 않는다.\n"
        "- 물건 잡기, 기계 조작, 제스처 등 사람처럼 행동하는 모습을 자연스럽게 표현한다.\n"
        "- 캐릭터 얼굴형과 털색은 참조 이미지와 일치시키되, 자세는 반드시 인간형 직립 이족보행으로 유지한다.\n"
        "- 이전 컷들과 같은 외형을 유지한다. 털색, 줄무늬, 눈 색, 체형, 꼬리 길이를 임의로 바꾸지 않는다.\n"
        "- 앉아 있는 장면이라면 최소 한 앞발은 무릎, 좌석, 테이블, 휴대폰 같은 물체와 상호작용하며 손처럼 보여야 한다.\n"
        "- 만화적 이펙트(놀람 표시 !, ?, 땀방울, 분노 마크 등)를 적극 활용.\n"
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
        f"{composition_hint}"
        "- 배경에 상품명, 브랜드, 간판 텍스트를 최소화한다. 꼭 필요하면 실존 독일 브랜드(REWE, Milka, DM 등)만 정확한 철자로.\n"
        "- 가상의 브랜드명이나 의미 없는 텍스트를 배경에 절대 넣지 않는다.\n"
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
            "- This size rule is absolute from the thumbnail through panel 6. Kolla must remain visibly larger than Zero in head size, torso mass, and full-body silhouette. Never let Zero read as the larger or equal-sized character.\n"
            "- Keep the Kolla-to-Zero size ratio stable across the whole episode. Do not suddenly enlarge Kolla or shrink Zero beyond the same narrow 10 to 15 percent band.\n"
            "- Both protagonists must stay anthropomorphic bipeds with upright pelvis and upright torso.\n"
            "- Never let either protagonist stand, walk, run, crawl, or brace on all fours.\n"
            "- Forepaws must act like hands only and must never be weight-bearing front legs.\n"
            "- If seated, the pelvis may rest on a seat, but the torso must remain upright and the forepaws must still read as hands.\n"
            "- Never place either protagonist on top of a baggage belt, baggage carousel, train track, rail, checkout belt, or escalator handrail. Keep them on the proper floor, platform, or stair tread beside the machinery.\n"
            "- In stair or escalator scenes, Zero must remain fully bipedal and upright rather than dropping to four legs.\n"
            "- If the requested scene specifies key props or hand interactions, restore those exact props and actions instead of inventing substitutes.\n"
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
            "- Keep exactly one Kolla on the left and one Zero on the right.\n"
            "- Keep Kolla visibly larger than Zero in every shot, with larger head size, torso mass, and overall silhouette. Never allow equal or reversed size reading.\n"
            "- Keep the Kolla-to-Zero size ratio stable across the thumbnail and panels. Do not let one shot suddenly inflate Kolla or shrink Zero far beyond the same narrow 10 to 15 percent band.\n"
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

    background_message = _background_gate_error_message("썸네일", thumbnail_background_check)
    if background_message:
        hard_blockers.append(background_message)
    if _check_requires_manual_review(thumbnail_background_check):
        manual_review_reasons.append("썸네일 배경 텍스트 검사를 수동 확인해야 합니다.")

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
        message = _background_gate_error_message(f"패널 {panel_no}", background_check)
        if message:
            hard_blockers.append(message)
        if _check_requires_manual_review(background_check):
            manual_review_reasons.append(f"패널 {panel_no} 배경 텍스트 검사를 수동 확인해야 합니다.")

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


def run_webtoon_pipeline(
    settings: WebtoonSettings,
    *,
    topic: str,
    publish: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    week_key, run_id = build_run_identity()
    artifact_paths = create_artifact_paths(run_id, week_key)
    try:
        return _execute_webtoon_pipeline(
            settings,
            topic=topic,
            publish=publish,
            notes=notes,
            week_key=week_key,
            run_id=run_id,
            artifact_paths=artifact_paths,
        )
    finally:
        shutil.rmtree(artifact_paths.run_dir, ignore_errors=True)


def _execute_webtoon_pipeline(
    settings: WebtoonSettings,
    *,
    topic: str,
    publish: bool,
    notes: str,
    week_key: str,
    run_id: str,
    artifact_paths: PipelineArtifacts,
) -> dict[str, Any]:
    llm_client = GeminiTextClient(settings)
    image_client = GeminiImageClient(settings)
    ocr_client = GeminiOcrClient(settings)

    brief = llm_client.build_creative_brief(topic)
    sanitized_topic = sanitize_public_text(topic, fallback=brief["title"])
    reference_files = list_character_reference_files(settings)
    reference_parts = _build_reference_image_parts(reference_files)

    thumbnail_image_text = ""
    thumbnail_base_png_bytes = b""
    thumbnail_character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_bg_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}
    thumbnail_distinction_check: dict[str, Any] = {
        "has_issues": False,
        "issues": [],
        "edit_instruction": "",
        "duplicated_panel_numbers": [],
    }
    thumbnail_prompt = ""
    thumbnail_retry_feedback = ""
    max_generation_attempts = max(2, settings.max_correction_attempts + 1)

    for generation_attempt in range(1, max_generation_attempts + 1):
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
        thumbnail_base_png_bytes, thumbnail_character_check = correct_character_composition(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
        thumbnail_base_png_bytes, thumbnail_reference_check = correct_character_reference_consistency(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
        quality_errors = [
            message
            for message in (
                _quality_gate_error_message("썸네일 캐릭터 구성", thumbnail_character_check),
                _quality_gate_error_message("썸네일 캐릭터 참조 일관성", thumbnail_reference_check),
            )
            if message
        ]
        thumbnail_base_png_bytes, thumbnail_bg_check = correct_background_text(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
        background_error = _background_gate_error_message("썸네일", thumbnail_bg_check)
        if background_error:
            quality_errors.append(background_error)
        if not quality_errors:
            break
        thumbnail_retry_feedback = "\n".join(f"- {message}" for message in quality_errors)
        logger.warning(
            "썸네일 재생성 필요 (시도 %d/%d): %s",
            generation_attempt,
            max_generation_attempts,
            " | ".join(quality_errors),
        )
    else:
        logger.warning("썸네일 품질 게이트를 통과하지 못했지만 최선의 결과로 진행합니다: %s", thumbnail_retry_feedback)

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

    panel_base_items: list[dict[str, Any]] = []
    panel_generation_notes: list[dict[str, Any]] = [
        {
            "slide_type": "thumbnail",
            "image_notes": thumbnail_image_text,
            "file": thumbnail_base_path.name,
            "character_composition_check": thumbnail_character_check,
            "character_reference_check": thumbnail_reference_check,
            "background_text_check": thumbnail_bg_check,
            "thumbnail_distinction_check": thumbnail_distinction_check,
        }
    ]
    current_panels = [
        {
            "panel_no": int(panel.get("panel_no", index)),
            "story_role": str(panel.get("story_role", "")).strip(),
            "location": str(panel.get("location", "")).strip(),
            "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
            "speaker_dialogues": normalize_speaker_dialogues(panel),
            "dialogue_lines": flatten_speaker_dialogues(normalize_speaker_dialogues(panel)),
        }
        for index, panel in enumerate(brief.get("panels", [])[:MAX_WEBTOON_PANEL_COUNT], start=1)
    ]

    def _generate_single_panel(
        panel: dict[str, Any],
        *,
        previous_panel: dict[str, Any] | None = None,
        previous_panel_item: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        panel_reference_paths = [*reference_files]
        panel_reference_parts = [*reference_parts]
        panel_scene_context = _build_panel_scene_context(panel, previous_panel)
        if previous_panel_item is not None and _should_reference_previous_panel(panel, previous_panel):
            panel_reference_paths.append(previous_panel_item["base_path"])
            panel_reference_parts.append((previous_panel_item["base_bytes"], "image/png"))
        panel_retry_feedback = ""
        panel_image_text = ""
        panel_base_png_bytes = b""
        panel_character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
        panel_reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
        panel_bg_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}
        panel_prompt = ""

        for generation_attempt in range(1, max_generation_attempts + 1):
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
            if panel_retry_feedback:
                panel_prompt += (
                    "\n\n재생성 보정 지시:\n"
                    "- 아래 실패 사유를 반드시 반영해 처음부터 다시 생성한다.\n"
                    f"{panel_retry_feedback}"
                )
            panel_image_bytes, panel_mime_type, panel_image_text = image_client.generate_image(
                panel_prompt,
                reference_images=panel_reference_parts,
                reference_image_paths=panel_reference_paths,
            )
            panel_base_png_bytes = (
                panel_image_bytes if panel_mime_type == "image/png" else convert_image_bytes(panel_image_bytes, "PNG")
            )
            panel_base_png_bytes, panel_character_check = correct_character_composition(
                image_client,
                ocr_client,
                panel_base_png_bytes,
                panel_scene_context,
                reference_images=panel_reference_parts,
                reference_image_paths=panel_reference_paths,
                max_attempts=max(1, settings.max_correction_attempts),
            )
            panel_base_png_bytes, panel_reference_check = correct_character_reference_consistency(
                image_client,
                ocr_client,
                panel_base_png_bytes,
                panel_scene_context,
                reference_images=panel_reference_parts,
                reference_image_paths=panel_reference_paths,
                max_attempts=max(1, settings.max_correction_attempts),
            )
            quality_errors = [
                message
                for message in (
                    _quality_gate_error_message(f"패널 {panel.get('panel_no')} 캐릭터 구성", panel_character_check),
                    _quality_gate_error_message(
                        f"패널 {panel.get('panel_no')} 캐릭터 참조 일관성",
                        panel_reference_check,
                    ),
                )
                if message
            ]
            panel_base_png_bytes, panel_bg_check = correct_background_text(
                image_client,
                ocr_client,
                panel_base_png_bytes,
                panel_scene_context,
                reference_images=panel_reference_parts,
                reference_image_paths=panel_reference_paths,
                max_attempts=max(1, settings.max_correction_attempts),
            )
            background_error = _background_gate_error_message(f"패널 {panel.get('panel_no')}", panel_bg_check)
            if background_error:
                quality_errors.append(background_error)
            if not quality_errors:
                break
            panel_retry_feedback = "\n".join(f"- {message}" for message in quality_errors)
            logger.warning(
                "패널 %s 재생성 필요 (시도 %d/%d): %s",
                panel.get("panel_no"),
                generation_attempt,
                max_generation_attempts,
                " | ".join(quality_errors),
            )
        else:
            logger.warning("패널 %s 품질 게이트를 통과하지 못했지만 최선의 결과로 진행합니다: %s", panel.get("panel_no"), panel_retry_feedback)

        panel_no = int(panel.get("panel_no", 0) or 0)
        panel_base_path = artifact_paths.run_dir / f"panel_{panel_no:02d}_base_v1.png"
        save_image(panel_base_png_bytes, panel_base_path)
        return {
            "item": {
                "panel_no": panel_no,
                "base_path": panel_base_path,
                "base_bytes": panel_base_png_bytes,
            },
            "note": {
                "slide_type": "panel",
                "panel_no": panel_no,
                "scene_prompt": panel.get("scene_prompt", ""),
                "image_notes": panel_image_text,
                "file": panel_base_path.name,
                "character_composition_check": panel_character_check,
                "character_reference_check": panel_reference_check,
                "background_text_check": panel_bg_check,
            },
        }

    for panel_index, panel in enumerate(current_panels):
        result = _generate_single_panel(
            panel,
            previous_panel=current_panels[panel_index - 1] if panel_index > 0 else None,
            previous_panel_item=panel_base_items[-1] if panel_base_items else None,
        )
        panel_base_items.append(result["item"])
        panel_generation_notes.append(result["note"])

    thumbnail_base_png_bytes, thumbnail_distinction_check = correct_thumbnail_panel_distinction(
        image_client,
        ocr_client,
        thumbnail_base_png_bytes,
        thumbnail_scene_prompt=brief.get("thumbnail_scene_prompt", ""),
        panel_base_items=panel_base_items,
        panel_summaries=[
            {
                "panel_no": panel.get("panel_no"),
                "location": panel.get("location", ""),
                "scene_prompt": panel.get("scene_prompt", ""),
                "story_role": panel.get("story_role", ""),
                "key_props": panel.get("key_props", []),
                "carryover_props": panel.get("carryover_props", []),
            }
            for panel in current_panels
        ],
        reference_images=reference_parts,
        reference_image_paths=reference_files,
        max_attempts=max(1, settings.max_correction_attempts),
    )
    thumbnail_distinction_had_issue = any(
        bool(attempt.get("has_issues")) for attempt in thumbnail_distinction_check.get("attempts", [])
    )
    if thumbnail_distinction_had_issue:
        logger.warning(
            "썸네일-패널 구분 보정 수행: %s",
            " | ".join(thumbnail_distinction_check.get("issues", [])) or thumbnail_distinction_check.get("edit_instruction", ""),
        )
        thumbnail_base_png_bytes, thumbnail_character_check = correct_character_composition(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
        thumbnail_base_png_bytes, thumbnail_reference_check = correct_character_reference_consistency(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
        thumbnail_base_png_bytes, thumbnail_bg_check = correct_background_text(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
            reference_images=reference_parts,
            reference_image_paths=reference_files,
            max_attempts=max(1, settings.max_correction_attempts),
        )
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
        save_image(thumbnail_final_png_bytes, thumbnail_final_path)
        save_image(thumbnail_publish_jpg_bytes, thumbnail_publish_path)

    panel_generation_notes[0]["thumbnail_distinction_check"] = thumbnail_distinction_check

    image_version = 1
    correction_reviews: list[dict[str, Any]] = []
    correction_payloads: list[dict[str, Any]] = []
    slide_outputs: list[dict[str, Any]] = []
    aggregated_ocr_payload: dict[str, Any] = {}

    while True:
        slide_outputs = [
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
        panel_reviews: list[dict[str, Any]] = []
        corrected_panels: list[dict[str, Any]] = []
        any_rerender_required = False

        def _ocr_check_panel(panel: dict[str, Any], panel_base: dict[str, Any]) -> dict[str, Any]:
            panel_no = int(panel["panel_no"])
            panel_render_version = image_version
            render_result = render_text_boxes(
                panel_base["base_bytes"],
                [panel],
                settings,
                render_version=panel_render_version,
            )
            layout_review = review_bubble_layout(render_result["layout"])
            for _ in range(2):
                if not layout_review["has_issues"]:
                    break
                panel_render_version += 1
                render_result = render_text_boxes(
                    panel_base["base_bytes"],
                    [panel],
                    settings,
                    render_version=panel_render_version,
                )
                layout_review = review_bubble_layout(render_result["layout"])
            panel_final_png_bytes = render_result["image_bytes"]
            panel_publish_jpg_bytes = convert_image_bytes(panel_final_png_bytes, "JPEG")
            panel_final_path = build_versioned_path(artifact_paths.run_dir, f"panel_{panel_no:02d}_final", image_version, "png")
            panel_publish_path = build_versioned_path(
                artifact_paths.run_dir,
                f"panel_{panel_no:02d}_publish",
                image_version,
                "jpg",
            )
            save_image(panel_final_png_bytes, panel_final_path)
            save_image(panel_publish_jpg_bytes, panel_publish_path)

            bubble_ocr_image = build_bubble_only_ocr_image(panel_final_png_bytes, render_result["layout"])
            try:
                ocr_text = ocr_client.extract_text(bubble_ocr_image, mime_type="image/png")
                review = ocr_client.plan_text_corrections(
                    panel.get("dialogue_lines", []),
                    ocr_text,
                    speaker_dialogues=panel.get("speaker_dialogues", []),
                )
            except Exception as ocr_exc:
                logger.warning("패널 %d OCR 검사 실패, 원본 대화 유지: %s", panel_no, ocr_exc)
                ocr_text = ""
                review = {
                    "rerender_required": False,
                    "corrected_text_lines": panel.get("dialogue_lines", []),
                    "corrected_speaker_dialogues": panel.get("speaker_dialogues", []),
                    "skipped": str(ocr_exc),
                }
            review["panel_no"] = panel_no
            review["source_image_version"] = image_version
            review["render_layout"] = render_result["layout"]
            review["bubble_layout_review"] = layout_review

            corrected_lines = [
                normalize_dialogue_text(str(line).strip())
                for line in review.get("corrected_text_lines", panel.get("dialogue_lines", []))
                if str(line).strip()
            ]

            return {
                "panel_no": panel_no,
                "review_entry": {"panel_no": panel_no, "ocr_text": ocr_text, "review": review},
                "slide": {
                    "slide_index": panel_no + 1,
                    "slide_type": "panel",
                    "panel_no": panel_no,
                    "png_bytes": panel_final_png_bytes,
                    "jpg_bytes": panel_publish_jpg_bytes,
                    "final_path": panel_final_path,
                    "publish_path": panel_publish_path,
                    "layout": render_result["layout"],
                },
                "corrected_panel": _apply_corrected_dialogues(
                    panel,
                    corrected_lines or panel.get("dialogue_lines", []),
                    corrected_speaker_dialogues=review.get("corrected_speaker_dialogues"),
                ),
                "rerender_required": review["rerender_required"],
            }

        with ThreadPoolExecutor(max_workers=max(1, settings.api_parallelism)) as pool:
            ocr_futures = [
                pool.submit(_ocr_check_panel, panel, panel_base)
                for panel, panel_base in zip(current_panels, panel_base_items, strict=True)
            ]
            ocr_results = sorted(
                (f.result() for f in as_completed(ocr_futures)),
                key=lambda r: r["panel_no"],
            )

        for result in ocr_results:
            panel_reviews.append(result["review_entry"])
            slide_outputs.append(result["slide"])
            corrected_panels.append(result["corrected_panel"])
            if result["rerender_required"]:
                any_rerender_required = True

        correction_round = len(correction_reviews) + 1
        correction_payload = {
            "topic": topic,
            "image_version": image_version,
            "thumbnail": {
                "title": brief["title"],
                "subtitle": brief.get("thumbnail_subtitle", ""),
                "file": thumbnail_final_path.name,
                "layout": thumbnail_render["layout"],
            },
            "panels": panel_reviews,
        }
        correction_filename = f"correction_v{correction_round}.json"
        correction_payloads.append({"filename": correction_filename, "payload": correction_payload})
        correction_reviews.append(
            {
                "correction_version": correction_round,
                "source_image_version": image_version,
                "panels": panel_reviews,
            }
        )
        aggregated_ocr_payload = {
            "ocr_model": settings.ocr_extract_model,
            "ocr_review_model": settings.llm_model,
            "image_version": image_version,
            "panels": [{"panel_no": item["panel_no"], "ocr_text": item["ocr_text"]} for item in panel_reviews],
        }

        if not any_rerender_required:
            logger.info("교정 루프 종료: 모든 패널 OCR 검증 통과 (라운드 %d)", correction_round)
            break
        if correction_round > settings.max_correction_attempts:
            logger.warning(
                "교정 루프 종료: 최대 교정 횟수 초과 (max=%d). 일부 패널에 여전히 텍스트 오류가 있을 수 있습니다.",
                settings.max_correction_attempts,
            )
            break

        current_panels = corrected_panels
        image_version += 1

    panel_layout_entries = [
        layout_entry
        for slide in slide_outputs
        if slide.get("slide_type") == "panel"
        for layout_entry in slide.get("layout", [])
    ]
    bubble_layout_review = review_bubble_layout(panel_layout_entries)
    final_review_slide_images = [(slide["png_bytes"], "image/png") for slide in slide_outputs]
    stage_gate_findings = _summarize_stage_gate_findings(
        thumbnail_checks={
            "character": thumbnail_character_check,
            "reference": thumbnail_reference_check,
            "background": thumbnail_bg_check,
            "distinction": thumbnail_distinction_check,
        },
        panel_generation_notes=panel_generation_notes,
    )
    try:
        final_package_review = ocr_client.review_final_webtoon_package(
            topic=topic,
            title=brief["title"],
            thumbnail_subtitle=brief.get("thumbnail_subtitle", ""),
            caption=brief["caption"],
            episode_scope=brief.get("episode_scope", ""),
            subtitle_scope=brief.get("subtitle_scope", ""),
            scope_summary=brief.get("scope_summary", ""),
            thumbnail_scene_prompt=brief.get("thumbnail_scene_prompt", ""),
            panel_summaries=[
                {
                    "panel_no": panel.get("panel_no"),
                    "story_role": panel.get("story_role", ""),
                    "location": panel.get("location", ""),
                    "scene_prompt": panel.get("scene_prompt", ""),
                    "key_props": panel.get("key_props", []),
                    "carryover_props": panel.get("carryover_props", []),
                }
                for panel in current_panels
            ],
            slide_images=final_review_slide_images,
            stage_gate_findings=stage_gate_findings,
        )
    except Exception as exc:
        logger.warning("최종 패키지 검수 실패, 수동 검토 대상으로 표시합니다: %s", exc)
        final_package_review = {
            "hard_blockers": [],
            "soft_scores": {},
            "notes": [],
            "summary": "",
            "review_unavailable": str(exc),
        }

    quality_report = _build_publish_decision(
        publish_requested=publish,
        brief=brief,
        thumbnail_checks={
            "character": thumbnail_character_check,
            "reference": thumbnail_reference_check,
            "background": thumbnail_bg_check,
            "distinction": thumbnail_distinction_check,
        },
        panel_generation_notes=panel_generation_notes,
        panel_reviews=panel_reviews,
        bubble_review=bubble_layout_review,
        final_package_review=final_package_review,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    if quality_report["quality_decision"] == "blocked":
        initial_status = "publish_blocked"
    elif quality_report["quality_decision"] == "manual_review":
        initial_status = "review_required"
    else:
        initial_status = "approved"
    approved_by = settings.approval_default_user if quality_report["quality_decision"] == "allow" else ""
    approved_at = now.isoformat() if quality_report["quality_decision"] == "allow" else ""
    approved_image_version: int | str = image_version if quality_report["quality_decision"] == "allow" else ""

    metadata = {
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
        "panels": current_panels,
        "character_notes": brief.get("character_notes", ""),
        "character_reference_files": [str(path.relative_to(settings.character_assets_dir.parent)) for path in reference_files],
        "llm_model": brief["model"],
        "llm_thinking_level": settings.llm_thinking_level,
        "image_model": settings.image_model,
        "image_notes": panel_generation_notes,
        "font_file": thumbnail_render["font_path"],
        "thumbnail_base_file": thumbnail_base_path.name,
        "thumbnail_final_file": thumbnail_final_path.name,
        "panel_base_files": [item["base_path"].name for item in panel_base_items],
        "ocr_model": settings.llm_model,
        "ocr_review_model": settings.llm_model,
        "ocr_extract_model": settings.ocr_extract_model,
        "ocr_thinking_level": settings.llm_thinking_level,
        "ocr_review_thinking_level": settings.llm_thinking_level,
        "ocr_extract_thinking_level": settings.ocr_extract_thinking_level,
        "ocr_result": aggregated_ocr_payload,
        "correction_model": settings.llm_model,
        "correction_attempts": correction_reviews,
        "quality_report": quality_report,
        "approved_image_version": approved_image_version,
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
    if quality_report["hard_blockers"]:
        notes_parts.append(f"hard_blockers={len(quality_report['hard_blockers'])}")
    elif quality_report["manual_review_reasons"]:
        notes_parts.append(f"manual_review={len(quality_report['manual_review_reasons'])}")
    sheet_row = {
        "week_key": week_key,
        "run_id": run_id,
        "attempt_no": 1,
        "input_mode": "manual_topic",
        "generator_model": settings.image_model,
        "ocr_model": settings.llm_model,
        "ocr_extract_model": settings.ocr_extract_model,
        "topic": topic,
        "caption": brief["caption"],
        "drive_folder_url": "",
        "composited_image_file_url": "",
        "final_image_file_url": "",
        "is_active": "TRUE",
        "status": initial_status,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "approved_image_version": approved_image_version,
        "instagram_post_id": "",
        "instagram_post_url": "",
        "published_file_url": "",
        "posted_at": "",
        "last_updated_at": now.isoformat(),
        "notes": " | ".join(notes_parts),
    }

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
    publish_bundle_names: list[str] = []
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
        publish_bundle_names.append(slide["publish_path"].name)

    full_caption = brief["caption"]
    if brief.get("hashtags"):
        full_caption += "\n\n" + " ".join(brief["hashtags"])

    outbound_bundle = OutboundBundle(
        run_id=run_id,
        week_key=week_key,
        drive_path_parts=(f"{now.year}년", f"{now.month:02d}월", run_id),
        upload_artifacts=tuple(outbound_artifacts),
        metadata_payload=metadata,
        metadata_filename="run_metadata.json",
        ocr_payload=aggregated_ocr_payload,
        ocr_filename=f"ocr_result_v{image_version}.json",
        correction_payloads=tuple((item["filename"], item["payload"]) for item in correction_payloads),
        sheet_name="weekly_planning",
        sheet_headers=tuple(WEEKLY_PLANNING_HEADERS),
        sheet_row=sheet_row,
        quality_report=quality_report,
        publish_decision=quality_report["publish_decision"],
        initial_status=initial_status,
        approved_by=approved_by,
        approved_at=approved_at,
        approved_image_version=approved_image_version,
        slides=tuple(metadata["slides"]),
        publish_request=(
            PublishRequest(
                caption=full_caption,
                image_bundle_names=tuple(publish_bundle_names),
            )
            if publish
            else None
        ),
    )

    return execute_outbound_bundle(settings, outbound_bundle)
