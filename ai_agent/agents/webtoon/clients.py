from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import re
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


def normalize_dialogue_text(text: str) -> str:
    normalized = sanitize_public_text(text, fallback="")
    for before, after in DIALOGUE_RENDER_REPLACEMENTS:
        normalized = normalized.replace(before, after)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.strip()


_LLM_TIMEOUT = 60_000      # 60초 (밀리초 단위)
_IMAGE_TIMEOUT = 180_000   # 180초 (밀리초 단위)


class GeminiTextClient:
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.image_api_key,
            http_options=types.HttpOptions(timeout=_LLM_TIMEOUT),
        )

    def _generate_text(self, prompt: str, *, max_retries: int = 3) -> str:
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.llm_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(),
                )
                text = _extract_gemini_text(response)
                if not text:
                    raise RuntimeError("Gemini text response did not contain text output")
                return text
            except Exception as exc:
                if attempt >= max_retries:
                    raise
                logger.warning("LLM 텍스트 생성 실패 (시도 %d/%d): %s", attempt, max_retries, exc)
                time.sleep(2 ** attempt)
        raise RuntimeError("Unreachable")

    def smoke_test(self) -> dict[str, Any]:
        return {
            "model": self.settings.llm_model,
            "response_text": self._generate_text("웹툰 에이전트 연결 테스트입니다. 정확히 'LLM_OK'만 출력하세요."),
        }

    def build_creative_brief(self, topic: str) -> dict[str, Any]:
        prompt = (
            "당신은 독일생활 웹툰 기획자입니다.\n"
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
            "- 한 장면 안에는 반드시 이 둘만 등장한다. 추가 인물, 분신, 잘린 얼굴 스티커, 떠 있는 두상은 만들지 마세요.\n\n"
            "## 아트 스타일\n"
            "- 한국식 디지털 웹툰 스타일 (네이버/카카오 웹툰 느낌)\n"
            "- 굵고 깔끔한 외곽선, 생동감 있는 색상, 과장된 표정과 역동적 포즈\n"
            "- 만화적 이펙트 사용 (놀람 표시, 땀방울, 분노 마크, 반짝임 등)\n"
            "- 배경은 독일 생활 환경을 디테일하게 묘사\n\n"
            "## 텍스트 규칙 (매우 중요)\n"
            "- title, caption, hashtags, dialogue_lines 어디에서든 '고양이'라는 단어를 절대 사용하지 마세요.\n"
            "- 등장인물은 사람을 대체하는 캐릭터이므로 '고양이'를 직접 언급하면 안 됩니다.\n"
            "- 예: '고양이들의 독일 마트 도전기' (X) → '독일 마트 도전기!' (O)\n"
            "- 예: '고양이 웹툰' (X) → '독일생활 적응기' (O)\n\n"
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
            "- scene_prompt에 'exactly two recurring protagonists only, no extra heads, no duplicate faces, no sticker portraits, no costume changes'를 포함하세요.\n"
            "- scene_prompt에 반드시 'The characters do NOT wear any clothing, shoes, or accessories. They have natural fur only.'를 포함하세요.\n"
            "- scene_prompt에 반드시 'Do NOT draw any speech bubbles, dialogue text, or captions in the image.'를 포함하세요.\n"
            "- 독일어 배경 텍스트가 필요하면 실존 브랜드명만 정확한 철자로 사용하세요.\n\n"
            "## dialogue_lines 규칙\n"
            "- 후처리 렌더링용 정확한 말풍선 대사 텍스트이며 반드시 한국어로만 작성하세요.\n"
            "- 각 panel의 dialogue_lines는 2~3줄이 이상적. 최대 4줄.\n"
            "- 한 줄 길이는 12~20자가 자연스럽고, 꼭 필요할 때만 조금 더 길게.\n"
            "- 독일어는 dialogue_lines에 넣지 마세요.\n"
            "- '고양이'라는 단어를 대사에서도 사용하지 마세요.\n"
            "- 대사 순서: 콜라 대사를 먼저, 제로 대사를 나중에 (콜라 1~2줄 → 제로 1~2줄).\n"
            "- 매우 중요: 이 순서는 말풍선 배치와 직접 연결됩니다. "
            "앞쪽 줄(콜라 대사)은 화면 왼쪽 말풍선에, 뒤쪽 줄(제로 대사)은 화면 오른쪽 말풍선에 자동 배치됩니다. "
            "따라서 대사 내용이 반드시 해당 캐릭터의 성격과 상황에 맞아야 합니다.\n"
            "- 매 패널마다 반드시 두 캐릭터 모두 대사가 있어야 합니다 (각각 최소 1줄). "
            "한 캐릭터만 말하는 패널은 만들지 마세요.\n"
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
            "panels는 정확히 6개여야 합니다.\n"
            "각 panel에는 panel_no, scene_prompt, dialogue_lines를 넣으세요.\n"
            "해시태그는 문자열 배열로 작성하되, '고양이'라는 단어를 포함하지 마세요.\n"
            '필드: "title", "image_prompt", "caption", "hashtags", "character_notes", "panels"\n'
            f"주제: {topic}"
        )
        payload = _extract_json_object(self._generate_text(prompt))
        payload["model"] = self.settings.llm_model
        sanitized_topic = sanitize_public_text(topic, fallback="독일생활 적응기")
        payload["title"] = sanitize_public_text(
            str(payload.get("title", "")).strip() or sanitized_topic,
            fallback=sanitized_topic or "독일생활 적응기",
        )
        payload["caption"] = sanitize_public_text(
            str(payload.get("caption", "")).strip(),
            fallback=f"{payload['title']} 에피소드를 준비했습니다.",
        )
        payload["hashtags"] = sanitize_public_hashtags(payload.get("hashtags", []))
        payload["image_prompt"] = str(payload.get("image_prompt", "")).strip() or (
            "Korean digital webtoon style, bold clean outlines, vibrant colors, "
            "expressive exaggerated facial expressions, dynamic poses, "
            "anthropomorphic cats walking upright on two legs using front paws as hands, "
            "manga-style emotion effects, detailed background"
        )
        payload["character_notes"] = str(payload.get("character_notes", "")).strip()
        raw_panels = payload.get("panels", [])
        if len(raw_panels) > 6:
            logger.warning("LLM이 %d개 패널을 반환했습니다. 처음 6개만 사용합니다.", len(raw_panels))
        panels: list[dict[str, Any]] = []
        for index, panel in enumerate(raw_panels[:6], start=1):
            dialogue_lines = [
                normalize_dialogue_text(str(line).strip())
                for line in panel.get("dialogue_lines", [])
                if str(line).strip()
            ]
            dialogue_lines = [line for line in dialogue_lines if line]
            for line in dialogue_lines:
                if len(line) > 40:
                    logger.warning("패널 %d 대사가 40자 초과 (%d자): %s", index, len(line), line[:50])
            panels.append(
                {
                    "panel_no": int(panel.get("panel_no", index)),
                    "scene_prompt": str(panel.get("scene_prompt", "")).strip(),
                    "dialogue_lines": dialogue_lines,
                }
            )
        while len(panels) < 6:
            logger.warning("LLM이 6개 미만의 패널을 반환하여 빈 패널을 추가합니다. (현재 %d개)", len(panels))
            panels.append(
                {
                    "panel_no": len(panels) + 1,
                    "scene_prompt": "",
                    "dialogue_lines": [],
                }
            )
        payload["panels"] = self._normalize_korean_dialogues(panels)
        return payload

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
            "각 패널의 대사는 가능하면 2~3줄, 길어도 4줄 이내로 유지하세요.\n"
            "한 줄은 보통 12~22자 정도가 가장 좋지만, 어색해지면 40자 이내에서 자연스럽게 조정하세요.\n"
            "한 컷이 개별 이미지이므로 정보 전달이 충분하도록 요약을 지나치게 하지 마세요.\n"
            "중요: '고양이'라는 단어는 어떤 대사에도 사용하지 마세요.\n"
            "중요: 두 캐릭터는 친한 친구 사이이므로 반드시 반말을 사용하세요. "
            "존댓말(~요, ~습니다, ~세요 등)은 절대 사용하지 마세요.\n"
            "대사 순서를 유지하세요: 앞쪽 줄은 콜라(왼쪽 캐릭터), 뒤쪽 줄은 제로(오른쪽 캐릭터)의 대사입니다.\n"
            "패널 수는 유지하세요.\n"
            "JSON 배열만 반환하세요. 각 항목은 dialogue_lines 배열만 포함합니다.\n"
            f"입력 패널: {json.dumps(panels, ensure_ascii=False)}"
        )
        try:
            translated = _extract_json_object(self._generate_text(prompt))
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
            lines = [
                normalize_dialogue_text(str(line).strip())
                for line in translated_panel.get("dialogue_lines", [])
                if str(line).strip()
            ]
            lines = [line for line in lines if line]
            normalized.append({**panel, "dialogue_lines": lines or panel.get("dialogue_lines", [])})
        return normalized + panels[len(normalized) :]


