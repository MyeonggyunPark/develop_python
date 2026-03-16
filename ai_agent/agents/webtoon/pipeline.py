from __future__ import annotations

import json
import logging
import mimetypes
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
    GeminiOcrClient,
    GeminiTextClient,
    GeminiImageClient,
    GoogleWorkspaceClient,
    InstagramGraphClient,
    convert_image_bytes,
    normalize_dialogue_text,
    save_image,
    sanitize_public_text,
)
from .config import WebtoonSettings
from .text_renderer import render_text_boxes, render_thumbnail_card


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


@dataclass
class PipelineArtifacts:
    run_dir: Path
    run_id: str
    week_key: str
    metadata_path: Path
    publish_result_path: Path


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
    "manga-style emotion effects (sweat drops, exclamation marks, sparkles, anger marks), "
    "detailed realistic background, high quality digital illustration, "
    "the characters do NOT wear any clothing shoes or accessories they have natural fur only, "
    "absolutely NO text NO speech bubbles NO dialogue NO captions NO UI elements in the image"
)

CHARACTER_REFERENCE_LOCK_BLOCK = (
    "- 함께 제공된 참조 이미지는 단순 참고가 아니라 고정된 마스터 레퍼런스 모델 시트다. 그림체가 아니라 캐릭터 정체성을 그대로 복사해야 한다.\n"
    "- 콜라 고정 특징: 매우 짙은 검은 털, 둥글고 부드러운 얼굴형, 큰 삼각 귀, 갈색빛 안쪽 귀, 둥근 노란 눈, 줄무늬나 반점 없음, 매끈한 단색 몸통, 둥글게 말리는 꼬리 끝.\n"
    "- 제로 고정 특징: 밝은 회색 바탕, 진한 회색 클래식 태비 소용돌이 무늬, 이마의 선명한 M자 무늬, 뺨 줄무늬, 옆구리의 큰 소용돌이 무늬, 줄무늬 꼬리, 갈색 눈, 분홍 안쪽 귀, 밝은 주둥이와 배.\n"
    "- 특히 제로의 옆구리 소용돌이 무늬와 이마 M자 무늬를 절대 단순화하거나 직선 줄무늬 몇 개로 바꾸지 않는다.\n"
    "- 캐릭터 얼굴형과 체형은 참조 이미지보다 더 날렵하거나 각지게 바꾸지 않는다. 참조처럼 둥글고 부드러운 인상을 유지한다.\n"
    "- 옷, 반다나, 목걸이, 모자, 신발, 액세서리를 절대 추가하지 않는다.\n"
)


def build_thumbnail_generation_prompt(
    base_prompt: str,
    *,
    title: str,
    topic: str,
    character_notes: str,
) -> str:
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
    return (
        f"{WEBTOON_STYLE_PREFIX}.\n\n"
        f"{base_prompt}\n\n"
        "추가 지시:\n"
        "- 이 이미지는 웹툰 시리즈 표지(커버) 이미지이다. 본문 컷이 아니다.\n"
        "- 함께 제공된 참조 이미지의 두 캐릭터를 의인화하여 그린다.\n"
        "- 두 캐릭터는 반드시 두 발로 서서, 앞발을 손처럼 사용하는 의인화 캐릭터로 그린다.\n"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 검은 캐릭터(콜라): 자신감 있는 포즈, 노란 눈, 검은 단색 털. 왼쪽에 배치.\n"
        "- 회색 줄무늬 캐릭터(제로): 약간 걱정하는 표정, 갈색 눈, 회색+검은 줄무늬, 분홍 귀. 오른쪽에 배치.\n"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 콜라는 제로보다 전신 기준으로 약간 더 크게 보이게 그린다. 대략 10~15% 크게 유지한다.\n"
        "- 정확히 두 캐릭터만 등장한다. 추가 인물, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 한 장 안에 같은 장면을 두 번 반복하거나 위아래/좌우로 분할된 멀티패널 구도를 절대 만들지 않는다.\n"
        "- 상단 하늘이나 빈 여백에 반응용 얼굴 컷인, 추가 전신, 잘린 캐릭터 일부를 넣지 않는다.\n"
        "- 두 캐릭터가 독일 풍경(거리, 마트, 카페, 공원 등) 속에서 함께 있는 매력적인 한 장면.\n"
        "- 이미지 상단 40%는 후처리에서 제목 텍스트를 올릴 공간이므로 단색 또는 하늘/벽 같은 단순한 배경으로 비워둔다.\n"
        "- 이미지 하단 60%에 두 캐릭터가 크고 선명하게 배치.\n"
        "- 만화적 이펙트(반짝임, 하트, 별 등)를 적절히 활용하여 웹툰 느낌을 강조.\n"
        "- 말풍선, 자막, 대사 텍스트, 제목 텍스트는 이미지 안에 절대 넣지 않는다.\n"
        f"- 주제 분위기: {topic.strip()}\n"
        f"{notes_block}"
    )


