from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import types
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload
from PIL import Image, ImageDraw

from .config import WebtoonSettings

logger = logging.getLogger(__name__)

BANNED_PUBLIC_TERM = "고양이"
PUBLIC_TEXT_REPLACEMENTS = (
    ("고양이 웹툰", "생활툰"),
    ("고양이웹툰", "생활툰"),
    ("고양이 툰", "생활툰"),
    ("고양이툰", "생활툰"),
    ("검은 고양이", "콜라"),
    ("검은고양이", "콜라"),
    ("회색 고양이", "제로"),
    ("회색고양이", "제로"),
    ("두 고양이", "둘"),
    ("고양이들", "둘"),
    ("고양이들의", "일상의"),
    ("고양이의", "캐릭터의"),
)
OCR_META_PREFIXES = (
    "이미지에 보이는 텍스트는",
    "다음과 같습니다",
    "ocr 결과",
    "텍스트 추출 결과",
)
_MIN_CONTEXT_CACHE_CHARS = 3000
DIALOGUE_RENDER_REPLACEMENTS = (
    ("…", "..."),
    ("⋯", "..."),
    ("“", '"'),
    ("”", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("\u00a0", " "),
    ("\u200b", ""),
)
SINGLE_LOCATION_SCOPE = "single_location"
MULTI_LOCATION_JOURNEY_SCOPE = "journey"
_SINGLE_LOCATION_SCOPE_ALIASES = {
    "single",
    "single_location",
    "single-location",
    "single_place",
    "single_scene",
    "single_situation",
    "one_location",
    "one_place",
}
_MULTI_LOCATION_SCOPE_ALIASES = {
    "journey",
    "multi",
    "multi_location",
    "multi-location",
    "multi_location_journey",
    "multi_scene",
    "multi-scene",
    "travel",
}
DEFAULT_PUBLIC_EPISODE_FALLBACK = "독일 생활 에피소드"


def _split_prop_candidates(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        flattened: list[str] = []
        for item in raw_value:
            flattened.extend(_split_prop_candidates(item))
        return flattened
    text = str(raw_value).strip()
    if not text:
        return []
    return [part for part in re.split(r"[,/\n|]+", text) if str(part).strip()]


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
    return merged[:6]


def _normalize_panel_props(raw_props: Any, *, fallback: list[str] | None = None) -> list[str]:
    normalized: list[str] = []
    for item in _split_prop_candidates(raw_props):
        text = re.sub(r"\s+", " ", str(item).strip())
        text = re.sub(r"^[0-9]+\.\s*", "", text)
        text = text.strip(" -*•")
        if text:
            normalized.append(text)
    merged = _merge_prop_lists(normalized)
    if merged:
        return merged
    return _merge_prop_lists(fallback or [])


def _extract_json_object(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("JSON 파싱 실패, 원본 텍스트 앞 200자: %s", cleaned[:200])
        raise ValueError(f"LLM 응답에서 유효한 JSON을 추출할 수 없습니다: {exc}") from exc


def _to_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _escape_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _extract_gemini_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()

    parts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            if getattr(part, "text", None):
                parts.append(str(part.text))
    return "\n".join(parts).strip()


def _collect_candidate_parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        parts: list[Any] = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content and getattr(content, "parts", None):
                parts.extend(content.parts)
        return parts
    return list(getattr(response, "parts", None) or [])


def _image_reference_budget(
    attempt: int,
    *,
    max_retries: int,
    total_references: int,
    has_edit_image: bool,
) -> int | None:
    if total_references <= 3:
        return None
    if attempt == 1:
        return None
    if attempt < max_retries:
        return 3
    return 2 if has_edit_image else 3


def _candidate_finish_reasons(response: Any) -> list[str]:
    summaries: list[str] = []
    for index, candidate in enumerate(getattr(response, "candidates", []) or []):
        finish_reason = getattr(candidate, "finish_reason", None)
        finish_message = getattr(candidate, "finish_message", None)
        if finish_reason is None and not finish_message:
            continue
        summaries.append(f"{index}:{finish_reason or 'unknown'}:{finish_message or ''}".rstrip(":"))
    return summaries


_THINKING_LEVELS = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}
_THINKING_BUDGETS = {
    "minimal": 0,
    "low": 512,
    "medium": 2048,
    "high": 8192,
}


def _normalize_thinking_level(value: str | None, *, default: str = "medium") -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in _THINKING_LEVELS else default


def _build_thinking_config(model_name: str, thinking_level: str | None) -> types.ThinkingConfig | None:
    if not thinking_level:
        return None
    normalized = _normalize_thinking_level(thinking_level)
    if model_name.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinkingBudget=_THINKING_BUDGETS[normalized])
    return types.ThinkingConfig(thinkingLevel=_THINKING_LEVELS[normalized])


def _build_generate_content_config(
    *,
    model_name: str,
    thinking_level: str | None,
    response_modalities: list[str] | None = None,
    system_instruction: str | None = None,
    cached_content: str | None = None,
) -> types.GenerateContentConfig:
    config_kwargs: dict[str, Any] = {}
    thinking_config = _build_thinking_config(model_name, thinking_level)
    if thinking_config is not None:
        config_kwargs["thinking_config"] = thinking_config
    if response_modalities:
        config_kwargs["response_modalities"] = response_modalities
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if cached_content:
        config_kwargs["cached_content"] = cached_content
    return types.GenerateContentConfig(**config_kwargs)


class _GeminiPromptCacheMixin:
    def _init_prompt_cache(self) -> None:
        self._cached_prompt_names: dict[str, str | None] = {}

    def _get_cached_content_name(
        self,
        *,
        model_name: str,
        cache_key: str | None,
        system_instruction: str | None,
    ) -> str | None:
        if not getattr(self.settings, "enable_context_caching", False):
            return None
        if not cache_key or not system_instruction or not system_instruction.strip():
            return None
        if len(system_instruction.strip()) < _MIN_CONTEXT_CACHE_CHARS:
            return None
        full_key = f"{model_name}:{cache_key}"
        if full_key in self._cached_prompt_names:
            return self._cached_prompt_names[full_key]
        try:
            cached = self.client.caches.create(
                model=model_name,
                config=types.CreateCachedContentConfig(
                    display_name=f"webtoon-{cache_key}",
                    system_instruction=system_instruction,
                    ttl=self.settings.context_cache_ttl,
                ),
            )
            cache_name = getattr(cached, "name", "") or None
        except Exception as exc:
            logger.warning("컨텍스트 캐시 생성 실패 (%s): %s", cache_key, exc)
            cache_name = None
        self._cached_prompt_names[full_key] = cache_name
        return cache_name


def sanitize_public_text(text: str, *, fallback: str = "") -> str:
    cleaned = str(text).strip()
    for before, after in PUBLIC_TEXT_REPLACEMENTS:
        cleaned = cleaned.replace(before, after)
    cleaned = cleaned.replace(BANNED_PUBLIC_TERM, "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?…:])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s+", "(", cleaned)
    cleaned = re.sub(r"\s+\)", ")", cleaned)
    cleaned = cleaned.strip(" ,")
    return cleaned or fallback


def sanitize_public_hashtags(raw_hashtags: list[Any]) -> list[str]:
    sanitized: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_hashtags:
        tag_text = sanitize_public_text(str(raw_tag).strip().lstrip("#"), fallback="")
        tag_text = re.sub(r"\s+", "", tag_text)
        if not tag_text:
            continue
        normalized_tag = f"#{tag_text}"
        if normalized_tag in seen:
            continue
        seen.add(normalized_tag)
        sanitized.append(normalized_tag)
    return sanitized


def clean_ocr_text(text: str) -> str:
    cleaned = str(text).strip()
    if not cleaned:
        return ""

    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
    else:
        lines = cleaned.splitlines()

    normalized_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            normalized_lines.append("")
            continue
        if line in {"---", "—", "```", "text", "json"}:
            continue
        lower_line = line.lower()
        if any(prefix in lower_line for prefix in OCR_META_PREFIXES):
            continue
        normalized_lines.append(line)

    collapsed: list[str] = []
    prev_blank = False
    for line in normalized_lines:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    return "\n".join(collapsed).strip()


def _normalize_background_correction_text(found: str, correct: str) -> str:
    cleaned = clean_ocr_text(correct)
    if not cleaned:
        return ""

    collapsed = re.sub(r"\s+", " ", cleaned).strip()
    lower_text = f"{clean_ocr_text(found)} {collapsed}".lower()
    needs_placeholder = (
        len(collapsed) > 48
        or collapsed.count(" ") >= 6
        or bool(re.search(r"[\[\]\(\)]", collapsed))
        or any(token in lower_text for token in ("placeholder", "unreadable", "gibberish", "bars/icons", "icons only", "blank bars"))
    )
    if not needs_placeholder:
        return collapsed

    if any(token in lower_text for token in ("willkommen", "welcome", "greeting")):
        return "WELCOME"
    if any(token in lower_text for token in ("ausgang", "exit", "entrance")):
        return "EXIT"
    if any(token in lower_text for token in ("gepäck", "baggage", "luggage")):
        return "BAGGAGE"
    if any(token in lower_text for token in ("ticket", "fahrschein", "boarding")):
        return "TICKET"
    if any(token in lower_text for token in ("arrival", "arrivals")):
        return "ARRIVALS"
    if any(token in lower_text for token in ("depart", "departure", "flight", "gate", "platform")):
        return "INFO"
    if any(token in lower_text for token in ("stamp", "stempel", "einreise", "entry stamp", "seal")):
        return "STAMP"
    if any(token in lower_text for token in ("passport", "reisepass", "visa", "document", "name", "id", "geburt", "dob", "form")):
        return "DOCUMENT"
    if any(token in lower_text for token in ("screen", "display", "board", "ui", "menu")):
        return "INFO"
    return "LABEL"


def normalize_dialogue_text(text: str) -> str:
    normalized = sanitize_public_text(text, fallback="")
    for before, after in DIALOGUE_RENDER_REPLACEMENTS:
        normalized = normalized.replace(before, after)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.strip()


def _normalize_thumbnail_title_pair(
    raw_title: Any,
    raw_subtitle: Any,
    *,
    topic: str,
    fallback_title: str,
) -> tuple[str, str]:
    title = sanitize_public_text(str(raw_title or "").strip(), fallback=fallback_title)
    subtitle = sanitize_public_text(str(raw_subtitle or "").strip(), fallback="")
    normalized_topic = sanitize_public_text(topic, fallback=fallback_title)

    if not subtitle:
        match = re.match(r"^(.{2,18}?[!?])\s+(.+)$", title)
        if match:
            title = match.group(1).strip()
            subtitle = sanitize_public_text(match.group(2).strip(), fallback="")
        else:
            for delimiter in (" - ", ": ", " | ", " / ", " · "):
                if delimiter in title:
                    first, second = title.split(delimiter, 1)
                    if first.strip() and second.strip():
                        title = sanitize_public_text(first.strip(), fallback=fallback_title)
                        subtitle = sanitize_public_text(second.strip(), fallback="")
                        break

    if not subtitle and len(title) > 16:
        words = [word for word in re.split(r"\s+", title) if word]
        if len(words) >= 2:
            head_words: list[str] = []
            tail_words: list[str] = []
            for index, word in enumerate(words):
                candidate = " ".join([*head_words, word]).strip()
                if len(candidate) <= 14 or not head_words:
                    head_words.append(word)
                    tail_words = words[index + 1 :]
                else:
                    tail_words = words[index:]
                    break
            if tail_words:
                title = sanitize_public_text(" ".join(head_words), fallback=fallback_title)
                subtitle = sanitize_public_text(" ".join(tail_words), fallback="")

    if not subtitle and normalized_topic and normalized_topic != title:
        subtitle = normalized_topic
    if subtitle == title:
        subtitle = normalized_topic if normalized_topic != title else ""
    return title, subtitle


def _derive_episode_scope_from_panels(panels: list[dict[str, Any]]) -> str:
    locations = [str(panel.get("location", "")).strip() for panel in panels if str(panel.get("location", "")).strip()]
    if not locations:
        return SINGLE_LOCATION_SCOPE

    unique_locations: list[str] = []
    for location in locations:
        if location not in unique_locations:
            unique_locations.append(location)

    location_transitions = sum(
        1 for previous, current in zip(locations, locations[1:]) if previous and current and previous != current
    )
    if len(unique_locations) >= 3 or location_transitions >= 2:
        return MULTI_LOCATION_JOURNEY_SCOPE
    return SINGLE_LOCATION_SCOPE


def _normalize_scope_value(raw_value: Any, *, fallback: str) -> str:
    normalized = str(raw_value or "").strip().lower().replace(" ", "_")
    if normalized in _SINGLE_LOCATION_SCOPE_ALIASES:
        return SINGLE_LOCATION_SCOPE
    if normalized in _MULTI_LOCATION_SCOPE_ALIASES:
        return MULTI_LOCATION_JOURNEY_SCOPE
    return fallback


def _normalize_episode_scope(value: Any, *, panels: list[dict[str, Any]] | None = None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "single": SINGLE_LOCATION_SCOPE,
        "single_scene": SINGLE_LOCATION_SCOPE,
        "single_location": SINGLE_LOCATION_SCOPE,
        "single_place": SINGLE_LOCATION_SCOPE,
        "single_site": SINGLE_LOCATION_SCOPE,
        "one_location": SINGLE_LOCATION_SCOPE,
        "journey": MULTI_LOCATION_JOURNEY_SCOPE,
        "travel": MULTI_LOCATION_JOURNEY_SCOPE,
        "multi_location": MULTI_LOCATION_JOURNEY_SCOPE,
        "multi_scene": MULTI_LOCATION_JOURNEY_SCOPE,
        "multiple_locations": MULTI_LOCATION_JOURNEY_SCOPE,
        "trip": MULTI_LOCATION_JOURNEY_SCOPE,
    }
    if normalized in mapping:
        return mapping[normalized]
    unique_locations = {
        str(panel.get("location", "")).strip()
        for panel in (panels or [])
        if str(panel.get("location", "")).strip()
    }
    return MULTI_LOCATION_JOURNEY_SCOPE if len(unique_locations) >= 3 else SINGLE_LOCATION_SCOPE


def _normalize_subtitle_scope(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "single": "single_location",
        "single_scene": "single_location",
        "single_location": "single_location",
        "single_place": "single_location",
        "single_site": "single_location",
        "one_location": "single_location",
        "journey": "journey",
        "travel": "journey",
        "multi_location": "journey",
        "multi_scene": "journey",
        "multiple_locations": "journey",
        "trip": "journey",
    }
    if normalized in mapping:
        return mapping[normalized]
    return fallback


def _normalize_speaker_name(value: Any, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"kolla", "cola", "black_cat", "black", "left"}:
        return "kolla"
    if normalized in {"zero", "gray_cat", "grey_cat", "gray", "grey", "right"}:
        return "zero"
    return default


def _split_lines_by_speaker(dialogue_lines: list[str]) -> list[dict[str, Any]]:
    cleaned = [normalize_dialogue_text(str(line).strip()) for line in dialogue_lines if str(line).strip()]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return [
            {"speaker": "kolla", "dialogue_lines": []},
            {"speaker": "zero", "dialogue_lines": []},
        ]
    if len(cleaned) == 1:
        return [
            {"speaker": "kolla", "dialogue_lines": [cleaned[0]]},
            {"speaker": "zero", "dialogue_lines": []},
        ]
    if len(cleaned) == 2:
        return [
            {"speaker": "kolla", "dialogue_lines": [cleaned[0]]},
            {"speaker": "zero", "dialogue_lines": [cleaned[1]]},
        ]
    if len(cleaned) == 3:
        return [
            {"speaker": "kolla", "dialogue_lines": cleaned[:2]},
            {"speaker": "zero", "dialogue_lines": [cleaned[2]]},
        ]
    return [
        {"speaker": "kolla", "dialogue_lines": cleaned[:2]},
        {"speaker": "zero", "dialogue_lines": cleaned[2:4]},
    ]


def normalize_speaker_dialogues(panel: dict[str, Any]) -> list[dict[str, Any]]:
    raw_blocks = panel.get("speaker_dialogues", [])
    if not isinstance(raw_blocks, list) or not raw_blocks:
        return _split_lines_by_speaker(panel.get("dialogue_lines", []))

    speaker_map = {
        "kolla": {"speaker": "kolla", "dialogue_lines": []},
        "zero": {"speaker": "zero", "dialogue_lines": []},
    }
    for fallback_speaker, raw_block in zip(("kolla", "zero"), raw_blocks, strict=False):
        if not isinstance(raw_block, dict):
            continue
        speaker = _normalize_speaker_name(raw_block.get("speaker"), fallback_speaker)
        lines = [
            normalize_dialogue_text(str(line).strip())
            for line in raw_block.get("dialogue_lines", raw_block.get("lines", []))
            if str(line).strip()
        ]
        speaker_map[speaker] = {
            "speaker": speaker,
            "dialogue_lines": [line for line in lines if line][:2],
        }

    if not speaker_map["kolla"]["dialogue_lines"] and not speaker_map["zero"]["dialogue_lines"]:
        return _split_lines_by_speaker(panel.get("dialogue_lines", []))
    return [speaker_map["kolla"], speaker_map["zero"]]


def flatten_speaker_dialogues(blocks: list[dict[str, Any]]) -> list[str]:
    flattened: list[str] = []
    for block in blocks:
        for line in block.get("dialogue_lines", []):
            stripped = normalize_dialogue_text(str(line).strip())
            if stripped:
                flattened.append(stripped)
    return flattened


_LLM_TIMEOUT = 60_000      # 60초 (밀리초 단위)
_IMAGE_TIMEOUT = 180_000   # 180초 (밀리초 단위)

# Gemini API 호출 간 최소 간격 (초) — 503 폭주 방지
_MIN_API_INTERVAL = 1.5
_last_api_call_lock = threading.Lock()
_last_api_call_time: float = 0.0


def _throttle_api_call() -> None:
    """API 호출 전에 최소 간격을 보장한다."""
    global _last_api_call_time
    with _last_api_call_lock:
        now = time.monotonic()
        elapsed = now - _last_api_call_time
        if elapsed < _MIN_API_INTERVAL:
            time.sleep(_MIN_API_INTERVAL - elapsed)
        _last_api_call_time = time.monotonic()


def _is_server_overload(exc: Exception) -> bool:
    """503/429/UNAVAILABLE 등 서버 과부하 에러인지 판별."""
    msg = str(exc)
    return any(keyword in msg for keyword in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"))


def _backoff_seconds(attempt: int, *, is_overload: bool) -> float:
    """503이면 긴 백오프 + 지터, 그 외에는 짧은 백오프."""
    if is_overload:
        base = min(15 * (2 ** (attempt - 1)), 90)  # 15, 30, 60, 90, 90...
        jitter = random.uniform(0, base * 0.3)
        return base + jitter
    base = min(2 ** attempt, 30)
    return base + random.uniform(0, 2)


class GeminiTextClient(_GeminiPromptCacheMixin):
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=_LLM_TIMEOUT),
        )
        self._init_prompt_cache()

    def _generate_text(
        self,
        prompt: str,
        *,
        max_retries: int = 5,
        thinking_level: str | None = None,
        system_instruction: str | None = None,
        cache_key: str | None = None,
    ) -> str:
        model_name = self.settings.llm_model
        resolved_thinking_level = thinking_level or self.settings.llm_thinking_level
        cached_content = self._get_cached_content_name(
            model_name=model_name,
            cache_key=cache_key,
            system_instruction=system_instruction,
        )
        for attempt in range(1, max_retries + 1):
            _throttle_api_call()
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=_build_generate_content_config(
                        model_name=model_name,
                        thinking_level=resolved_thinking_level,
                        system_instruction=None if cached_content else system_instruction,
                        cached_content=cached_content,
                    ),
                )
                text = _extract_gemini_text(response)
                if not text:
                    raise RuntimeError("Gemini text response did not contain text output")
                return text
            except Exception as exc:
                if attempt >= max_retries:
                    raise
                overload = _is_server_overload(exc)
                wait = _backoff_seconds(attempt, is_overload=overload)
                logger.warning("LLM 텍스트 생성 실패 (시도 %d/%d, 대기 %.0f초): %s", attempt, max_retries, wait, exc)
                time.sleep(wait)
        raise RuntimeError("Unreachable")

    def smoke_test(self) -> dict[str, Any]:
        return {
            "model": self.settings.llm_model,
            "response_text": self._generate_text(
                "웹툰 에이전트 연결 테스트입니다. 정확히 'LLM_OK'만 출력하세요.",
                thinking_level="minimal",
            ),
        }

    def build_creative_brief(self, topic: str) -> dict[str, Any]:
        system_instruction = (
            "당신은 생활형 에피소드 웹툰 기획자입니다.\n"
            "주어진 주제로 6컷 웹툰용 장면 계획과 인스타그램 캡션을 만듭니다.\n"
            "응답은 JSON 객체만 반환하세요.\n\n"
            "## 캐릭터 설정 (절대 변경 금지)\n"
            "- 콜라(검은 캐릭터): 의인화된 캐릭터. 두 발로 서서 걷고, 앞발을 손처럼 사용한다. "
            "자신감 있고 허세가 있지만 결국 허당인 성격. 노란 눈, 검은 단색 털.\n"
            "- 제로(회색 줄무늬 캐릭터): 의인화된 캐릭터. 두 발로 서서 걷고, 앞발을 손처럼 사용한다. "
            "조심스럽고 걱정이 많지만 실행력이 있는 성격. 갈색 눈, 회색 바탕에 검은 줄무늬, 분홍 귀.\n"
            "- 두 캐릭터 모두 사람처럼 이족보행하며, 물건을 잡고, 기계를 조작하고, 제스처를 취한다.\n"
            "- 캐릭터 체형은 유지하되 행동과 포즈는 완전히 사람처럼 표현한다.\n"
            "- 캐릭터는 절대 옷을 입지 않는다. 항상 자연스러운 털 그대로의 모습이다.\n"
            "- 콜라는 항상 화면 왼쪽, 제로는 항상 화면 오른쪽에 배치한다.\n"
            "- 콜라의 전신 크기는 제로보다 항상 약간 더 크게 보이게 유지한다. 대략 10~15% 크게 표현한다.\n"
            "- 이 크기 서열은 썸네일부터 패널 6까지 모든 이미지에서 예외 없이 동일해야 합니다. 어느 컷에서도 제로가 더 크거나 같게 읽히면 실패입니다.\n"
            "- 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지하세요. 어떤 컷에서는 10~15% 차이였는데 다른 컷에서 콜라가 갑자기 지나치게 커지거나 제로가 지나치게 작아지면 실패입니다.\n"
            "- 원근, 카메라 거리, 포즈가 달라져도 콜라가 더 크게 읽혀야 합니다. 필요하면 콜라를 더 가깝게 두거나 제로를 더 뒤에 배치하세요.\n"
            "- 한 장면의 주연 캐릭터는 반드시 이 둘뿐입니다. 추가 고양이형 분신, 실루엣, 잘린 얼굴 스티커, 떠 있는 두상은 만들지 마세요.\n"
            "- 장면의 공간감을 보여주기 위한 비주요 인간 배경 인물이나 대기 줄은 필요할 때만 허용됩니다.\n\n"
            "## 아트 스타일\n"
            "- 한국식 디지털 웹툰 스타일 (네이버/카카오 웹툰 느낌)\n"
            "- 굵고 깔끔한 외곽선, 생동감 있는 색상, 과장된 표정과 역동적 포즈\n"
            "- 만화적 이펙트 사용 (놀람 표시, 땀방울, 분노 마크, 반짝임 등)\n"
            "- 배경은 주제와 장면 전개에 맞는 생활 공간을 디테일하게 묘사\n\n"
            "## 썸네일 규칙\n"
            "- title은 썸네일 주제목이다. 4~12자 안팎의 짧고 강한 한 문구로 작성하세요.\n"
            "- thumbnail_subtitle은 썸네일 부제목이다. 주제목을 풀어 설명하는 10~24자 안팎의 짧은 보조 문구로 작성하세요.\n"
            "- title과 thumbnail_subtitle은 같은 문장을 반복하면 안 됩니다. title은 짧게, subtitle은 설명형으로 구분하세요.\n"
            "- thumbnail_scene_prompt 필드에 표지의 핵심 장면을 한국어로 구체적으로 작성하세요.\n"
            "- 표지는 어떤 주제든 출발 사건, 핵심 장소, 핵심 행동을 즉시 보여줘야 합니다.\n"
            "- 표지가 본문과 무관한 예쁜 골목 풍경으로 흐르면 안 됩니다. 주제의 대표 장소와 행동이 반드시 드러나야 합니다.\n"
            "- 썸네일 배경은 주제를 한눈에 설명하는 포괄적 대표 배경이어야 합니다. 에피소드 전체가 어디서 어떤 사건으로 시작되고 전개되는지 즉시 읽혀야 합니다.\n"
            "- 썸네일은 특정 패널 하나의 국소 장면이 아니라, 본문 6컷보다 상위 개념의 대표 공간과 대표 사건을 압축한 establishing shot이어야 합니다.\n"
            "- 표지는 본문 6컷 중 어느 한 컷의 배경과 장면을 그대로 복제하면 안 됩니다. 본문 컷과 다른 대표 티저 장면이어야 합니다.\n"
            "- 썸네일은 본문 1~6컷 중 어느 한 컷의 장소, 구도, 순간을 그대로 재사용하면 안 됩니다.\n"
            "- 썸네일은 패널 1의 scene_prompt를 축약하거나 복사한 장면이면 안 됩니다.\n"
            "- thumbnail_scene_prompt는 모든 panel의 location 및 scene_prompt와 구별되는 별도의 teaser shot이어야 합니다.\n"
            "- 썸네일은 에피소드 전체를 대표하는 예고 컷이어야 하며, 본문 컷 하나를 확대하거나 재연한 이미지여서는 안 됩니다.\n"
            "- 썸네일과 panel 1은 캐릭터 포즈, 시선 방향, 손동작, 소품 배치까지 비슷하면 안 됩니다. 같은 캐리어 배치나 같은 나란히 서기 구도는 피하세요.\n"
            "- 본문 1~6컷 어느 곳에서도 썸네일의 배경 구조, 서브로케이션, 카메라 거리, 대표 간판, 대표 소품 배치를 다시 사용하면 안 됩니다. 썸네일 배경이 본문에 재등장하면 실패입니다.\n"
            "- 썸네일과 본문은 구조적 배경 지문도 달라야 합니다. 천장 형태, 중앙 홀 구조, 복도 깊이, 출구 프레임, 카운터 구조, 바닥 패턴, 대표 간판 군집이 본문 어느 컷과 겹치면 실패입니다.\n"
            "- journey형 에피소드라면 썸네일은 특정 패널 장소의 넓은 버전이면 안 됩니다. 전체 여정을 소개하는 별도 출발 장면이나 전환 장면이어야 합니다.\n"
            "- 표지 상단이나 구석에 추가 캐릭터 실루엣, 뒷모습, 참조용 분신처럼 보이는 장식을 절대 넣지 마세요.\n\n"
            "## 범위 일치 규칙\n"
            "- 먼저 이 에피소드가 하나의 좁은 현장/상황 안에서 끝나는 이야기인지, 여러 장소를 거치는 여정형 이야기인지 결정하세요.\n"
            '- episode_scope 필드에는 반드시 "single_location" 또는 "journey" 중 하나만 넣으세요.\n'
            '- subtitle_scope 필드에도 반드시 "single_location" 또는 "journey" 중 하나만 넣으세요.\n'
            "- scope_summary 필드에는 title, thumbnail_subtitle, caption, thumbnail_scene_prompt, panels가 함께 약속하는 이야기 범위를 한국어 한 문장으로 요약하세요.\n"
            "- subtitle_scope는 thumbnail_subtitle이 약속하는 범위를 직접 표현해야 하며, episode_scope와 실제 패널 전개와 모순되면 안 됩니다.\n"
            "- title, thumbnail_subtitle, thumbnail_scene_prompt, panels의 location/scene_prompt는 모두 같은 에피소드 범위를 약속해야 합니다.\n"
            "- caption도 title, thumbnail_subtitle, scope_summary와 같은 범위를 설명해야 합니다. 본문보다 더 넓거나 다른 에피소드처럼 쓰면 안 됩니다.\n"
            "- thumbnail_subtitle이 특정 현장, 특정 장소, 특정 상황을 강조하면 6컷 대부분과 결말도 그 범위 안에서 전개되어야 합니다.\n"
            "- panels가 여러 장소를 이동하는 여정형 전개라면 thumbnail_subtitle도 그 이동 전체를 포괄하는 넓은 표현이어야 합니다. 단일 현장만 강조하는 부제목을 쓰면 안 됩니다.\n"
            "- 배경 전환은 허용되지만, subtitle이 약속한 범위를 벗어나는 다른 에피소드처럼 보이면 안 됩니다.\n\n"
            "## 텍스트 규칙 (매우 중요)\n"
            "- title, caption, hashtags, dialogue_lines 어디에서든 '고양이'라는 단어를 절대 사용하지 마세요.\n"
            "- 등장인물은 사람을 대체하는 캐릭터이므로 '고양이'를 직접 언급하면 안 됩니다.\n"
            "- 예: '고양이들의 마트 적응기' (X) → '마트 적응기!' (O)\n"
            "- 예: '고양이 웹툰' (X) → '생활 에피소드' (O)\n\n"
            "## scene_prompt 작성 규칙\n"
            "- scene_prompt는 이미지 생성 AI에게 전달되는 영문 프롬프트이다.\n"
            "- 반드시 아래 스타일 지시를 scene_prompt 앞에 포함하세요:\n"
            '  "Korean digital webtoon style, bold clean outlines, vibrant colors, '
            "expressive exaggerated facial expressions, dynamic poses, "
            "anthropomorphic cats walking upright on two legs using front paws as hands, "
            'manga-style emotion effects, detailed background"\n'
            "- 캐릭터 묘사 시 반드시 '의인화(anthropomorphic)', '이족보행(walking upright on two legs)', "
            "'앞발을 손처럼 사용(using front paws as hands)'을 포함하세요.\n"
            "- 콜라는 'a black cat character (Kolla) on the LEFT side', 제로는 'a gray tabby cat character with dark stripes (Zero) on the RIGHT side'로 묘사하세요.\n"
            "- scene_prompt에 'Kolla should appear slightly larger than Zero, about 10 to 15 percent bigger in body scale'를 포함하세요.\n"
            "- 모든 scene_prompt와 thumbnail_scene_prompt에서 'Kolla must remain larger than Zero in every shot, with larger head, torso, and full-body silhouette and no perspective exception' 규칙을 유지하세요.\n"
            "- 또한 모든 scene_prompt와 thumbnail_scene_prompt에서 'Keep the Kolla-to-Zero size ratio stable across the whole episode; do not suddenly enlarge Kolla or shrink Zero beyond the same 10 to 15 percent band' 규칙을 유지하세요.\n"
            "- scene_prompt에 'exactly two recurring cat protagonists only, no extra cat duplicates, no extra heads, no duplicate faces, no sticker portraits, no costume changes; minor background humans are allowed only when the location naturally requires them'를 포함하세요.\n"
            "- scene_prompt에 'strictly upright bipedal posture, upright pelvis and torso, forepaws used as hands only, no weight-bearing forepaws, never on all fours'를 포함하세요.\n"
            "- 썸네일 포함 모든 장면에서 네 발 보행, 앞발 체중 지지, 일반 동물형 달리기, 바닥을 짚는 자세가 한 번이라도 나오면 실패로 간주하세요.\n"
            "- scene_prompt에 캐릭터가 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단처럼 사람이 올라서면 안 되는 표면 위에 서지 않는다는 뜻을 명시하세요. 이런 물체는 배경 소품이고, 캐릭터는 주변의 정상적인 바닥, 계단, 좌석, 플랫폼 면 위에 있어야 합니다.\n"
            "- 간판, 안내판, 디지털 화면, 메뉴판, 라벨처럼 텍스트가 많은 배경 요소는 글자를 과하게 넣지 마세요. 꼭 필요하면 1~3개의 짧고 정확한 실재 단어만 사용하고, 나머지는 색 막대/아이콘/선으로 단순화하세요.\n"
            "- 철자를 자신 있게 정확히 쓸 수 없는 배경 텍스트는 아예 쓰지 말고, 장면 핵심 라벨 1개만 남기고 나머지는 bars/icons/blank lines로 처리하도록 scene_prompt를 작성하세요.\n"
            "- 장소 종류와 무관하게 텍스트가 많은 배경 요소는 여러 줄 미세문구 대신 큰 라벨 1개 또는 매우 짧은 2단어 라벨만 허용하세요.\n"
            "- 의미 없는 알파벳 덩어리, 모자이크 같은 깨진 텍스트, 읽을 수 없는 과도한 미세 글자를 유도하는 scene_prompt는 작성하지 마세요.\n"
            "- scene_prompt에 반드시 'The characters do NOT wear any clothing, shoes, or accessories. They have natural fur only.'를 포함하세요.\n"
            "- scene_prompt에 반드시 'Do NOT draw any speech bubbles, dialogue text, or captions in the image.'를 포함하세요.\n"
            "- 독일어 배경 텍스트가 필요하면 실존 브랜드명만 정확한 철자로 사용하세요.\n\n"
            "## dialogue_lines 규칙\n"
            "- 후처리 렌더링용 정확한 말풍선 대사 텍스트이며 반드시 한국어로만 작성하세요.\n"
            "- 각 panel에는 speaker_dialogues 배열을 넣으세요. 정확히 2개 항목만 허용합니다: 첫 번째는 콜라, 두 번째는 제로.\n"
            '- 각 항목 형식: {"speaker": "kolla"|"zero", "dialogue_lines": ["..."]}\n'
            "- 각 캐릭터는 최소 1줄, 최대 2줄만 말하게 하세요.\n"
            "- 전체 대사는 2~4줄 이내여야 합니다.\n"
            "- 한 줄 길이는 12~20자가 자연스럽고, 꼭 필요할 때만 조금 더 길게.\n"
            "- 독일어는 dialogue_lines에 넣지 마세요.\n"
            "- '고양이'라는 단어를 대사에서도 사용하지 마세요.\n"
            "- 매우 중요: 콜라 대사는 항상 콜라 항목 안에만, 제로 대사는 항상 제로 항목 안에만 넣으세요. 서로 섞지 마세요.\n"
            "- 이 speaker_dialogues 구조는 말풍선 좌우 배치와 직접 연결됩니다. 콜라는 왼쪽, 제로는 오른쪽입니다.\n"
            "- 말투: 콜라와 제로는 친한 친구 사이이므로 반드시 반말을 사용하세요. "
            "존댓말(~요, ~습니다, ~세요 등)은 절대 사용하지 마세요. "
            "예: '이거 봐봐!', '뭐야 이게...', '야 이리 와봐', '아 진짜?', '대박이다!'\n"
            "- 대사의 감정 톤은 해당 패널의 장면 분위기와 일치해야 합니다.\n\n"
            "## 6컷 구성 (기승전결 흐름)\n"
            "- 1컷(기): 상황 제시 — 호기심이나 도전의 시작. 배경과 상황 소개.\n"
            "- 2컷(승): 첫 번째 시도 — 본격적으로 행동에 나서는 장면.\n"
            "- 3컷(전): 문제 발생/당황 — 예상치 못한 장애물이나 실수.\n"
            "- 4컷(전): 갈등 고조/고군분투 — 문제가 심화되거나 좌충우돌.\n"
            "- 5컷(결): 해결/반전 — 돌파구를 찾거나 뜻밖의 전환.\n"
            "- 6컷(결): 확실한 마무리 — 이야기가 완전히 완결되는 장면. "
            "단순히 여운을 남기는 것이 아니라, 상황이 해결되고 두 캐릭터가 "
            "교훈이나 감상을 주고받으며 에피소드가 깔끔하게 끝나야 합니다.\n\n"
            "## 패널 장면 다양성 규칙\n"
            "- 각 패널에는 story_role과 location 필드를 반드시 넣으세요.\n"
            "- location은 이번 컷의 핵심 배경 장소를 한국어 명사구로 구체적으로 적습니다. 주제에 따라 집, 거리, 마트, 학교, 공공기관, 교통수단, 회사, 관공서 등 실제 공간을 분명히 적으세요.\n"
            "- 배경이 같은 장소로 유지되는 컷은 최대 2컷까지만 허용합니다.\n"
            "- 이전 컷과 장소가 바뀌면 scene_prompt와 location에서 배경 변화가 명확히 드러나야 합니다.\n"
            "- 각 scene_prompt에는 장소를 바꿔야 할 때 반드시 새로운 배경 요소를 직접 적으세요.\n"
            "- 각 패널에는 key_props 배열과 carryover_props 배열을 반드시 넣으세요.\n"
            "- key_props는 현재 컷에서 실제로 눈에 보여야 하는 핵심 소품 목록입니다. 휴대품, 문서, 도구, 음식, 탈것 관련 오브젝트처럼 서사에 중요한 것만 0~4개 적으세요.\n"
            "- carryover_props는 이전 컷에서 이번 컷까지 같은 물건으로 계속 이어져야 하는 소품의 부분집합입니다. 없으면 빈 배열로 두세요.\n"
            "- 같은 물건이 이어지면 carryover_props에서는 이전 컷과 정확히 같은 이름을 반복해 쓰세요. 동의어 치환이나 포괄어 변경으로 얼버무리지 마세요.\n"
            "- carryover_props에 적은 소품은 반드시 key_props에도 다시 포함하세요.\n"
            "- 특정 주제 예시를 하드코딩하지 말고, 그 패널에서 실제로 중요한 소품 이름만 적으세요.\n"
            "- 내용 흐름상 이어지는 핵심 소품(예: 여권, 티켓, 캐리어, 가방, 지도, 휴대폰, 문서철)은 다음 컷에서도 같은 소품으로 유지하세요. 같은 흐름인데 갑자기 다른 물건으로 바꾸면 실패입니다.\n"
            "- 소품이 계속 이어져야 하는 컷이라면 scene_prompt에 그 연속 소품을 다시 직접 적으세요. 종류와 주된 색상, 손에 든 상태가 바뀌면 안 됩니다.\n"
            "- panel 1은 thumbnail_scene_prompt와 같은 장소, 같은 행동 순간, 같은 카메라 구도를 사용하면 안 됩니다.\n"
            "- 모든 panel은 thumbnail_scene_prompt와 배경 장소 또는 사건 시점이 명확히 구별되어야 합니다.\n\n"
            "panels는 정확히 6개여야 합니다.\n"
            "각 panel에는 panel_no, story_role, location, scene_prompt, key_props, carryover_props, speaker_dialogues를 넣으세요.\n"
            "해시태그는 문자열 배열로 작성하되, '고양이'라는 단어를 포함하지 마세요.\n"
            '필드: "title", "thumbnail_subtitle", "episode_scope", "subtitle_scope", "scope_summary", "image_prompt", "thumbnail_scene_prompt", "caption", "hashtags", "character_notes", "panels"\n'
        )
        payload = self._normalize_brief_payload(
            topic,
            _extract_json_object(
                self._generate_text(
                    f"주제: {topic}",
                    thinking_level="high",
                    system_instruction=system_instruction,
                    cache_key="creative-brief-v2",
                )
            ),
        )
        review: dict[str, Any] = {"has_issues": False, "issues": [], "rewrite_instruction": ""}
        for attempt in range(1, 3):
            review = self._review_creative_brief(topic, payload)
            if not review["has_issues"]:
                break
            logger.warning("스토리 기획 보정 필요 (시도 %d/2): %s", attempt, " | ".join(review["issues"]))
            try:
                payload = self._rewrite_creative_brief(topic, payload, review)
            except Exception as exc:
                logger.warning("스토리 기획 재작성 실패, 현재 기획 유지: %s", exc)
                break
        payload["story_review"] = review
        return payload

    def _normalize_brief_payload(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_payload = dict(payload)
        normalized_payload["model"] = self.settings.llm_model
        sanitized_topic = sanitize_public_text(topic, fallback=DEFAULT_PUBLIC_EPISODE_FALLBACK)
        thumbnail_title, thumbnail_subtitle = _normalize_thumbnail_title_pair(
            normalized_payload.get("title", ""),
            normalized_payload.get("thumbnail_subtitle", fallback.get("thumbnail_subtitle", "") if fallback else ""),
            topic=sanitized_topic,
            fallback_title=sanitized_topic or DEFAULT_PUBLIC_EPISODE_FALLBACK,
        )
        normalized_payload["title"] = thumbnail_title
        normalized_payload["thumbnail_subtitle"] = thumbnail_subtitle
        normalized_payload["caption"] = sanitize_public_text(
            str(normalized_payload.get("caption", "")).strip(),
            fallback=f"{normalized_payload['title']} 에피소드를 준비했습니다.",
        )
        normalized_payload["hashtags"] = sanitize_public_hashtags(normalized_payload.get("hashtags", []))
        normalized_payload["image_prompt"] = str(normalized_payload.get("image_prompt", "")).strip() or (
            "Korean digital webtoon style, bold clean outlines, vibrant colors, "
            "expressive exaggerated facial expressions, dynamic poses, "
            "anthropomorphic cats walking upright on two legs using front paws as hands, "
            "manga-style emotion effects, detailed background"
        )
        normalized_payload["thumbnail_scene_prompt"] = str(normalized_payload.get("thumbnail_scene_prompt", "")).strip()
        normalized_payload["character_notes"] = str(normalized_payload.get("character_notes", "")).strip()
        raw_panels = normalized_payload.get("panels", fallback.get("panels", []) if fallback else [])
        fallback_panels = fallback.get("panels", []) if fallback else []
        if len(raw_panels) > 6:
            logger.warning("LLM이 %d개 패널을 반환했습니다. 처음 6개만 사용합니다.", len(raw_panels))
        panels: list[dict[str, Any]] = []
        for index, panel in enumerate(raw_panels[:6], start=1):
            fallback_panel = fallback_panels[index - 1] if index - 1 < len(fallback_panels) else {}
            speaker_dialogues = normalize_speaker_dialogues(panel)
            dialogue_lines = flatten_speaker_dialogues(speaker_dialogues)
            dialogue_lines = [line for line in dialogue_lines if line]
            for line in dialogue_lines:
                if len(line) > 40:
                    logger.warning("패널 %d 대사가 40자 초과 (%d자): %s", index, len(line), line[:50])
            fallback_key_props = _normalize_panel_props(fallback_panel.get("key_props", []))
            fallback_carryover_props = _normalize_panel_props(fallback_panel.get("carryover_props", []))
            key_props = _normalize_panel_props(panel.get("key_props", []), fallback=fallback_key_props)
            carryover_props = _normalize_panel_props(
                panel.get("carryover_props", []),
                fallback=fallback_carryover_props,
            )
            carryover_props = _merge_prop_lists(carryover_props)
            key_props = _merge_prop_lists(key_props, carryover_props)
            panels.append(
                {
                    "panel_no": int(panel.get("panel_no", index)),
                    "story_role": str(panel.get("story_role", "")).strip(),
                    "location": str(panel.get("location", "")).strip(),
                    "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
                    "key_props": key_props,
                    "carryover_props": carryover_props,
                    "speaker_dialogues": speaker_dialogues,
                    "dialogue_lines": dialogue_lines,
                }
            )
        while len(panels) < 6:
            logger.warning("LLM이 6개 미만의 패널을 반환하여 빈 패널을 추가합니다. (현재 %d개)", len(panels))
            panels.append(
                {
                    "panel_no": len(panels) + 1,
                    "story_role": "",
                    "location": "",
                    "scene_prompt": "",
                    "key_props": [],
                    "carryover_props": [],
                    "speaker_dialogues": [],
                    "dialogue_lines": [],
                }
            )
        normalized_payload["episode_scope"] = _normalize_episode_scope(
            normalized_payload.get("episode_scope", fallback.get("episode_scope", "") if fallback else ""),
            panels=panels,
        )
        normalized_payload["subtitle_scope"] = _normalize_subtitle_scope(
            normalized_payload.get("subtitle_scope", fallback.get("subtitle_scope", "") if fallback else ""),
            fallback=normalized_payload["episode_scope"],
        )
        raw_scope_summary = str(
            normalized_payload.get("scope_summary", fallback.get("scope_summary", "") if fallback else "")
        ).strip()
        if not raw_scope_summary:
            first_location = next((str(panel.get("location", "")).strip() for panel in panels if str(panel.get("location", "")).strip()), "")
            if normalized_payload["episode_scope"] == "single_location" and first_location:
                raw_scope_summary = f"{first_location} 안에서 시작부터 마무리까지 이어지는 에피소드"
            elif normalized_payload["thumbnail_subtitle"]:
                raw_scope_summary = normalized_payload["thumbnail_subtitle"]
            else:
                raw_scope_summary = sanitized_topic
        normalized_payload["scope_summary"] = sanitize_public_text(raw_scope_summary, fallback=sanitized_topic)
        normalized_payload["panels"] = self._normalize_korean_dialogues(panels)
        return normalized_payload

    def _review_creative_brief(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        system_instruction = (
            "당신은 6컷 웹툰 기획안을 검수하는 스토리 에디터입니다.\n"
            "주제와 기획안 JSON을 보고, 어떤 주제든 다음 기준으로만 판단하세요.\n"
            "- 썸네일은 주제의 출발 사건과 핵심 장소를 직접 보여줘야 합니다. 단순히 예쁜 일반 풍경이면 실패입니다.\n"
            "- 썸네일은 주제를 대표하는 포괄적 배경이어야 합니다. 에피소드 전체의 대표 공간과 대표 사건이 한눈에 보여야 하며, 특정 패널 하나의 국소 장면처럼 보이면 실패입니다.\n"
            "- thumbnail_scene_prompt가 패널 1 또는 다른 패널의 location/scene_prompt와 사실상 같은 장면이면 실패입니다. 썸네일은 패널 재사용이 아니라 별도의 대표 장면이어야 합니다.\n"
            "- 썸네일이 panel 1 또는 다른 panel과 같은 장소, 같은 구도, 같은 사건 순간이면 실패입니다.\n"
            "- 썸네일이 본문 컷의 복사본, 확대본, 넓게 잡은 변형판처럼 보이면 실패입니다.\n"
            "- 썸네일의 배경 구조, 서브로케이션, 대표 간판, 대표 소품 배치가 본문 1~6컷 어디에서라도 다시 등장하면 실패입니다.\n"
            "- 썸네일과 본문이 같은 구조적 지문을 공유하면 실패입니다. 천장 형태, 중앙 홀 구조, 복도/게이트 프레임, 카운터 타입, 바닥 패턴, 대표 간판 군집이 겹치면 중복입니다.\n"
            "- 썸네일과 panel 1의 캐릭터 포즈, 소품 배치, 시선 방향이 비슷하면 실패입니다.\n"
            "- title이 길어서 설명문처럼 늘어지면 실패입니다. title은 짧은 핵심 문구여야 합니다.\n"
            "- thumbnail_subtitle은 title을 반복하지 말고, title 아래에 붙는 설명형 보조 문구여야 합니다.\n"
            '- episode_scope는 "single_location" 또는 "journey" 중 하나로 명확해야 합니다.\n'
            '- subtitle_scope도 "single_location" 또는 "journey" 중 하나로 명확해야 합니다.\n'
            "- scope_summary는 title, thumbnail_subtitle, caption, panels가 공통으로 약속하는 범위를 한 문장으로 설명해야 합니다.\n"
            "- subtitle_scope는 thumbnail_subtitle이 약속하는 범위를, episode_scope는 실제 패널 location 전개를 설명해야 합니다.\n"
            "- episode_scope와 subtitle_scope가 서로 다르거나 실제 전개/부제목과 어긋나면 실패입니다.\n"
            "- title, thumbnail_subtitle, thumbnail_scene_prompt, panels의 location/scene_prompt가 같은 에피소드 범위를 가리켜야 합니다.\n"
            "- caption이 subtitle이나 scope_summary보다 더 넓거나 다른 에피소드처럼 보이면 실패입니다.\n"
            "- thumbnail_subtitle이 특정 장소/현장/상황을 약속하면 패널 전개와 결말도 그 범위 안에 머물러야 합니다.\n"
            "- 반대로 panels가 여러 장소를 이동하는 여정형 이야기라면 thumbnail_subtitle도 그 전체를 포괄하는 넓은 표현이어야 합니다. 단일 장소만 강조하면 실패입니다.\n"
            "- 1~6컷은 기, 승, 전, 전, 결, 결의 진행이 명확해야 합니다.\n"
            "- 6컷 안에서 문제 제기와 해결이 모두 끝나야 합니다. 마지막 컷은 확실한 마무리여야 합니다.\n"
            "- location과 scene_prompt는 배경 전환이 필요한 컷에서 충분히 달라야 합니다.\n"
            "- 같은 장소가 너무 오래 반복되면 실패입니다. 같은 장소 연속은 최대 2컷까지만 허용합니다.\n"
            "- 각 panel에는 key_props와 carryover_props가 있어야 합니다. carryover_props는 이전 컷에서 이어지는 동일 소품만 적어야 합니다.\n"
            "- carryover_props에 적힌 이름은 이전 컷과 같은 표기를 유지해야 합니다. 같은 소품인데 표현만 바꿔 적으면 실패입니다.\n"
            "- carryover_props는 반드시 해당 panel의 key_props에도 포함되어야 합니다.\n"
            "- 같은 소품이 이어지는 panel은 scene_prompt에도 그 소품이 다시 직접 언급되어야 합니다.\n"
            "- 내용 흐름상 계속 들고 있거나 사용 중인 핵심 소품이 다음 컷에서 갑자기 다른 물건으로 바뀌면 실패입니다.\n"
            "- 연속 소품은 scene_prompt에도 다시 명시되어야 하며, 종류와 주된 색상과 사용 방식이 유지되어야 합니다.\n"
            "- speaker_dialogues는 콜라와 제로 대사가 섞이지 않아야 합니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "rewrite_instruction"(string)\n'
        )
        review = _extract_json_object(
            self._generate_text(
                f"주제: {topic}\n기획안: {json.dumps(payload, ensure_ascii=False)}",
                thinking_level="high",
                system_instruction=system_instruction,
                cache_key="creative-brief-review-v2",
            )
        )
        review["has_issues"] = bool(review.get("has_issues", False))
        review["issues"] = [str(issue).strip() for issue in review.get("issues", []) if str(issue).strip()]
        review["rewrite_instruction"] = str(review.get("rewrite_instruction", "")).strip()
        return review

    def _rewrite_creative_brief(
        self,
        topic: str,
        payload: dict[str, Any],
        review: dict[str, Any],
    ) -> dict[str, Any]:
        system_instruction = (
            "주제와 기존 웹툰 기획안, 검수 이슈를 반영해 6컷 웹툰 기획안을 처음부터 다시 작성하세요.\n"
            "응답은 JSON 객체만 반환하세요.\n"
            "반드시 기존 스키마를 유지하세요: title, thumbnail_subtitle, episode_scope, subtitle_scope, scope_summary, image_prompt, thumbnail_scene_prompt, caption, hashtags, character_notes, panels.\n"
            "panels는 정확히 6개이며, 각 panel에는 panel_no, story_role, location, scene_prompt, key_props, carryover_props, speaker_dialogues가 필요합니다.\n"
            "speaker_dialogues는 정확히 콜라와 제로 2개 블록으로 작성하세요.\n"
            "title은 짧은 핵심 문구, thumbnail_subtitle은 설명형 보조 문구로 분리하세요.\n"
            'episode_scope는 "single_location" 또는 "journey" 중 하나로만 작성하세요.\n'
            'subtitle_scope도 "single_location" 또는 "journey" 중 하나로만 작성하세요.\n'
            "scope_summary는 title, thumbnail_subtitle, caption, panels가 공통으로 약속하는 이야기 범위를 한 문장으로 요약하세요.\n"
            "thumbnail_subtitle이 약속하는 에피소드 범위와 panels의 location 전개를 반드시 일치시키세요.\n"
            "thumbnail_subtitle이 단일 현장/상황을 말하면 결말까지 그 범위 안에서 끝내고, panels가 여러 장소를 거치면 subtitle도 그 전체 여정을 포괄하도록 다시 쓰세요.\n"
            "subtitle_scope는 반드시 thumbnail_subtitle의 실제 범위와 같아야 하며, episode_scope와 충돌하면 안 됩니다.\n"
            "caption도 subtitle과 scope_summary가 약속한 범위를 그대로 따라야 합니다.\n"
            "썸네일은 본문 어느 컷의 복사본이 아니라 별도의 티저 장면이어야 합니다.\n"
            "썸네일은 패널 1~6과 장소, 행동 순간, 카메라 구도가 모두 충분히 구분되어야 하며 특히 패널 1과 같은 장면이면 안 됩니다.\n"
            "썸네일은 주제를 한눈에 설명하는 포괄적 대표 배경이어야 하며, 본문 1~6컷 어디에도 같은 배경 구조나 서브로케이션을 다시 쓰면 안 됩니다.\n"
            "썸네일 대표 배경은 본문 특정 한 컷뿐 아니라 1~6 전체 어디에도 재등장하면 안 됩니다.\n"
            "썸네일과 본문은 구조적 지문까지 분리하세요. 천장 형태, 중앙 안내판 배치, 아트리움/복도/게이트 프레임, 카운터 구조, 바닥 패턴이 겹치지 않게 만드세요.\n"
            "썸네일과 패널 1은 캐릭터 포즈, 시선, 손동작, 소품 배치도 서로 다르게 만드세요.\n"
            "콜라는 썸네일부터 패널 6까지 모든 이미지에서 제로보다 항상 더 크게 읽혀야 합니다. 어느 컷에서도 같은 크기나 역전이 허용되지 않습니다.\n"
            "콜라와 제로의 상대 크기 비율은 썸네일부터 패널 6까지 같은 좁은 범위로 유지하세요. 특정 컷에서 콜라가 갑자기 지나치게 커지거나 제로가 지나치게 작아지면 실패입니다.\n"
            "썸네일부터 패널 6까지 모든 이미지에서 두 캐릭터는 완전한 이족보행이어야 하며, 네 발 보행이나 앞발 체중 지지는 허용하지 않습니다.\n"
            "각 패널의 key_props에는 이번 컷에서 실제로 보이는 핵심 소품만 적으세요. 휴대품, 문서, 도구, 음식, 탈것 관련 오브젝트처럼 장면을 바꾸는 물건만 넣고, 배경 잡동사니까지 나열하지 마세요.\n"
            "각 패널의 carryover_props에는 이전 컷에서 이번 컷까지 동일 물건으로 이어져야 하는 소품만 적으세요. 없으면 빈 배열로 두세요.\n"
            "같은 소품이 이어지면 carryover_props의 이름은 이전 컷과 같은 표기를 그대로 재사용하세요. 여권을 여행서류, 티켓을 표, 캐리어를 가방처럼 바꿔 적지 마세요.\n"
            "carryover_props는 반드시 key_props에도 포함하고, scene_prompt에도 그 소품을 다시 적으세요.\n"
            "특정 주제 예시를 박아넣지 말고, 이번 이야기에서 실제로 쓰이는 소품명만 구조적으로 정리하세요.\n"
            "내용 흐름상 이어지는 핵심 소품은 다음 컷에서도 같은 물건으로 유지하세요. 여권이 티켓으로 바뀌거나, 같은 캐리어가 다른 가방으로 바뀌면 실패입니다.\n"
            "연속 소품이 유지되는 컷은 scene_prompt에 그 소품을 다시 적고, 종류와 주된 색상과 손에 든 상태를 유지하세요.\n"
            "마지막 컷은 반드시 문제 해결 후 마무리 장면이어야 합니다.\n"
        )
        rewritten = _extract_json_object(
            self._generate_text(
                "\n".join(
                    [
                        f"주제: {topic}",
                        f"기존 기획안: {json.dumps(payload, ensure_ascii=False)}",
                        f"검수 이슈: {json.dumps(review.get('issues', []), ensure_ascii=False)}",
                        f"재작성 지시: {review.get('rewrite_instruction', '')}",
                    ]
                ),
                thinking_level="high",
                system_instruction=system_instruction,
                cache_key="creative-brief-rewrite-v2",
            )
        )
        return self._normalize_brief_payload(topic, rewritten, fallback=payload)

    def _normalize_korean_dialogues(self, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened = [line for panel in panels for line in panel.get("dialogue_lines", [])]
        needs_normalization = any(re.search(r"[A-Za-z]", line) for line in flattened) or any(
            len(panel.get("dialogue_lines", [])) > 4 or any(len(line) > 40 for line in panel.get("dialogue_lines", []))
            for panel in panels
        )
        if not needs_normalization:
            return panels

        prompt = (
            "아래 6컷 웹툰의 말풍선 대사를 자연스러운 한국어로 다시 정리하세요.\n"
            "장면 의미와 감정선은 유지하되, 영문/독문/로마자 표기는 말풍선에서 제거하고 한국어 표현으로 번역합니다.\n"
            "각 패널의 대사는 speaker_dialogues 구조를 유지하세요. 첫 항목은 콜라, 두 번째 항목은 제로입니다.\n"
            "각 캐릭터는 1~2줄만 말하게 하세요. 전체 대사는 가능하면 2~3줄, 길어도 4줄 이내로 유지하세요.\n"
            "한 줄은 보통 12~22자 정도가 가장 좋지만, 어색해지면 40자 이내에서 자연스럽게 조정하세요.\n"
            "한 컷이 개별 이미지이므로 정보 전달이 충분하도록 요약을 지나치게 하지 마세요.\n"
            "중요: '고양이'라는 단어는 어떤 대사에도 사용하지 마세요.\n"
            "중요: 두 캐릭터는 친한 친구 사이이므로 반드시 반말을 사용하세요. "
            "존댓말(~요, ~습니다, ~세요 등)은 절대 사용하지 마세요.\n"
            "대사 화자를 섞지 마세요. 콜라 대사는 콜라 블록 안에만, 제로 대사는 제로 블록 안에만 넣으세요.\n"
            "패널 수는 유지하세요.\n"
            'JSON 배열만 반환하세요. 각 항목은 "speaker_dialogues" 배열만 포함합니다.\n'
            f"입력 패널: {json.dumps(panels, ensure_ascii=False)}"
        )
        try:
            translated = _extract_json_object(
                self._generate_text(
                    prompt,
                    thinking_level="medium",
                    cache_key="normalize-dialogues-v1",
                )
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("대사 정규화 실패, 원본 유지: %s", exc)
            return panels
        if not isinstance(translated, list):
            logger.warning("대사 정규화 응답이 리스트가 아닙니다 (type=%s), 원본 유지", type(translated).__name__)
            return panels
        if len(translated) != len(panels):
            logger.warning(
                "대사 정규화 응답 패널 수 불일치 (요청=%d, 응답=%d), 가능한 범위만 적용",
                len(panels), len(translated),
            )

        normalized: list[dict[str, Any]] = []
        for panel, translated_panel in zip(panels, translated, strict=False):
            speaker_dialogues = normalize_speaker_dialogues(translated_panel)
            lines = flatten_speaker_dialogues(speaker_dialogues)
            normalized.append(
                {
                    **panel,
                    "speaker_dialogues": speaker_dialogues or panel.get("speaker_dialogues", []),
                    "dialogue_lines": lines or panel.get("dialogue_lines", []),
                }
            )
        return normalized + panels[len(normalized) :]


class GeminiOcrClient(_GeminiPromptCacheMixin):
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=_LLM_TIMEOUT),
        )
        self._init_prompt_cache()

    def _generate_text(
        self,
        prompt: str,
        *,
        image_bytes: bytes | None = None,
        mime_type: str = "image/png",
        image_parts: list[tuple[bytes, str]] | None = None,
        max_retries: int = 5,
        model_name: str | None = None,
        thinking_level: str | None = None,
        system_instruction: str | None = None,
        cache_key: str | None = None,
    ) -> str:
        resolved_model_name = model_name or self.settings.ocr_model
        resolved_thinking_level = thinking_level or self.settings.ocr_thinking_level
        cached_content = self._get_cached_content_name(
            model_name=resolved_model_name,
            cache_key=cache_key,
            system_instruction=system_instruction,
        )
        contents: list[Any] = [prompt]
        if image_bytes is not None:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        for part_bytes, part_mime_type in image_parts or []:
            contents.append(types.Part.from_bytes(data=part_bytes, mime_type=part_mime_type))
        for attempt in range(1, max_retries + 1):
            _throttle_api_call()
            try:
                response = self.client.models.generate_content(
                    model=resolved_model_name,
                    contents=contents,
                    config=_build_generate_content_config(
                        model_name=resolved_model_name,
                        thinking_level=resolved_thinking_level,
                        system_instruction=None if cached_content else system_instruction,
                        cached_content=cached_content,
                    ),
                )
                text = _extract_gemini_text(response)
                if not text:
                    raise RuntimeError("Gemini OCR response did not contain text output")
                return text
            except Exception as exc:
                if attempt >= max_retries:
                    raise
                overload = _is_server_overload(exc)
                wait = _backoff_seconds(attempt, is_overload=overload)
                logger.warning("OCR 텍스트 생성 실패 (시도 %d/%d, 대기 %.0f초): %s", attempt, max_retries, wait, exc)
                time.sleep(wait)
        raise RuntimeError("Unreachable")

    def _generate_review_text(
        self,
        prompt: str,
        *,
        image_bytes: bytes | None = None,
        mime_type: str = "image/png",
        image_parts: list[tuple[bytes, str]] | None = None,
        max_retries: int = 5,
        thinking_level: str | None = None,
        system_instruction: str | None = None,
        cache_key: str | None = None,
    ) -> str:
        return self._generate_text(
            prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            image_parts=image_parts,
            max_retries=max_retries,
            model_name=self.settings.llm_model,
            thinking_level=thinking_level or self.settings.llm_thinking_level,
            system_instruction=system_instruction,
            cache_key=cache_key,
        )

    def smoke_test(self, image_bytes: bytes, mime_type: str = "image/png") -> dict[str, Any]:
        extracted = self.extract_text(image_bytes, mime_type=mime_type)
        return {"model": self.settings.ocr_extract_model, "extracted_text": extracted}

    def extract_text(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raw_text = self._generate_text(
            "이미지에 보이는 텍스트만 줄바꿈 포함 원문 그대로 추출하세요.\n"
            "설명문, 요약, '다음과 같습니다' 같은 서술 문장은 절대 추가하지 마세요.\n"
            "텍스트가 없으면 빈 문자열만 반환하세요.",
            image_bytes=image_bytes,
            mime_type=mime_type,
            model_name=self.settings.ocr_extract_model,
            thinking_level=self.settings.ocr_extract_thinking_level,
        )
        return clean_ocr_text(raw_text)

    def plan_text_corrections(
        self,
        intended_lines: list[str],
        ocr_text: str,
        *,
        speaker_dialogues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not intended_lines:
            return {
                "rerender_required": False,
                "corrected_text_lines": [],
                "corrected_speaker_dialogues": [],
                "issues": [],
                "edit_instruction": "",
                "confidence": 1.0,
            }

        speaker_blocks = normalize_speaker_dialogues({"speaker_dialogues": speaker_dialogues or []})
        system_instruction = (
            "당신은 웹툰 이미지의 OCR 결과를 검수하는 편집자입니다.\n"
            "의도한 텍스트와 OCR 결과를 비교해 오탈자, 누락, 글자 깨짐 여부를 판단하세요.\n"
            "중요: 배경 간판, 기계 화면, 표지판의 독일어/영어 텍스트는 완전히 무시하세요. "
            "말풍선 안의 한국어 대사 텍스트만 비교 대상입니다.\n"
            "중요: 말풍선 너비 제한으로 한 대사가 여러 줄로 자동 줄바꿈될 수 있습니다. "
            "예를 들어 '그래도 덕분에 마트 구경은 실컷 했네?'가 OCR에서 "
            "'그래도 덕분에 마트 구경은 실컷'과 '했네?'로 나뉘어도 이는 정상적인 줄바꿈이며 오류가 아닙니다. "
            "줄바꿈 위치 차이만으로는 절대 rerender_required를 true로 설정하지 마세요.\n"
            "rerender_required=true는 실제 오탈자(글자가 다름, 누락, 깨짐, 다른 언어)인 경우에만 해당합니다.\n"
            "중요: 콜라/제로의 대사는 절대 서로 섞으면 안 됩니다. speaker 블록을 유지하세요.\n"
            "중요: 콜라, 제로 같은 고유명사는 한 글자라도 바뀌면 오류입니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "rerender_required"(bool), "corrected_text_lines"(array), "issues"(array), '
            '"edit_instruction"(string), "confidence"(number), "corrected_speaker_dialogues"(array)\n'
            'corrected_speaker_dialogues 항목 형식: {"speaker": "kolla"|"zero", "dialogue_lines": ["..."]}\n'
            "corrected_text_lines에는 이미지에 최종적으로 정확히 보여야 하는 텍스트 줄만 넣으세요.\n"
            "corrected_speaker_dialogues에는 화자별 최종 대사 줄을 넣으세요. 이 값이 가장 우선입니다.\n"
        )
        payload = _extract_json_object(
            self._generate_review_text(
                "\n".join(
                    [
                        f"의도한 텍스트: {json.dumps(intended_lines, ensure_ascii=False)}",
                        f"의도한 speaker_dialogues: {json.dumps(speaker_blocks, ensure_ascii=False)}",
                        f"OCR 결과: {ocr_text}",
                    ]
                ),
                system_instruction=system_instruction,
                cache_key="ocr-plan-text-corrections-v1",
            )
        )
        raw_blocks = payload.get("corrected_speaker_dialogues", speaker_blocks)
        normalized_blocks = normalize_speaker_dialogues({"speaker_dialogues": raw_blocks})
        if not any(block.get("dialogue_lines") for block in normalized_blocks):
            normalized_blocks = normalize_speaker_dialogues({"speaker_dialogues": speaker_blocks})
        payload["corrected_speaker_dialogues"] = normalized_blocks
        payload["corrected_text_lines"] = flatten_speaker_dialogues(normalized_blocks)
        if not payload["corrected_text_lines"]:
            payload["corrected_text_lines"] = [
                normalize_dialogue_text(str(line).strip())
                for line in payload.get("corrected_text_lines", intended_lines)
                if str(line).strip()
            ]
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        payload["rerender_required"] = bool(payload.get("rerender_required", False))
        payload["confidence"] = float(payload.get("confidence", 0.0) or 0.0)
        return payload

    def review_final_webtoon_package(
        self,
        *,
        topic: str,
        title: str,
        thumbnail_subtitle: str,
        caption: str,
        episode_scope: str,
        subtitle_scope: str,
        scope_summary: str,
        thumbnail_scene_prompt: str,
        panel_summaries: list[dict[str, Any]],
        slide_images: list[tuple[bytes, str]],
        stage_gate_findings: list[str] | None = None,
    ) -> dict[str, Any]:
        if not slide_images:
            return {
                "hard_blockers": [],
                "soft_scores": {},
                "notes": [],
                "summary": "",
                "review_unavailable": "no_slide_images",
            }

        system_instruction = (
            "첫 번째 이미지는 웹툰 썸네일이고, 뒤이어 제공되는 이미지는 본문 6컷 패널입니다.\n"
            "이 결과물이 현재 제약 조건을 얼마나 만족하는지 최종 품질 검수를 수행하세요.\n"
            "중요: 어떤 주제든 같은 기준으로만 판단하세요. 특정 주제 예시를 임의로 덧붙이지 마세요.\n"
            "중요: 비주요 인간 배경 인물, 승객, 직원, 줄은 장면상 필요하면 허용됩니다. 이들을 추가 주인공으로 판단하지 마세요.\n"
            "중요: 함께 제공되는 상위 stage gate 점검 메모는 이전 단계의 자동 검수 결과입니다. 이 메모가 가리키는 문제가 최종 이미지에도 실제로 보이면 같은 성격의 하드 블로커를 반드시 다시 적으세요.\n"
            "중요: 상위 stage gate에서 문제를 지적했는데 최종 이미지에서 여전히 보이는 경우, '모든 제약을 완벽히 준수했다' 같은 요약을 쓰면 안 됩니다.\n"
            "중요: 썸네일-본문 중복은 특정 패널 한 장만 보는 것이 아니라 본문 1~6 전체와 비교해 판단하세요. 썸네일 대표 배경이 어느 한 패널이라도 재등장하면 하드 블로커입니다.\n"
            "중요: 썸네일-본문 중복은 간판 문구만 다른 정도로 피해 갔다고 보지 마세요. 천장 형태, 중앙 홀 구조, 복도 깊이, 출구 프레임, 카운터 타입, 바닥 패턴, 대표 간판 군집이 같으면 같은 배경으로 판단하세요.\n"
            "중요: 콜라와 제로의 상대 크기 비율은 썸네일부터 패널 6까지 거의 같은 좁은 범위(대략 콜라가 10~15% 더 큼)로 유지되어야 합니다. 특정 컷에서 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아지면 하드 블로커입니다.\n"
            "중요: panel_summaries의 key_props와 carryover_props는 컷별 핵심 소품과 연속 소품입니다. carryover_props에 적힌 소품이 사라지거나 다른 물건으로 바뀌면 하드 블로커입니다.\n"
            "중요: 각 패널은 scene_prompt와 실제 이미지가 맞아야 합니다. 장소, 핵심 동작, 실내외 구분, 좌석/플랫폼 구분이 어긋나면 하드 블로커입니다.\n"
            "중요: prominent background text의 철자 오류, unreadable microtext, 잘못된 문서 라벨은 하드 블로커입니다.\n"
            "중요: 사족보행, 앞발 체중 지지, 장비 상판 위 탑승, 극단적 크기 비율 붕괴는 하드 블로커입니다.\n"
            "하드 블로커에는 게시를 막아야 할 명백한 문제만 넣으세요.\n"
            "예: 썸네일-본문 중복, 장면과 맞지 않는 배경, 주인공 추가/분신, 심한 사족보행, 심한 말풍선 가림, 오탈자/글자 깨짐, 마무리 컷 부재, subtitle/caption/scope와 실제 전개 범위 불일치, 시리즈 전체에서 캐릭터 상대 크기 비율 붕괴 등.\n"
            "title, thumbnail_subtitle, caption, episode_scope, subtitle_scope, scope_summary가 실제 썸네일과 6컷 전개, 결말과 같은 범위를 가리켜야 합니다.\n"
            "episode_scope가 single_location이면 패널과 결말이 같은 현장/상황권 안에서 끝나야 하며, journey면 여러 장소 이동이 subtitle/caption/scope_summary에 반영되어야 합니다.\n"
            "subtitle_scope는 thumbnail_subtitle이 약속하는 범위를 직접 설명해야 합니다. subtitle_scope가 single_location인데 실제 패널이 여정형이면 하드 블로커입니다.\n"
            "소프트 점수는 0.0~1.0 범위로 평가하세요.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "hard_blockers"(array), "soft_scores"(object), "notes"(array), "summary"(string)\n'
            'soft_scores 필드: "topic_alignment", "story_flow", "background_progression", '
            '"thumbnail_distinction", "bubble_placement", "ending_resolution", "scope_alignment", "caption_alignment"\n'
        )
        first_bytes, first_mime = slide_images[0]
        payload = _extract_json_object(
            self._generate_review_text(
                "\n".join(
                    [
                        f"주제: {topic}",
                        f"제목: {title}",
                        f"썸네일 부제목: {thumbnail_subtitle}",
                        f"인스타 캡션: {caption}",
                        f"에피소드 범위 타입: {episode_scope}",
                        f"부제목 범위 타입: {subtitle_scope}",
                        f"범위 요약: {scope_summary}",
                        f"썸네일 핵심 장면: {thumbnail_scene_prompt}",
                        f"패널 요약: {json.dumps(panel_summaries, ensure_ascii=False)}",
                        f"상위 stage gate 점검 메모: {json.dumps(stage_gate_findings or [], ensure_ascii=False)}",
                    ]
                ),
                image_bytes=first_bytes,
                mime_type=first_mime,
                image_parts=slide_images[1:],
                thinking_level="high",
                system_instruction=system_instruction,
                cache_key="ocr-review-final-package-v2",
            )
        )
        raw_scores = payload.get("soft_scores", {})
        soft_scores: dict[str, float] = {}
        for key in (
            "topic_alignment",
            "story_flow",
            "background_progression",
            "thumbnail_distinction",
            "bubble_placement",
            "ending_resolution",
            "scope_alignment",
            "caption_alignment",
        ):
            value = raw_scores.get(key, 0.0) if isinstance(raw_scores, dict) else 0.0
            try:
                soft_scores[key] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                soft_scores[key] = 0.0
        return {
            "hard_blockers": [str(item).strip() for item in payload.get("hard_blockers", []) if str(item).strip()],
            "soft_scores": soft_scores,
            "notes": [str(item).strip() for item in payload.get("notes", []) if str(item).strip()],
            "summary": str(payload.get("summary", "")).strip(),
        }

    def check_background_text(self, image_bytes: bytes, scene_prompt: str, mime_type: str = "image/png") -> dict[str, Any]:
        system_instruction = (
            "이미지 속 배경에 보이는 독일어/영어 텍스트(간판, 화면 UI, 표지판 등)를 모두 추출하세요.\n"
            "한국어 말풍선 텍스트는 완전히 무시하세요.\n"
            "중요: 이 텍스트는 별도 텍스트 레이어가 아니라 이미지 안에 래스터화된 글리프일 수 있습니다. OCR 문장 복원보다 실제로 눈에 보이는 큰 글자 모양과 철자를 우선 판정하세요.\n"
            "부분적으로 깨진 단어, 한 글자만 틀린 표지판, 비슷한 글자 모양의 오탈자도 배경 텍스트 오류로 잡아야 합니다.\n"
            "추출한 배경 텍스트에 철자 오류, 글자 깨짐, 모자이크 같은 의사문자, 읽을 수 없는 표시 텍스트가 있는지 확인하세요.\n"
            "특히 큰 간판, 디지털 화면, 메뉴판, 라벨, 안내판처럼 시선이 가는 텍스트 영역의 가짜 알파벳 덩어리나 깨진 미세 글자는 오류입니다.\n"
            "여권, 비자, 문서철, 입국 도장, 항공편 전광판처럼 원래 미세 텍스트가 많은 영역은 전체 문장을 복원하려 하지 말고, 제목급의 짧은 라벨 1~3개만 남기고 나머지는 비문자 요소로 단순화하세요.\n"
            "작은 세부 문구를 전부 복원할 수 없으면, 눈에 띄는 큰 텍스트만 정확히 고치고 나머지는 짧고 읽을 수 있는 한두 줄 또는 색 막대/아이콘으로 단순화하라고 제안해도 됩니다.\n"
            "단, 장면 이해에 핵심인 안내어(예: 출구, 층수, 호수, 게이트, 통로 번호, 가격, 상태 표기)는 bars/icons로 대체하지 말고 짧고 읽을 수 있는 실제 단어로 남겨야 합니다.\n"
            "철자를 정확히 확신할 수 없는 비핵심 배경 텍스트는 지우거나 blank bars/icons로 단순화하라고 제안하세요. 억지 복원은 금지합니다.\n"
            "장소 종류와 무관하게 표지판 하나당 큰 라벨 하나를 우선합니다. 여러 줄 미세 텍스트가 보이면 거의 항상 단순화 대상으로 간주하세요.\n"
            "핵심 표지판은 가능하면 단일 단어 또는 매우 짧은 2단어 라벨로 제안하세요. 예: 'INFO', 'EXIT', 'OPEN', 'TICKET', 'Gate A12', 'Aisle 3'.\n"
            "correct 필드는 긴 설명문이 아니라 최종 화면에 남길 짧은 텍스트 또는 아주 짧은 편집 지시만 적으세요. 한 항목당 최대 12단어입니다.\n"
            "correct 필드는 가능하면 1~3개 단어, 최대 한 줄로 제한하세요. 긴 문장 설명이나 괄호 속 해설은 금지합니다.\n"
            "전광판/디지털 화면은 'INFO', 'Gate A12', 'On Time'처럼 헤더 1개와 짧은 행 1~3개만 제안하세요.\n"
            "문서/여권/티켓은 'DOCUMENT', 'NAME', 'ID'처럼 큰 라벨 1~3개만 제안하고 나머지는 blank lines로 두라고 하세요.\n"
            "도장/스탬프는 'STAMP' 같은 짧은 라벨과 날짜 한 줄만 제안하세요. 원형 둘레의 미세문구는 금지입니다.\n"
            "버튼/키오스크/UI 미세문구는 icons only, blank bars only, one short label 같은 짧은 지시로 제안하세요.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "background_texts"(array of string), "has_errors"(bool), "corrections"(array)\n'
            'corrections 항목: {"found": "이미지에 보이는 텍스트", "correct": "올바른 철자 또는 안전한 단순화 지시", "reason": "설명"}\n'
            "배경 텍스트가 없거나 철자 오류가 없으면 has_errors=false, corrections=[]을 반환하세요.\n"
        )
        payload = _extract_json_object(
            self._generate_review_text(
                f"장면 설명 참고: {scene_prompt}",
                image_bytes=image_bytes,
                mime_type=mime_type,
                system_instruction=system_instruction,
                cache_key="ocr-check-background-text-v1",
            )
        )
        payload["has_errors"] = bool(payload.get("has_errors", False))
        payload["background_texts"] = [clean_ocr_text(str(t)) for t in payload.get("background_texts", []) if str(t).strip()]
        payload["background_texts"] = [t for t in payload["background_texts"] if t]
        payload["corrections"] = [
            {
                "found": clean_ocr_text(str(item.get("found", "")).strip()),
                "correct": _normalize_background_correction_text(
                    str(item.get("found", "")).strip(),
                    str(item.get("correct", "")).strip(),
                ),
                "reason": str(item.get("reason", "")).strip(),
            }
            for item in payload.get("corrections", [])
            if clean_ocr_text(str(item.get("found", "")).strip())
            and _normalize_background_correction_text(
                str(item.get("found", "")).strip(),
                str(item.get("correct", "")).strip(),
            )
        ]
        return payload

    def check_character_composition(self, image_bytes: bytes, scene_prompt: str, mime_type: str = "image/png") -> dict[str, Any]:
        system_instruction = (
            "웹툰 패널 이미지를 보고 등장인물 구성이 올바른지 검사하세요.\n"
            "기준은 다음과 같습니다.\n"
            "- 정확히 두 주인공만 한 번씩 등장해야 합니다.\n"
            "- 이미지 전체에 고양이형 주인공은 콜라 1명, 제로 1명만 있어야 합니다. 추가 고양이형 캐릭터나 복제체가 보이면 오류입니다.\n"
            "- 승객, 점원, 심사관, 직원 같은 비주요 인간 배경 인물은 장면 설명에 필요하면 허용되지만, 고양이형 주인공처럼 크거나 전면에 나오면 오류입니다.\n"
            "- 검은 캐릭터 콜라는 왼쪽, 회색 줄무늬 캐릭터 제로는 오른쪽에 있어야 합니다.\n"
            "- 콜라는 제로보다 약간 더 크게 보여야 합니다.\n"
            "- 콜라가 제로와 비슷한 크기이거나 더 작아 보이면 오류입니다. 머리, 몸통, 전체 키 기준으로 콜라가 더 크게 읽혀야 합니다.\n"
            "- 이 크기 규칙은 썸네일부터 패널 6까지 모든 컷에 동일하게 적용된다고 가정하고 검사하세요. 어느 컷에서도 제로가 더 크거나 같게 읽히면 오류입니다.\n"
            "- 또한 상대 크기 비율은 시리즈 전체에서 거의 같은 좁은 범위로 유지되어야 합니다. 콜라가 갑자기 과도하게 커지거나 제로가 과도하게 작아져 대략 10~15% 범위를 크게 벗어나면 오류입니다.\n"
            "- 두 캐릭터는 의인화된 이족보행 캐릭터여야 합니다. 서 있거나 걷는 장면에서 네 발로 서 있으면 오류입니다.\n"
            "- 앞발이 바닥을 딛고 체중을 지탱하는 일반 동물형 자세는 오류입니다.\n"
            "- 앉아 있는 장면이어도 골반이 좌석에 닿고 상체는 사람처럼 세워져 있어야 하며, 앞발을 손처럼 쓰는 의인화 자세가 보여야 합니다.\n"
            "- 계단, 경사면, 이동 장치가 있는 장면에서도 두 캐릭터는 계속 이족보행이어야 합니다. 한 캐릭터라도 네 발로 기어오르거나 앞발로 체중을 지탱하면 오류입니다.\n"
            "- 사족보행, 앞발 체중 지지, 일반 동물형 달리기, 바닥을 짚는 자세는 한 번이라도 보이면 즉시 오류입니다.\n"
            "- 벨트, 레일, 기계 상판, 운반 장비, 전시대 상단처럼 사람이 올라서면 안 되는 표면은 배경 장비입니다. 주인공이 그 위에 올라타거나 서 있으면 오류입니다.\n"
            "- 하늘이나 상단 여백에 떠 있는 추가 캐릭터, 잘린 상반신, 중복 얼굴, 반응용 컷인, 분신이 있으면 오류입니다.\n"
            "- 실루엣, 뒷모습, 그림자처럼 보이는 추가 캐릭터, 상단 구석의 참조용 미니 캐릭터, 부분적으로 잘린 추가 몸체도 모두 오류입니다.\n"
            "- 스티커처럼 붙은 얼굴, 말풍선 옆 반응 컷인, 작은 복제 캐릭터, 뒷모습 분신도 전부 추가 캐릭터로 간주합니다.\n"
            "- 한 장 안에 같은 장면이 위아래 또는 좌우로 반복되거나, 두 컷이 합쳐진 것처럼 보이면 오류입니다.\n"
            "- 하나의 이미지가 만화 컷 여러 개로 분할되어 있거나, 같은 캐릭터 쌍이 두 번 보이면 오류입니다.\n"
            "- scene_prompt에 들어 있는 핵심 장소/행동 단서가 실제 이미지 배경에 반영되어야 합니다. 주제와 무관한 일반 풍경이면 오류입니다.\n"
            "- scene_prompt가 특정 소품, 손동작, 물건 상호작용을 지정하면 그대로 보여야 합니다. 캐리어, 가방, 표, 지도, 여권, 휴대폰 등 핵심 소품을 다른 물건으로 바꾸면 오류입니다.\n"
            "- scene_prompt에 이전 컷 연속 소품 정보가 포함되어 있다면 그 소품을 같은 종류와 비슷한 주된 색상으로 유지해야 합니다. 여권이 티켓으로, 캐리어가 다른 가방으로, 문서철이 지도나 휴대폰으로 바뀌면 오류입니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "edit_instruction"(string), '
            '"kolla_count"(int), "zero_count"(int), "extra_character_count"(int), "duplicate_scene_detected"(bool), '
            '"bipedal_ok"(bool), "scene_match_ok"(bool), "kolla_larger_than_zero_ok"(bool), "silhouette_extra_count"(int), '
            '"kolla_size_gap_band_ok"(bool), "estimated_size_gap_percent"(number), '
            '"duplicate_character_detected"(bool), "upper_margin_character_detected"(bool), '
            '"quadruped_detected"(bool), "quadruped_subjects"(array), "upright_pose_ok"(bool), '
            '"forepaws_used_as_hands_ok"(bool), "unsafe_surface_pose_detected"(bool), "reference_like_copy_detected"(bool), '
            '"cutin_or_sticker_detected"(bool), "partial_body_duplicate_detected"(bool)\n'
            "문제가 없으면 has_issues=false, issues=[], edit_instruction=''로 반환하세요.\n"
        )
        payload = _extract_json_object(
            self._generate_review_text(
                f"장면 설명 참고: {scene_prompt}",
                image_bytes=image_bytes,
                mime_type=mime_type,
                system_instruction=system_instruction,
                cache_key="ocr-check-character-composition-v1",
            )
        )
        payload["kolla_count"] = max(0, int(payload.get("kolla_count", 0) or 0))
        payload["zero_count"] = max(0, int(payload.get("zero_count", 0) or 0))
        payload["extra_character_count"] = max(0, int(payload.get("extra_character_count", 0) or 0))
        payload["silhouette_extra_count"] = max(0, int(payload.get("silhouette_extra_count", 0) or 0))
        payload["duplicate_scene_detected"] = bool(payload.get("duplicate_scene_detected", False))
        payload["bipedal_ok"] = bool(payload.get("bipedal_ok", True))
        payload["scene_match_ok"] = bool(payload.get("scene_match_ok", True))
        payload["kolla_larger_than_zero_ok"] = bool(payload.get("kolla_larger_than_zero_ok", True))
        payload["kolla_size_gap_band_ok"] = bool(payload.get("kolla_size_gap_band_ok", True))
        try:
            payload["estimated_size_gap_percent"] = float(payload.get("estimated_size_gap_percent", 12.0) or 12.0)
        except (TypeError, ValueError):
            payload["estimated_size_gap_percent"] = 12.0
        payload["duplicate_character_detected"] = bool(payload.get("duplicate_character_detected", False))
        payload["upper_margin_character_detected"] = bool(payload.get("upper_margin_character_detected", False))
        payload["quadruped_detected"] = bool(payload.get("quadruped_detected", False))
        payload["quadruped_subjects"] = [
            str(subject).strip() for subject in payload.get("quadruped_subjects", []) if str(subject).strip()
        ]
        payload["upright_pose_ok"] = bool(payload.get("upright_pose_ok", True))
        payload["forepaws_used_as_hands_ok"] = bool(payload.get("forepaws_used_as_hands_ok", True))
        payload["unsafe_surface_pose_detected"] = bool(payload.get("unsafe_surface_pose_detected", False))
        payload["reference_like_copy_detected"] = bool(payload.get("reference_like_copy_detected", False))
        payload["cutin_or_sticker_detected"] = bool(payload.get("cutin_or_sticker_detected", False))
        payload["partial_body_duplicate_detected"] = bool(payload.get("partial_body_duplicate_detected", False))
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        total_extra_characters = payload["extra_character_count"] + payload["silhouette_extra_count"]
        computed_issue = (
            payload.get("has_issues", False)
            or payload["kolla_count"] != 1
            or payload["zero_count"] != 1
            or total_extra_characters > 0
            or payload["duplicate_scene_detected"]
            or not payload["bipedal_ok"]
            or not payload["scene_match_ok"]
            or not payload["kolla_larger_than_zero_ok"]
            or not payload["kolla_size_gap_band_ok"]
            or payload["duplicate_character_detected"]
            or payload["upper_margin_character_detected"]
            or payload["quadruped_detected"]
            or not payload["upright_pose_ok"]
            or not payload["forepaws_used_as_hands_ok"]
            or payload["unsafe_surface_pose_detected"]
            or payload["reference_like_copy_detected"]
            or payload["cutin_or_sticker_detected"]
            or payload["partial_body_duplicate_detected"]
        )
        payload["has_issues"] = bool(computed_issue)
        if payload["has_issues"] and not payload["edit_instruction"]:
            payload["edit_instruction"] = (
                "Remove every extra silhouette, rear-view duplicate, sticker-like face, cut-in, partial body, reflection-like copy, "
                "or floating mini character so the image contains exactly one full-body Kolla on the left and one full-body Zero on the right. "
                "Keep Kolla visibly larger than Zero in every shot in overall body scale, head size, torso mass, and silhouette so Kolla never reads as the smaller or equal-sized character. "
                "Keep that size gap stable at about ten to fifteen percent rather than letting Kolla become dramatically oversized or Zero become tiny. "
                "Keep both characters anthropomorphic and upright on two legs only, with forepaws used as hands rather than front legs. "
                "Do not allow any quadruped pose, weight-bearing forepaw pose, non-walkable-prop standing pose, or upper-margin duplicate character. "
                "Keep recurring props consistent with the scene context instead of swapping passports, tickets, bags, maps, phones, or document folders to different object types. "
                "If belts, rails, machinery, stairs, or moving equipment appear, keep both protagonists on the proper walking surface beside them rather than on top of the equipment. Match the requested scene and location cues, "
                "and remove any repeated multi-panel layout or duplicated scene fragment."
            )
        return payload

    def check_character_reference_consistency(
        self,
        image_bytes: bytes,
        scene_prompt: str,
        reference_images: list[tuple[bytes, str]],
        mime_type: str = "image/png",
    ) -> dict[str, Any]:
        system_instruction = (
            "첫 번째 이미지는 현재 생성된 웹툰 이미지이고, 뒤이어 제공되는 이미지는 반드시 따라야 하는 캐릭터 참조 이미지들입니다.\n"
            "현재 이미지의 콜라와 제로가 참조 이미지의 동일 캐릭터와 외형적으로 일치하는지 검사하세요.\n"
            "\n"
            "중요: 심각한 정체성 오류만 보고하세요. 다음은 오류가 아닙니다:\n"
            "- 미세한 색조 차이 (귀 안쪽이 갈색 vs 분홍 등)\n"
            "- 무늬의 단순화나 스타일 변형 (줄무늬가 소용돌이 대신 물결형 등)\n"
            "- 그림체나 렌더링 스타일의 자연스러운 차이\n"
            "\n"
            "실제 오류로 보고해야 하는 경우:\n"
            "- 콜라가 검은색이 아니거나, 제로가 회색 줄무늬가 아닌 경우 (전혀 다른 색상)\n"
            "- 캐릭터 수가 2마리가 아닌 경우 (중복, 누락, 추가 캐릭터)\n"
            "- 콜라와 제로의 좌우 위치가 바뀐 경우 (콜라=왼쪽, 제로=오른쪽)\n"
            "- 캐릭터가 고양이가 아닌 다른 동물로 보이는 경우\n"
            "- 콜라의 눈이 노란색이 아니거나, 제로의 눈 색이 완전히 다른 경우\n"
            "- 콜라가 제로보다 작거나 비슷하게 읽히는 경우\n"
            "- 썸네일부터 패널 6까지 어느 컷이든 콜라가 제로보다 작거나 비슷하게 읽히는 경우\n"
            "- 썸네일부터 패널 6까지 상대 크기 비율이 갑자기 무너져 콜라가 과도하게 커지거나 제로가 과도하게 작아진 경우\n"
            "- 네 발 보행, 앞발 체중 지지, 일반 동물형 달리기, 벨트/레일/기계 상판 위 탑승처럼 의인화 규칙이 무너진 경우\n"
            "- scene_prompt에 명시된 연속 소품이 이전 컷과 다른 물건으로 바뀌어 캐릭터 행동 연속성이 깨진 경우\n"
            "\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "edit_instruction"(string)\n'
            "문제가 없으면 has_issues=false, issues=[], edit_instruction=''로 반환하세요.\n"
        )
        payload = _extract_json_object(
            self._generate_review_text(
                f"장면 설명 참고: {scene_prompt}",
                image_bytes=image_bytes,
                mime_type=mime_type,
                image_parts=reference_images,
                system_instruction=system_instruction,
                cache_key="ocr-check-character-reference-v1",
            )
        )
        payload["has_issues"] = bool(payload.get("has_issues", False))
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        return payload

    def check_thumbnail_panel_distinction(
        self,
        thumbnail_bytes: bytes,
        panel_images: list[tuple[bytes, str]],
        *,
        thumbnail_scene_prompt: str,
        panel_summaries: list[dict[str, Any]],
        mime_type: str = "image/png",
    ) -> dict[str, Any]:
        system_instruction = (
            "첫 번째 이미지는 웹툰 썸네일이고, 뒤이어 제공되는 이미지는 본문 패널들입니다.\n"
            "썸네일이 본문 어느 패널의 배경, 장소, 사건 순간, 카메라 구도를 사실상 재사용했는지 검사하세요.\n"
            "특정 한 패널만 보는 것이 아니라 반드시 본문 1~6 전체와 비교하세요.\n"
            "기준은 다음과 같습니다.\n"
            "- 썸네일은 별도의 티저 장면이어야 하며, 패널 1~6 중 하나의 복사본/확대본/넓게 잡은 변형판이면 오류입니다.\n"
            "- 썸네일은 주제를 설명하는 포괄적 대표 배경이어야 하며, 특정 패널 하나의 국소 배경으로 읽히면 오류입니다.\n"
            "- 썸네일과 어떤 패널이 같은 장소, 같은 사건 순간, 같은 카메라 구도로 보이면 오류입니다.\n"
            "- 같은 넓은 장소 안의 다른 표지판 정도만 바뀐 경우도 오류입니다. 같은 메인 홀, 같은 복도, 같은 대기실, 같은 카운터 존처럼 배경의 큰 구조가 같으면 중복으로 봅니다.\n"
            "- 본문 1~6컷 중 하나라도 썸네일과 같은 배경 구조, 같은 서브로케이션, 같은 대표 간판/소품 배치를 가지면 오류입니다.\n"
            "- 구조적 지문이 같으면 중복입니다. 천장 형태, 중앙 홀 구조, 복도 깊이, 출구 프레임, 카운터 타입, 바닥 패턴, 대표 간판 군집이 같으면 같은 장소 변형판으로 판단하세요.\n"
            "- 특히 패널 4 같은 특정 번호에만 한정하지 말고, 1~6 어느 컷에서든 썸네일 대표 배경이 재등장하면 오류입니다.\n"
            "- 썸네일은 특히 패널 1과 다른 서브로케이션, 다른 사건 비트, 다른 카메라 거리로 보여야 합니다.\n"
            "- 썸네일 상단이나 구석에 추가 캐릭터 실루엣, 뒷모습, 참조용 분신, 미니 캐릭터, 잘린 몸체가 보이면 오류입니다.\n"
            "- 썸네일은 주제의 출발 사건과 핵심 장소를 직접 보여줘야 합니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "edit_instruction"(string), "duplicated_panel_numbers"(array)\n'
            "duplicated_panel_numbers에는 썸네일과 사실상 같은 장면으로 보이는 패널 번호만 넣으세요.\n"
        )
        payload = _extract_json_object(
            self._generate_review_text(
                "\n".join(
                    [
                        f"thumbnail_scene_prompt: {thumbnail_scene_prompt}",
                        f"panel_summaries: {json.dumps(panel_summaries, ensure_ascii=False)}",
                    ]
                ),
                image_bytes=thumbnail_bytes,
                mime_type=mime_type,
                image_parts=panel_images,
                system_instruction=system_instruction,
                cache_key="ocr-check-thumbnail-distinction-v1",
            )
        )
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        payload["duplicated_panel_numbers"] = [
            int(item)
            for item in payload.get("duplicated_panel_numbers", [])
            if str(item).strip().isdigit()
        ]
        payload["has_issues"] = bool(
            payload.get("has_issues", False) or payload["duplicated_panel_numbers"] or payload["issues"]
        )
        if payload["has_issues"] and not payload["edit_instruction"]:
            duplicated = ", ".join(str(no) for no in payload["duplicated_panel_numbers"]) or "panel scenes"
            payload["edit_instruction"] = (
                "Redesign the thumbnail as a separate teaser shot, not a duplicate of "
                f"{duplicated}. Compare against all six panels, not just one matched cut. Use a broad representative background for the whole episode rather than any panel background. Change to a clearly different sub-location, story beat, and camera distance while keeping the same topic, "
                "change the structural fingerprint as well, including ceiling shape, central hall layout, corridor depth, gate or exit frame, counter type, floor pattern, and signature signage cluster, and remove any extra silhouette, rear-view duplicate, mini character, or cropped body in the corners."
            )
        return payload


class GeminiImageClient:
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.last_generation_model: str | None = None
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=_IMAGE_TIMEOUT),
        )

    def smoke_test(self) -> dict[str, Any]:
        image_bytes, mime_type, text = self.generate_image("A tiny cartoon banana with a speech bubble saying smoke test.")
        return {
            "model": self.last_generation_model or self.settings.image_model,
            "mime_type": mime_type,
            "bytes": len(image_bytes),
            "text": text,
        }

    def generate_image(
        self,
        prompt: str,
        *,
        reference_images: list[tuple[bytes, str]] | None = None,
        reference_image_paths: list[Path] | None = None,
        edit_image_bytes: bytes | None = None,
        edit_image_mime_type: str | None = None,
        max_retries: int = 3,
    ) -> tuple[bytes, str, str]:
        normalized_reference_images = list(reference_images or [])
        reference_image_paths = list(reference_image_paths or [])
        total_references = len(normalized_reference_images) or len(reference_image_paths)

        def build_contents(*, max_reference_inputs: int | None) -> list[Any]:
            contents: list[Any] = [prompt]
            if edit_image_bytes is not None:
                contents.append(
                    types.Part.from_bytes(
                        data=edit_image_bytes,
                        mime_type=edit_image_mime_type or "image/png",
                    )
                )
            if normalized_reference_images:
                selected_reference_images = (
                    normalized_reference_images[:max_reference_inputs]
                    if max_reference_inputs is not None
                    else normalized_reference_images
                )
                for part_bytes, part_mime_type in selected_reference_images:
                    contents.append(types.Part.from_bytes(data=part_bytes, mime_type=part_mime_type))
            else:
                selected_reference_paths = (
                    reference_image_paths[:max_reference_inputs]
                    if max_reference_inputs is not None
                    else reference_image_paths
                )
                for path in selected_reference_paths:
                    contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=_guess_mime_type(path)))
            return contents

        last_error: Exception | None = None
        model_name = self.settings.image_model
        for attempt in range(1, max_retries + 1):
            max_reference_inputs = _image_reference_budget(
                attempt,
                max_retries=max_retries,
                total_references=total_references,
                has_edit_image=edit_image_bytes is not None,
            )
            contents = build_contents(max_reference_inputs=max_reference_inputs)
            _throttle_api_call()
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=_build_generate_content_config(
                        model_name=model_name,
                        thinking_level=None,
                        response_modalities=["IMAGE"],
                    ),
                )
            except Exception as exc:
                last_error = exc
                overload = _is_server_overload(exc)
                wait = _backoff_seconds(attempt, is_overload=overload)
                logger.warning("이미지 생성 API 호출 실패 (모델 %s, 시도 %d/%d, 대기 %.0f초): %s", model_name, attempt, max_retries, wait, exc)
                if attempt < max_retries:
                    time.sleep(wait)
                continue

            text_parts: list[str] = []
            image_bytes: bytes | None = None
            mime_type = "image/png"
            parts = _collect_candidate_parts(response)

            for part in parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                inline_data = getattr(part, "inline_data", None)
                if inline_data is None:
                    continue
                mime_type = getattr(inline_data, "mime_type", mime_type) or mime_type
                data = getattr(inline_data, "data", None)
                if isinstance(data, str):
                    image_bytes = base64.b64decode(data)
                elif isinstance(data, bytes):
                    image_bytes = data
                elif hasattr(part, "as_image"):
                    buffer = io.BytesIO()
                    part.as_image().save(buffer, format="PNG")
                    image_bytes = buffer.getvalue()
                    mime_type = "image/png"
                if image_bytes:
                    break

            if image_bytes is not None:
                self.last_generation_model = model_name
                if attempt > 1:
                    logger.info("이미지 생성 성공 (모델 %s, 시도 %d/%d)", model_name, attempt, max_retries)
                return image_bytes, mime_type, "\n".join(text_parts).strip()

            last_error = RuntimeError(f"Gemini response did not contain image data for model {model_name}")
            wait = _backoff_seconds(attempt, is_overload=True)
            logger.warning(
                "이미지 데이터 없는 응답 수신 (모델 %s, 시도 %d/%d, 대기 %.0f초, parts=%d, text_parts=%d, refs=%d/%d, pruned=%s, finish=%s)",
                model_name,
                attempt,
                max_retries,
                wait,
                len(parts),
                len(text_parts),
                len(contents) - 1 - (1 if edit_image_bytes is not None else 0),
                total_references,
                max_reference_inputs is not None,
                ",".join(_candidate_finish_reasons(response)) or "none",
            )
            if attempt < max_retries:
                time.sleep(wait)

        raise last_error  # type: ignore[misc]