class GeminiOcrClient:
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.image_api_key,
            http_options=types.HttpOptions(timeout=_LLM_TIMEOUT),
        )

    def _generate_text(
        self,
        prompt: str,
        *,
        image_bytes: bytes | None = None,
        mime_type: str = "image/png",
        image_parts: list[tuple[bytes, str]] | None = None,
        max_retries: int = 3,
    ) -> str:
        contents: list[Any] = [prompt]
        if image_bytes is not None:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        for part_bytes, part_mime_type in image_parts or []:
            contents.append(types.Part.from_bytes(data=part_bytes, mime_type=part_mime_type))
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.ocr_model,
                    contents=contents,
                    config=types.GenerateContentConfig(),
                )
                text = _extract_gemini_text(response)
                if not text:
                    raise RuntimeError("Gemini OCR response did not contain text output")
                return text
            except Exception as exc:
                if attempt >= max_retries:
                    raise
                logger.warning("OCR 텍스트 생성 실패 (시도 %d/%d): %s", attempt, max_retries, exc)
                time.sleep(2 ** attempt)
        raise RuntimeError("Unreachable")

    def smoke_test(self, image_bytes: bytes, mime_type: str = "image/png") -> dict[str, Any]:
        extracted = self.extract_text(image_bytes, mime_type=mime_type)
        return {"model": self.settings.ocr_model, "extracted_text": extracted}

    def extract_text(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raw_text = self._generate_text(
            "이미지에 보이는 텍스트만 줄바꿈 포함 원문 그대로 추출하세요.\n"
            "설명문, 요약, '다음과 같습니다' 같은 서술 문장은 절대 추가하지 마세요.\n"
            "텍스트가 없으면 빈 문자열만 반환하세요.",
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        return clean_ocr_text(raw_text)

    def plan_text_corrections(self, intended_lines: list[str], ocr_text: str) -> dict[str, Any]:
        if not intended_lines:
            return {
                "rerender_required": False,
                "corrected_text_lines": [],
                "issues": [],
                "edit_instruction": "",
                "confidence": 1.0,
            }

        prompt = (
            "당신은 웹툰 이미지의 OCR 결과를 검수하는 편집자입니다.\n"
            "의도한 텍스트와 OCR 결과를 비교해 오탈자, 누락, 글자 깨짐 여부를 판단하세요.\n"
            "중요: 배경 간판, 기계 화면, 표지판의 독일어/영어 텍스트는 완전히 무시하세요. "
            "말풍선 안의 한국어 대사 텍스트만 비교 대상입니다.\n"
            "중요: 말풍선 너비 제한으로 한 대사가 여러 줄로 자동 줄바꿈될 수 있습니다. "
            "예를 들어 '그래도 덕분에 마트 구경은 실컷 했네?'가 OCR에서 "
            "'그래도 덕분에 마트 구경은 실컷'과 '했네?'로 나뉘어도 이는 정상적인 줄바꿈이며 오류가 아닙니다. "
            "줄바꿈 위치 차이만으로는 절대 rerender_required를 true로 설정하지 마세요.\n"
            "rerender_required=true는 실제 오탈자(글자가 다름, 누락, 깨짐, 다른 언어)인 경우에만 해당합니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "rerender_required"(bool), "corrected_text_lines"(array), "issues"(array), '
            '"edit_instruction"(string), "confidence"(number)\n'
            "corrected_text_lines에는 이미지에 최종적으로 정확히 보여야 하는 텍스트 줄만 넣으세요.\n"
            f"의도한 텍스트: {json.dumps(intended_lines, ensure_ascii=False)}\n"
            f"OCR 결과: {ocr_text}"
        )
        payload = _extract_json_object(self._generate_text(prompt))
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

    def check_background_text(self, image_bytes: bytes, scene_prompt: str, mime_type: str = "image/png") -> dict[str, Any]:
        prompt = (
            "이미지 속 배경에 보이는 독일어/영어 텍스트(간판, 화면 UI, 표지판 등)를 모두 추출하세요.\n"
            "한국어 말풍선 텍스트는 완전히 무시하세요.\n"
            "추출한 배경 텍스트에 철자 오류가 있는지 확인하세요.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "background_texts"(array of string), "has_errors"(bool), "corrections"(array)\n'
            'corrections 항목: {"found": "이미지에 보이는 텍스트", "correct": "올바른 철자", "reason": "설명"}\n'
            "배경 텍스트가 없거나 철자 오류가 없으면 has_errors=false, corrections=[]을 반환하세요.\n"
            f"장면 설명 참고: {scene_prompt}"
        )
        payload = _extract_json_object(self._generate_text(prompt, image_bytes=image_bytes, mime_type=mime_type))
        payload["has_errors"] = bool(payload.get("has_errors", False))
        payload["background_texts"] = [clean_ocr_text(str(t)) for t in payload.get("background_texts", []) if str(t).strip()]
        payload["background_texts"] = [t for t in payload["background_texts"] if t]
        payload["corrections"] = [
            {
                "found": clean_ocr_text(str(item.get("found", "")).strip()),
                "correct": clean_ocr_text(str(item.get("correct", "")).strip()),
                "reason": str(item.get("reason", "")).strip(),
            }
            for item in payload.get("corrections", [])
            if clean_ocr_text(str(item.get("found", "")).strip()) and clean_ocr_text(str(item.get("correct", "")).strip())
        ]
        return payload

    def check_character_composition(self, image_bytes: bytes, scene_prompt: str, mime_type: str = "image/png") -> dict[str, Any]:
        prompt = (
            "웹툰 패널 이미지를 보고 등장인물 구성이 올바른지 검사하세요.\n"
            "기준은 다음과 같습니다.\n"
            "- 정확히 두 주인공만 한 번씩 등장해야 합니다.\n"
            "- 이미지 전체에 콜라 1명, 제로 1명만 있어야 합니다. 총 캐릭터 수가 2명을 넘으면 무조건 오류입니다.\n"
            "- 검은 캐릭터 콜라는 왼쪽, 회색 줄무늬 캐릭터 제로는 오른쪽에 있어야 합니다.\n"
            "- 콜라는 제로보다 약간 더 크게 보여야 합니다.\n"
            "- 하늘이나 상단 여백에 떠 있는 추가 캐릭터, 잘린 상반신, 중복 얼굴, 반응용 컷인, 분신이 있으면 오류입니다.\n"
            "- 부분적으로 잘린 추가 캐릭터도 오류입니다.\n"
            "- 한 장 안에 같은 장면이 위아래 또는 좌우로 반복되거나, 두 컷이 합쳐진 것처럼 보이면 오류입니다.\n"
            "- 하나의 이미지가 만화 컷 여러 개로 분할되어 있거나, 같은 캐릭터 쌍이 두 번 보이면 오류입니다.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "edit_instruction"(string), '
            '"kolla_count"(int), "zero_count"(int), "extra_character_count"(int), "duplicate_scene_detected"(bool)\n'
            "문제가 없으면 has_issues=false, issues=[], edit_instruction=''로 반환하세요.\n"
            f"장면 설명 참고: {scene_prompt}"
        )
        payload = _extract_json_object(self._generate_text(prompt, image_bytes=image_bytes, mime_type=mime_type))
        payload["kolla_count"] = max(0, int(payload.get("kolla_count", 0) or 0))
        payload["zero_count"] = max(0, int(payload.get("zero_count", 0) or 0))
        payload["extra_character_count"] = max(0, int(payload.get("extra_character_count", 0) or 0))
        payload["duplicate_scene_detected"] = bool(payload.get("duplicate_scene_detected", False))
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        computed_issue = (
            payload.get("has_issues", False)
            or payload["kolla_count"] != 1
            or payload["zero_count"] != 1
            or payload["extra_character_count"] > 0
            or payload["duplicate_scene_detected"]
        )
        payload["has_issues"] = bool(computed_issue)
        if payload["has_issues"] and not payload["edit_instruction"]:
            payload["edit_instruction"] = (
                "Remove every extra duplicate or floating character so the image contains exactly one Kolla on the left "
                "and one Zero on the right. Remove any repeated top/bottom scene layout and leave no extra body in the upper area."
            )
        return payload

    def check_character_reference_consistency(
        self,
        image_bytes: bytes,
        scene_prompt: str,
        reference_images: list[tuple[bytes, str]],
        mime_type: str = "image/png",
    ) -> dict[str, Any]:
        prompt = (
            "첫 번째 이미지는 현재 생성된 웹툰 이미지이고, 뒤이어 제공되는 이미지는 반드시 따라야 하는 캐릭터 참조 이미지들입니다.\n"
            "현재 이미지의 콜라와 제로가 참조 이미지의 동일 캐릭터와 외형적으로 일치하는지 검사하세요.\n"
            "검사 기준은 다음과 같습니다.\n"
            "- 얼굴형, 눈 색, 귀 색, 줄무늬 패턴, 꼬리 모양, 체형, 색 분포가 참조와 일치해야 합니다.\n"
            "- 같은 시리즈의 반복 캐릭터이므로 컷마다 다른 개체처럼 보이면 오류입니다.\n"
            "- 그림체는 약간 달라도 되지만, 캐릭터 정체성이 달라 보이면 오류입니다.\n"
            "- 콜라는 항상 왼쪽의 검은 캐릭터, 제로는 항상 오른쪽의 회색 줄무늬 캐릭터여야 합니다.\n"
            "- 참조 이미지를 느슨한 영감이 아니라 고정된 마스터 레퍼런스로 간주하세요.\n"
            "JSON 객체만 반환하세요.\n"
            '필드: "has_issues"(bool), "issues"(array), "edit_instruction"(string)\n'
            "문제가 없으면 has_issues=false, issues=[], edit_instruction=''로 반환하세요.\n"
            f"장면 설명 참고: {scene_prompt}"
        )
        payload = _extract_json_object(
            self._generate_text(
                prompt,
                image_bytes=image_bytes,
                mime_type=mime_type,
                image_parts=reference_images,
            )
        )
        payload["has_issues"] = bool(payload.get("has_issues", False))
        payload["issues"] = [str(issue).strip() for issue in payload.get("issues", []) if str(issue).strip()]
        payload["edit_instruction"] = str(payload.get("edit_instruction", "")).strip()
        return payload


class GeminiImageClient:
    def __init__(self, settings: WebtoonSettings) -> None:
        self.settings = settings
        self.client = genai.Client(
            api_key=settings.image_api_key,
            http_options=types.HttpOptions(timeout=_IMAGE_TIMEOUT),
        )

    def smoke_test(self) -> dict[str, Any]:
        image_bytes, mime_type, text = self.generate_image("A tiny cartoon banana with a speech bubble saying smoke test.")
        return {"model": self.settings.image_model, "mime_type": mime_type, "bytes": len(image_bytes), "text": text}

    def generate_image(
        self,
        prompt: str,
        *,
        reference_image_paths: list[Path] | None = None,
        edit_image_bytes: bytes | None = None,
        edit_image_mime_type: str | None = None,
        max_retries: int = 3,
    ) -> tuple[bytes, str, str]:
        contents: list[Any] = [prompt]
        if edit_image_bytes is not None:
            contents.append(
                types.Part.from_bytes(
                    data=edit_image_bytes,
                    mime_type=edit_image_mime_type or "image/png",
                )
            )
        for path in reference_image_paths or []:
            contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=_guess_mime_type(path)))

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.image_model,
                    contents=contents,
                    config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
                )
            except Exception as exc:
                last_error = exc
                logger.warning("이미지 생성 API 호출 실패 (시도 %d/%d): %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                continue

            text_parts: list[str] = []
            image_bytes: bytes | None = None
            mime_type = "image/png"

            parts = getattr(response, "parts", None)
            if parts is None:
                parts = []
                for candidate in getattr(response, "candidates", []) or []:
                    content = getattr(candidate, "content", None)
                    if content and getattr(content, "parts", None):
                        parts.extend(content.parts)

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
                if attempt > 1:
                    logger.info("이미지 생성 성공 (시도 %d/%d)", attempt, max_retries)
                return image_bytes, mime_type, "\n".join(text_parts).strip()

            last_error = RuntimeError("Gemini response did not contain image data")
            logger.warning("이미지 데이터 없는 응답 수신 (시도 %d/%d)", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(2 ** attempt)

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
        return f"https://drive.google.com/uc?export=download&id={file_id}"

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