def build_panel_generation_prompt(
    base_prompt: str,
    panel: dict[str, Any],
    character_notes: str,
    dialogue_lines: list[str],
) -> str:
    text_block = "\n".join(f"- {line}" for line in dialogue_lines) if dialogue_lines else "- 텍스트 없음"
    notes_block = f"\n캐릭터 일관성 메모: {character_notes}" if character_notes else ""
    panel_no = int(panel.get("panel_no", 1))
    if panel_no % 2 == 1:
        composition_hint = (
            "- 두 캐릭터를 이미지 하단 중앙에 배치 (콜라 왼쪽, 제로 오른쪽). 상단 좌우에 말풍선 공간 확보.\n"
        )
    else:
        composition_hint = (
            "- 두 캐릭터를 이미지 상단~중앙에 배치 (콜라 왼쪽, 제로 오른쪽). 하단 좌우에 말풍선 공간 확보.\n"
        )
    return (
        f"{WEBTOON_STYLE_PREFIX}.\n\n"
        f"{base_prompt}\n\n"
        "추가 지시:\n"
        "- 이 이미지는 6컷 웹툰의 단일 패널이다.\n"
        f"- 현재는 {panel_no}컷 장면만 그린다.\n"
        "- 함께 제공된 참조 이미지의 캐릭터를 의인화하여 그린다.\n"
        "- 두 캐릭터는 반드시 두 발로 서서 걷고 앞발을 손처럼 사용하는 의인화 캐릭터로 묘사한다.\n"
        f"{CHARACTER_REFERENCE_LOCK_BLOCK}"
        "- 캐릭터는 절대 옷, 신발, 액세서리를 착용하지 않는다. 항상 자연스러운 털 그대로.\n"
        "- 콜라(검은색)는 항상 화면 왼쪽, 제로(회색 줄무늬)는 항상 화면 오른쪽에 배치.\n"
        "- 콜라의 전신 크기는 제로보다 늘 약간 더 크게 보이게 그린다. 대략 10~15% 더 크게 유지한다.\n"
        "- 정확히 두 캐릭터만 등장한다. 추가 인물, 떠 있는 얼굴, 감정 스티커용 두상, 분신 컷인은 절대 넣지 않는다.\n"
        "- 두 주인공은 같은 장면 안의 같은 바닥 평면에 한 번씩만 등장한다. 상단 하늘이나 빈 여백에 별도의 캐릭터 컷인을 만들지 않는다.\n"
        "- 이 이미지는 단일 패널 한 컷만 담아야 한다. 위아래 두 장면, 좌우 분할, 반복된 동일 장면, 만화 페이지처럼 여러 컷이 섞인 구도를 절대 만들지 않는다.\n"
        "- 물건 잡기, 기계 조작, 제스처 등 사람처럼 행동하는 모습을 자연스럽게 표현한다.\n"
        "- 캐릭터 얼굴형과 털색은 참조 이미지와 일치시키되, 포즈와 행동은 사람처럼.\n"
        "- 이전 컷들과 같은 외형을 유지한다. 털색, 줄무늬, 눈 색, 체형, 꼬리 길이를 임의로 바꾸지 않는다.\n"
        "- 만화적 이펙트(놀람 표시 !, ?, 땀방울, 분노 마크 등)를 적극 활용.\n"
        "- 표정은 과장되게, 감정이 한눈에 읽히도록.\n"
        "- 배경은 독일 생활 환경을 구체적으로.\n"
        "- 절대로 말풍선, 자막, 대사 텍스트를 이미지 안에 그리지 않는다. 이미지에 텍스트 UI 요소가 전혀 없어야 한다.\n"
        f"{composition_hint}"
        "- 배경에 상품명, 브랜드, 간판 텍스트를 최소화한다. 꼭 필요하면 실존 독일 브랜드(REWE, Milka, DM 등)만 정확한 철자로.\n"
        "- 가상의 브랜드명이나 의미 없는 텍스트를 배경에 절대 넣지 않는다.\n"
        "- 패널 테두리, 컷 번호, 분할선도 그리지 않는다.\n"
        f"- 장면 설명: {panel.get('scene_prompt', '').strip()}\n"
        "- 이 컷의 말풍선 대사(참고용, 이미지에 넣지 않음):\n"
        f"{text_block}"
        f"{notes_block}"
    )


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

        fix_parts = [f'"{c["found"]}" \u2192 "{c["correct"]}"' for c in check["corrections"]]
        edit_prompt = (
            "Fix only the background text spelling in this image as listed below.\n"
            "Do NOT change characters, composition, colors, or art style.\n"
            "Do NOT add speech bubbles or any new UI.\n"
            "Preserve every existing object except the exact text glyphs listed below.\n"
            "Corrections:\n" + "\n".join(fix_parts)
        )

        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
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
            "Keep the same art style, background, color palette, and scene.\n"
            "Fix only character composition issues.\n"
            "Requirements:\n"
            "- Keep exactly one Kolla on the left and one Zero on the right.\n"
            "- Remove any extra duplicate characters, floating cut-in portraits, detached upper bodies, or cropped partial character copies.\n"
            "- Do not place any character in the upper sky area except the two main full-body characters in the main scene.\n"
            "- Keep Kolla slightly larger than Zero, about 10 to 15 percent bigger.\n"
            "- Do not add speech bubbles, captions, or UI elements.\n"
            "Specific fixes:\n"
            f"{check['edit_instruction']}"
        )
        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
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
    reference_image_paths: list[Path],
    max_attempts: int = 2,
) -> tuple[bytes, dict[str, Any]]:
    if not reference_image_paths:
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
    reference_parts = _build_reference_image_parts(reference_image_paths)

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
            "- Do not create extra copies, duplicate scenes, cut-in portraits, or multi-panel layouts.\n"
            "- Do not add speech bubbles, captions, or UI elements.\n"
            "Specific fixes:\n"
            f"{check['edit_instruction']}"
        )
        try:
            fixed_bytes, fixed_mime, _ = image_client.generate_image(
                edit_prompt,
                edit_image_bytes=current_bytes,
                edit_image_mime_type="image/png",
                reference_image_paths=reference_image_paths,
            )
            current_bytes = fixed_bytes if fixed_mime == "image/png" else convert_image_bytes(fixed_bytes, "PNG")
        except Exception as exc:
            logger.warning("캐릭터 참조 일관성 교정 실패 (시도 %d/%d): %s", attempt, max_attempts, exc)
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
    google_client = GoogleWorkspaceClient(settings)
    instagram_client = InstagramGraphClient(settings)

    brief = llm_client.build_creative_brief(topic)
    sanitized_topic = sanitize_public_text(topic, fallback=brief["title"])
    reference_files = list_character_reference_files(settings)

    thumbnail_image_text = ""
    thumbnail_base_png_bytes = b""
    thumbnail_character_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_reference_check: dict[str, Any] = {"has_issues": False, "issues": [], "edit_instruction": ""}
    thumbnail_bg_check: dict[str, Any] = {"has_errors": False, "background_texts": [], "corrections": []}
    thumbnail_prompt = ""
    thumbnail_retry_feedback = ""
    max_generation_attempts = max(2, settings.max_correction_attempts + 1)

    for generation_attempt in range(1, max_generation_attempts + 1):
        thumbnail_prompt = build_thumbnail_generation_prompt(
            brief["image_prompt"],
            title=brief["title"],
            topic=sanitized_topic,
            character_notes=brief.get("character_notes", ""),
        )
        if thumbnail_retry_feedback:
            thumbnail_prompt += (
                "\n\n재생성 보정 지시:\n"
                "- 아래 실패 사유를 반드시 반영해 처음부터 다시 생성한다.\n"
                f"{thumbnail_retry_feedback}"
            )
        thumbnail_image_bytes, thumbnail_mime_type, thumbnail_image_text = image_client.generate_image(
            thumbnail_prompt,
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
            max_attempts=max(1, settings.max_correction_attempts),
        )
        thumbnail_base_png_bytes, thumbnail_reference_check = correct_character_reference_consistency(
            image_client,
            ocr_client,
            thumbnail_base_png_bytes,
            thumbnail_prompt,
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
        if not quality_errors:
            thumbnail_base_png_bytes, thumbnail_bg_check = correct_background_text(
                image_client,
                ocr_client,
                thumbnail_base_png_bytes,
                thumbnail_prompt,
                max_attempts=max(1, settings.max_correction_attempts),
            )
            break
        thumbnail_retry_feedback = "\n".join(f"- {message}" for message in quality_errors)
        logger.warning(
            "썸네일 재생성 필요 (시도 %d/%d): %s",
            generation_attempt,
            max_generation_attempts,
            " | ".join(quality_errors),
        )
    else:
        raise RuntimeError(thumbnail_retry_feedback.replace("\n- ", " ").strip() or "썸네일 품질 게이트 실패")

    thumbnail_base_path = build_versioned_path(artifact_paths.run_dir, "thumbnail_base", 1, "png")
    save_image(thumbnail_base_png_bytes, thumbnail_base_path)
    thumbnail_render = render_thumbnail_card(
        thumbnail_base_png_bytes,
        title=brief["title"],
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
        }
    ]
    current_panels = [
        {
            "panel_no": int(panel.get("panel_no", index)),
            "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
            "dialogue_lines": [str(line).strip() for line in panel.get("dialogue_lines", []) if str(line).strip()],
        }
        for index, panel in enumerate(brief.get("panels", [])[:MAX_WEBTOON_PANEL_COUNT], start=1)
    ]

    def _generate_single_panel(panel: dict[str, Any]) -> dict[str, Any]:
        panel_reference_paths = [*reference_files, thumbnail_base_path]
        panel_reference_paths.extend(item["base_path"] for item in panel_base_items)
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
                brief.get("character_notes", ""),
                panel.get("dialogue_lines", []),
            )
            if panel_retry_feedback:
                panel_prompt += (
                    "\n\n재생성 보정 지시:\n"
                    "- 아래 실패 사유를 반드시 반영해 처음부터 다시 생성한다.\n"
                    f"{panel_retry_feedback}"
                )
            panel_image_bytes, panel_mime_type, panel_image_text = image_client.generate_image(
                panel_prompt,
                reference_image_paths=panel_reference_paths,
            )
            panel_base_png_bytes = (
                panel_image_bytes if panel_mime_type == "image/png" else convert_image_bytes(panel_image_bytes, "PNG")
            )
            panel_base_png_bytes, panel_character_check = correct_character_composition(
                image_client,
                ocr_client,
                panel_base_png_bytes,
                panel.get("scene_prompt", ""),
                max_attempts=max(1, settings.max_correction_attempts),
            )
            panel_base_png_bytes, panel_reference_check = correct_character_reference_consistency(
                image_client,
                ocr_client,
                panel_base_png_bytes,
                panel.get("scene_prompt", ""),
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
            if not quality_errors:
                panel_base_png_bytes, panel_bg_check = correct_background_text(
                    image_client,
                    ocr_client,
                    panel_base_png_bytes,
                    panel.get("scene_prompt", ""),
                    max_attempts=max(1, settings.max_correction_attempts),
                )
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
            raise RuntimeError(panel_retry_feedback.replace("\n- ", " ").strip() or f"패널 {panel.get('panel_no')} 품질 게이트 실패")

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

    for panel in current_panels:
        result = _generate_single_panel(panel)
        panel_base_items.append(result["item"])
        panel_generation_notes.append(result["note"])

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
            render_result = render_text_boxes(
                panel_base["base_bytes"],
                [panel],
                settings,
                render_version=image_version,
            )
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
            ocr_text = ocr_client.extract_text(bubble_ocr_image, mime_type="image/png")
            review = ocr_client.plan_text_corrections(panel.get("dialogue_lines", []), ocr_text)
            review["panel_no"] = panel_no
            review["source_image_version"] = image_version
            review["render_layout"] = render_result["layout"]

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
                "corrected_panel": {
                    **panel,
                    "dialogue_lines": corrected_lines or panel.get("dialogue_lines", []),
                },
                "rerender_required": review["rerender_required"],
            }

        with ThreadPoolExecutor(max_workers=3) as pool:
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
            "ocr_model": settings.ocr_model,
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

    now = datetime.now(timezone.utc).replace(microsecond=0)
    folder_chain = google_client.ensure_drive_path(f"{now.year}년", f"{now.month:02d}월", run_id)
    drive_folder = folder_chain[-1]

    uploaded_files: list[dict[str, Any]] = []
    uploaded_files_by_role: dict[str, Any] = {"thumbnail": {}, "panels": []}

    uploaded_thumbnail_base = google_client.upload_bytes(
        drive_folder["id"],
        thumbnail_base_path.name,
        thumbnail_base_png_bytes,
        "image/png",
        make_public=False,
    )
    uploaded_files_by_role["thumbnail"]["base_png"] = uploaded_thumbnail_base

    for slide in slide_outputs:
        uploaded_png = google_client.upload_bytes(
            drive_folder["id"],
            slide["final_path"].name,
            slide["png_bytes"],
            "image/png",
            make_public=False,
        )
        uploaded_jpg = google_client.upload_bytes(
            drive_folder["id"],
            slide["publish_path"].name,
            slide["jpg_bytes"],
            "image/jpeg",
            make_public=True,
        )
        uploaded_entry = {
            "slide_index": slide["slide_index"],
            "slide_type": slide["slide_type"],
            "panel_no": slide.get("panel_no"),
            "final_png": uploaded_png,
            "publish_jpg": uploaded_jpg,
        }
        uploaded_files.append(uploaded_entry)
        if slide["slide_type"] == "thumbnail":
            uploaded_files_by_role["thumbnail"]["final_png"] = uploaded_png
            uploaded_files_by_role["thumbnail"]["publish_jpg"] = uploaded_jpg
        else:
            uploaded_files_by_role["panels"].append(uploaded_entry)

    publish_result: dict[str, Any] | None = None
    status = "approved"
    posted_at = ""
    instagram_post_id = ""
    instagram_post_url = ""
    published_file_url = ""

    if publish:
        slide_urls = [entry["publish_jpg"]["public_download_url"] for entry in uploaded_files]
        full_caption = brief["caption"]
        if brief.get("hashtags"):
            full_caption += "\n\n" + " ".join(brief["hashtags"])
        publish_result = instagram_client.publish_carousel(
            image_urls=slide_urls,
            caption=full_caption,
        )
        status = "posted"
        posted_at = now.isoformat()
        media = publish_result.get("media", {})
        instagram_post_id = str(media.get("id", ""))
        instagram_post_url = str(media.get("permalink", ""))
        published_file_url = slide_urls[0] if slide_urls else ""

    metadata = {
        "run_id": run_id,
        "week_key": week_key,
        "topic": topic,
        "title": brief["title"],
        "caption": brief["caption"],
        "hashtags": brief["hashtags"],
        "image_prompt": brief["image_prompt"],
        "panels": current_panels,
        "character_notes": brief.get("character_notes", ""),
        "character_reference_files": [str(path.relative_to(settings.character_assets_dir.parent)) for path in reference_files],
        "llm_model": brief["model"],
        "image_model": settings.image_model,
        "image_notes": panel_generation_notes,
        "font_file": thumbnail_render["font_path"],
        "thumbnail_base_file": thumbnail_base_path.name,
        "thumbnail_final_file": thumbnail_final_path.name,
        "panel_base_files": [item["base_path"].name for item in panel_base_items],
        "ocr_model": settings.ocr_model,
        "ocr_result": aggregated_ocr_payload,
        "correction_model": settings.ocr_model,
        "correction_attempts": correction_reviews,
        "drive_folder": drive_folder,
        "uploaded_files": uploaded_files_by_role,
        "approved_image_version": image_version,
        "status": status,
        "approved_by": settings.approval_default_user,
        "approved_at": now.isoformat(),
        "publish_result": publish_result,
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

    google_client.upload_json(drive_folder["id"], "run_metadata.json", metadata)
    google_client.upload_json(drive_folder["id"], f"ocr_result_v{image_version}.json", aggregated_ocr_payload)

    for item in correction_payloads:
        google_client.upload_json(drive_folder["id"], item["filename"], item["payload"])

    if publish_result is not None:
        google_client.upload_json(drive_folder["id"], "publish_result_v1.json", publish_result)

    thumbnail_link = uploaded_files_by_role["thumbnail"]["final_png"].get("webViewLink", "")
    sheet_row = {
        "week_key": week_key,
        "run_id": run_id,
        "attempt_no": 1,
        "input_mode": "manual_topic",
        "generator_model": settings.image_model,
        "ocr_model": settings.ocr_model,
        "topic": topic,
        "caption": brief["caption"],
        "drive_folder_url": drive_folder.get("webViewLink", ""),
        "composited_image_file_url": thumbnail_link,
        "final_image_file_url": thumbnail_link,
        "is_active": "TRUE",
        "status": status,
        "approved_by": settings.approval_default_user,
        "approved_at": now.isoformat(),
        "approved_image_version": image_version,
        "instagram_post_id": instagram_post_id,
        "instagram_post_url": instagram_post_url,
        "published_file_url": published_file_url,
        "posted_at": posted_at,
        "last_updated_at": now.isoformat(),
        "notes": notes,
    }
    sheet_result = google_client.append_row("weekly_planning", WEEKLY_PLANNING_HEADERS, sheet_row)

    return {
        "run_id": run_id,
        "week_key": week_key,
        "status": status,
        "drive_folder": drive_folder,
        "sheet_append_result": sheet_result,
        "instagram": publish_result,
        "uploaded_files": uploaded_files_by_role,
        "character_reference_files": [str(path) for path in reference_files],
        "correction_attempts": correction_reviews,
        "slides": metadata["slides"],
    }