class GoogleWorkspaceClient:
    DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
    SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
    SCOPES = [DRIVE_SCOPE, SHEETS_SCOPE]

    def __init__(self, settings: WebtoonSettings, *, interactive_auth: bool = False) -> None:
        self.settings = settings
        self.interactive_auth = interactive_auth
        self._drive_service = None
        self._sheets_service = None
        self._creds = self._load_user_credentials()

    def _load_user_credentials(self) -> Credentials:
        token_file = self.settings.google_oauth_token_file
        creds: Credentials | None = None

        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), self.SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_credentials(creds)
            return creds

        if not self.interactive_auth:
            raise RuntimeError(
                "Google OAuth 사용자 토큰이 없습니다. "
                "먼저 `python -m agents.webtoon.main google-auth`를 실행해 사용자 인증을 완료하세요."
            )

        return self.authorize_user()

    def _save_credentials(self, creds: Credentials) -> None:
        self.settings.google_oauth_token_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.google_oauth_token_file.write_text(creds.to_json(), encoding="utf-8")

    def authorize_user(self) -> Credentials:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.settings.google_oauth_client_secret_file),
            self.SCOPES,
        )
        creds = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            authorization_prompt_message="브라우저에서 Google 인증을 완료하세요: {url}",
            success_message="Google 인증이 완료되었습니다. 터미널로 돌아가세요.",
            open_browser=False,
        )
        self._save_credentials(creds)
        self._creds = creds
        return creds

    @property
    def drive_service(self):
        if self._drive_service is None:
            self._drive_service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._drive_service

    @property
    def sheets_service(self):
        if self._sheets_service is None:
            self._sheets_service = build("sheets", "v4", credentials=self._creds, cache_discovery=False)
        return self._sheets_service

    def smoke_test_drive(self) -> dict[str, Any]:
        root = (
            self.drive_service.files()
            .get(
                fileId=self.settings.google_drive_root_folder_id,
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        smoke_folder = self.ensure_folder(self.settings.google_drive_root_folder_id, "smoke-tests")
        marker_name = f"drive-smoke-{int(time.time())}.txt"
        marker = self.upload_bytes(
            smoke_folder["id"],
            marker_name,
            b"drive smoke test",
            "text/plain",
            make_public=False,
        )
        return {"root": root, "smoke_folder": smoke_folder, "uploaded_marker": marker}

    def smoke_test_sheets(self) -> dict[str, Any]:
        metadata = (
            self.sheets_service.spreadsheets()
            .get(spreadsheetId=self.settings.google_sheets_spreadsheet_id, fields="properties.title,sheets.properties.title")
            .execute()
        )
        headers = ["timestamp", "service", "status"]
        row = {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z",
            "service": "sheets",
            "status": "ok",
        }
        append_result = self.append_row("smoke_tests", headers, row)
        return {"metadata": metadata, "append_result": append_result}

    def ensure_folder(self, parent_id: str, folder_name: str) -> dict[str, Any]:
        query = (
            f"'{parent_id}' in parents and "
            f"name = '{_escape_drive_query(folder_name)}' and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        result = (
            self.drive_service.files()
            .list(
                q=query,
                fields="files(id,name,webViewLink)",
                pageSize=1,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        files = result.get("files", [])
        if files:
            return files[0]

        folder = (
            self.drive_service.files()
            .create(
                body={
                    "name": folder_name,
                    "parents": [parent_id],
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return folder

    def ensure_drive_path(self, *parts: str) -> list[dict[str, Any]]:
        current_id = self.settings.google_drive_root_folder_id
        folders: list[dict[str, Any]] = []
        for part in parts:
            folder = self.ensure_folder(current_id, part)
            folders.append(folder)
            current_id = folder["id"]
        return folders

    def upload_bytes(
        self,
        folder_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        *,
        make_public: bool = False,
    ) -> dict[str, Any]:
        media = MediaInMemoryUpload(data, mimetype=mime_type, resumable=False)
        try:
            created = (
                self.drive_service.files()
                .create(
                    body={"name": filename, "parents": [folder_id]},
                    media_body=media,
                    fields="id,name,mimeType,webViewLink,webContentLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as error:
            error_text = str(error)
            if "storageQuotaExceeded" in error_text:
                raise RuntimeError(
                    "Google Drive 업로드 실패: 현재 루트 폴더가 서비스 계정의 저장용량이 없는 My Drive 영역에 있습니다. "
                    "루트 폴더를 Shared Drive로 옮기거나, 사용자 OAuth 위임 방식으로 전환해야 합니다."
                ) from error
            raise
        if make_public:
            self.drive_service.permissions().create(
                fileId=created["id"],
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
            ).execute()
            created["public_download_url"] = self.make_public_download_url(created["id"])
        return created

    def upload_json(
        self,
        folder_id: str,
        filename: str,
        payload: dict[str, Any],
        *,
        make_public: bool = False,
    ) -> dict[str, Any]:
        return self.upload_bytes(
            folder_id,
            filename,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
            make_public=make_public,
        )

    def make_public_download_url(self, file_id: str) -> str:
        return f"https://drive.usercontent.google.com/download?id={file_id}&export=download"

    def ensure_sheet(self, sheet_name: str, headers: list[str]) -> None:
        metadata = (
            self.sheets_service.spreadsheets()
            .get(spreadsheetId=self.settings.google_sheets_spreadsheet_id, fields="sheets.properties.title")
            .execute()
        )
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        if sheet_name not in titles:
            self.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=self.settings.google_sheets_spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            ).execute()
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=self.settings.google_sheets_spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()
            return

        first_row = (
            self.sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=self.settings.google_sheets_spreadsheet_id, range=f"{sheet_name}!1:1")
            .execute()
        )
        values = first_row.get("values", [])
        if not values:
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=self.settings.google_sheets_spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()

    def append_row(self, sheet_name: str, headers: list[str], row: dict[str, Any]) -> dict[str, Any]:
        self.ensure_sheet(sheet_name, headers)
        ordered_values = [row.get(header, "") for header in headers]
        result = (
            self.sheets_service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.settings.google_sheets_spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [ordered_values]},
            )
            .execute()
        )
        return result

    def auth_status(self) -> dict[str, Any]:
        token_file = self.settings.google_oauth_token_file
        return {
            "client_secret_file": str(self.settings.google_oauth_client_secret_file),
            "token_file": str(token_file),
            "token_exists": token_file.exists(),
            "scopes": self.SCOPES,
        }


_CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"


def upload_image_for_instagram(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """Upload image to catbox.moe and return a direct public URL.

    Google Drive URLs have cross-origin-resource-policy: same-site which
    prevents Instagram's servers from fetching the image.  This helper hosts
    the image on a publicly accessible CDN instead.
    """
    mime_type = "image/jpeg" if filename.endswith(".jpg") else "image/png"
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            _CATBOX_UPLOAD_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, image_bytes, mime_type)},
        )
    if response.status_code != 200 or not response.text.startswith("https://"):
        raise RuntimeError(f"이미지 호스팅 업로드 실패: status={response.status_code} body={response.text[:200]}")
    return response.text.strip()


class InstagramGraphClient:
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=f"https://graph.facebook.com/{settings.instagram_graph_api_version}",
            timeout=60.0,
        )

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        response = self.client.get(path, params={"access_token": self.settings.instagram_access_token, **params})
        if response.is_error:
            raise RuntimeError(self._build_error_message(response))
        return response.json()

    def _post(self, path: str, **data: Any) -> dict[str, Any]:
        response = self.client.post(path, data={"access_token": self.settings.instagram_access_token, **data})
        if response.is_error:
            raise RuntimeError(self._build_error_message(response))
        return response.json()

    @staticmethod
    def _build_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return f"Instagram API {response.status_code}: {payload}"

    def smoke_test(self) -> dict[str, Any]:
        return self._get(f"/{self.settings.instagram_business_account_id}", fields="id,username")

    def create_media_container(self, image_url: str, caption: str) -> str:
        payload = self._post(
            f"/{self.settings.instagram_business_account_id}/media",
            image_url=image_url,
            caption=caption,
        )
        creation_id = payload.get("id")
        if not creation_id:
            raise RuntimeError(f"Instagram media container creation failed: {payload}")
        return creation_id

    def create_carousel_item_container(self, image_url: str) -> str:
        payload = self._post(
            f"/{self.settings.instagram_business_account_id}/media",
            image_url=image_url,
            is_carousel_item=True,
        )
        creation_id = payload.get("id")
        if not creation_id:
            raise RuntimeError(f"Instagram carousel item creation failed: {payload}")
        return creation_id

    def create_carousel_container(self, children: list[str], caption: str) -> str:
        payload = self._post(
            f"/{self.settings.instagram_business_account_id}/media",
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
        )
        creation_id = payload.get("id")
        if not creation_id:
            raise RuntimeError(f"Instagram carousel container creation failed: {payload}")
        return creation_id

    def get_container_status(self, creation_id: str) -> dict[str, Any]:
        return self._get(f"/{creation_id}", fields="id,status_code,status")

    def publish_media(self, creation_id: str) -> dict[str, Any]:
        return self._post(
            f"/{self.settings.instagram_business_account_id}/media_publish",
            creation_id=creation_id,
        )

    def publish_image(
        self,
        image_url: str,
        caption: str,
        *,
        poll_interval_seconds: int = 5,
        poll_timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        creation_id = self.create_media_container(image_url=image_url, caption=caption)

        started_at = time.monotonic()
        while True:
            status = self.get_container_status(creation_id)
            status_code = status.get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(f"Instagram media container processing failed: {status}")
            if time.monotonic() - started_at > poll_timeout_seconds:
                raise TimeoutError(f"Timed out waiting for Instagram media container: {status}")
            time.sleep(poll_interval_seconds)

        publish_result = self.publish_media(creation_id)
        publish_id = publish_result.get("id")
        permalink = {}
        if publish_id:
            permalink = self._get(f"/{publish_id}", fields="id,permalink")
        return {
            "creation_id": creation_id,
            "publish_result": publish_result,
            "media": permalink,
        }

    def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        *,
        poll_interval_seconds: int = 5,
        poll_timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        if not image_urls:
            raise ValueError("image_urls must not be empty")

        child_creation_ids = [self.create_carousel_item_container(image_url) for image_url in image_urls]

        started_at = time.monotonic()
        for creation_id in child_creation_ids:
            while True:
                status = self.get_container_status(creation_id)
                status_code = status.get("status_code")
                if status_code == "FINISHED":
                    break
                if status_code == "ERROR":
                    raise RuntimeError(f"Instagram carousel item processing failed: {status}")
                if time.monotonic() - started_at > poll_timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for Instagram carousel item: {status}")
                time.sleep(poll_interval_seconds)

        creation_id = self.create_carousel_container(child_creation_ids, caption=caption)

        while True:
            status = self.get_container_status(creation_id)
            status_code = status.get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(f"Instagram carousel container processing failed: {status}")
            if time.monotonic() - started_at > poll_timeout_seconds:
                raise TimeoutError(f"Timed out waiting for Instagram carousel container: {status}")
            time.sleep(poll_interval_seconds)

        publish_result = self.publish_media(creation_id)
        publish_id = publish_result.get("id")
        permalink = {}
        if publish_id:
            permalink = self._get(f"/{publish_id}", fields="id,permalink")
        return {
            "creation_id": creation_id,
            "child_creation_ids": child_creation_ids,
            "publish_result": publish_result,
            "media": permalink,
        }


def build_smoke_test_image_bytes() -> bytes:
    image = Image.new("RGB", (960, 540), color=(245, 245, 240))
    draw = ImageDraw.Draw(image)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    draw.text((48, 48), f"OCR SMOKE TEST {timestamp}", fill=(20, 20, 20))
    draw.text((48, 120), "Drive Sheets Instagram", fill=(20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def convert_image_bytes(image_bytes: bytes, output_format: str) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        converted = image.convert("RGB")
        buffer = io.BytesIO()
        converted.save(buffer, format=output_format)
        return buffer.getvalue()


def save_image(image_bytes: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)
