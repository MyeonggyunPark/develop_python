from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agents.webtoon.clients import (
    GeminiImageClient,
    GeminiOcrClient,
    GeminiTextClient,
    _build_thinking_config,
    _compact_image_prompt_for_model,
    _extract_json_object,
    build_smoke_test_image_bytes,
    clean_ocr_text,
    flatten_speaker_dialogues,
    normalize_dialogue_text,
    normalize_speaker_dialogues,
    sanitize_public_hashtags,
    sanitize_public_text,
)
from agents.webtoon.config import WebtoonSettings


class TestExtractJsonObject:
    def test_plain_json(self):
        result = _extract_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_object(text)
        assert result == {"key": "value"}

    def test_json_array(self):
        result = _extract_json_object('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_json_with_whitespace(self):
        result = _extract_json_object('  \n  {"key": "value"}  \n  ')
        assert result == {"key": "value"}

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="유효한 JSON"):
            _extract_json_object("this is not json")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            _extract_json_object("")

    def test_nested_code_block(self):
        text = '```\n{"panels": [{"panel_no": 1}]}\n```'
        result = _extract_json_object(text)
        assert result["panels"][0]["panel_no"] == 1

    def test_invalid_json_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agents.webtoon.clients"):
            with pytest.raises(ValueError):
                _extract_json_object("not valid json {{{")
        assert "JSON 파싱 실패" in caplog.text


class TestThinkingConfig:
    def test_uses_budget_mapping_for_gemini_25_models(self):
        config = _build_thinking_config("gemini-2.5-flash", "high")
        assert config is not None
        assert config.thinking_budget == 8192

    def test_uses_thinking_level_for_gemini_3_models(self):
        config = _build_thinking_config("gemini-3-flash-preview", "high")
        assert config is not None
        assert str(config.thinking_level).endswith("HIGH")


class TestImagePromptCompaction:
    def test_compacts_long_prompt_for_gemini_2_5_image_models(self):
        long_prompt = "\n".join(
            [
                "intro line",
                "Korean digital webtoon style, bold clean outlines",
                "Kolla should appear slightly larger than Zero, about 10 to 15 percent bigger in body scale",
                "Keep the Kolla-to-Zero size ratio stable across the whole episode",
                "exactly two recurring cat protagonists only",
                "strictly upright bipedal posture",
                "The characters do NOT wear any clothing, shoes, or accessories. They have natural fur only.",
                "Do NOT draw any speech bubbles, dialogue text, or captions in the image.",
                "썸네일은 1컷의 반복이 아니라 별도의 예고 장면이어야 한다. 1컷과 같은 나란히 서기 포즈는 피한다.",
                "본문 1~6컷 어느 곳에서도 썸네일의 배경 구조, 서브로케이션, 카메라 거리, 대표 간판, 대표 소품 배치를 다시 사용하면 안 된다.",
                "이 이미지는 단일 패널 한 컷만 담아야 한다. 위아래 두 장면, 좌우 분할, 반복된 동일 장면, 만화 페이지처럼 여러 컷이 섞인 구도를 절대 만들지 않는다.",
                "Show these key props clearly: passport, suitcase.",
            ]
            + ["filler"] * 800
        )

        compacted = _compact_image_prompt_for_model(long_prompt, "gemini-2.5-flash-image")

        assert len(compacted) < len(long_prompt)
        assert "Korean digital webtoon style" in compacted
        assert "Do NOT draw any speech bubbles" in compacted
        assert "본문 1~6컷 어느 곳에서도" in compacted
        assert "단일 패널 한 컷만 담아야" in compacted
        # Duplicate "filler" lines should be collapsed to at most one occurrence.
        assert compacted.count("filler") <= 1


class TestBuildSmokeTestImage:
    def test_returns_bytes(self):
        result = build_smoke_test_image_bytes()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_is_valid_png(self):
        result = build_smoke_test_image_bytes()
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_contains_dynamic_date(self):
        from datetime import datetime, timezone

        result = build_smoke_test_image_bytes()
        # Just verify it returns valid image bytes (date is rendered in image, not extractable as text)
        assert len(result) > 100


class TestSanitizePublicText:
    def test_removes_banned_word_from_plain_text(self):
        result = sanitize_public_text("독일 고양이툰")
        assert result == "독일 생활툰"

    def test_rewrites_character_name_hashtags(self):
        result = sanitize_public_hashtags(["#고양이웹툰", "#검은고양이", "#회색고양이"])
        assert result == ["#생활툰", "#콜라", "#제로"]


class TestCleanOcrText:
    def test_removes_explanatory_wrapper_lines(self):
        raw = "이미지에 보이는 텍스트는 다음과 같습니다:\n---\n첫 줄\n둘째 줄\n---"
        assert clean_ocr_text(raw) == "첫 줄\n둘째 줄"

    def test_removes_code_fences(self):
        raw = "```text\n안녕\n하세요\n```"
        assert clean_ocr_text(raw) == "안녕\n하세요"


class TestNormalizeDialogueText:
    def test_replaces_ellipsis_for_stable_rendering(self):
        assert normalize_dialogue_text("설명서… 한번 읽어보자") == "설명서... 한번 읽어보자"


class TestSpeakerDialogues:
    def test_prefers_explicit_speaker_dialogues(self):
        panel = {
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["내가 해볼게"]},
                {"speaker": "zero", "dialogue_lines": ["잠깐, 이거 맞아?"]},
            ]
        }
        result = normalize_speaker_dialogues(panel)
        assert result[0]["speaker"] == "kolla"
        assert result[0]["dialogue_lines"] == ["내가 해볼게"]
        assert result[1]["speaker"] == "zero"
        assert result[1]["dialogue_lines"] == ["잠깐, 이거 맞아?"]

    def test_falls_back_to_flat_dialogue_order(self):
        panel = {"dialogue_lines": ["내가 먼저 할게", "버튼이 너무 많아", "이제 내가 볼게"]}
        result = normalize_speaker_dialogues(panel)
        assert flatten_speaker_dialogues(result) == ["내가 먼저 할게", "버튼이 너무 많아", "이제 내가 볼게"]
        assert result[0]["dialogue_lines"] == ["내가 먼저 할게", "버튼이 너무 많아"]
        assert result[1]["dialogue_lines"] == ["이제 내가 볼게"]


class TestPlanTextCorrections:
    def test_keeps_speaker_blocks_when_ocr_review_returns_them(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            del prompt
            captured.update(kwargs)
            return json.dumps(
                {
                    "rerender_required": False,
                    "corrected_text_lines": ["별거 있겠어?", "다 사람 사는 데야.", "혹시 몰라..."],
                    "corrected_speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["별거 있겠어?", "다 사람 사는 데야."]},
                        {"speaker": "zero", "dialogue_lines": ["혹시 몰라..."]},
                    ],
                    "issues": [],
                    "edit_instruction": "",
                    "confidence": 1.0,
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        result = client.plan_text_corrections(
            ["별거 있겠어?", "다 사람 사는 데야.", "혹시 몰라..."],
            "별거 있겠어?\n다 사람\n사는 데야.\n혹시 몰라...",
            speaker_dialogues=[
                {"speaker": "kolla", "dialogue_lines": ["별거 있겠어?", "다 사람 사는 데야."]},
                {"speaker": "zero", "dialogue_lines": ["혹시 몰라..."]},
            ],
        )

        assert result["corrected_speaker_dialogues"][0]["dialogue_lines"] == ["별거 있겠어?", "다 사람 사는 데야."]
        assert result["corrected_speaker_dialogues"][1]["dialogue_lines"] == ["혹시 몰라..."]


class TestGeminiClientApiKeys:
    def test_text_client_uses_gemini_api_key(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                captured["api_key"] = api_key
                captured["timeout"] = http_options.timeout

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)

        GeminiTextClient(_make_dummy_settings(gemini_api_key="image-key"))

        assert captured["api_key"] == "image-key"

    def test_ocr_client_uses_gemini_api_key(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                captured["api_key"] = api_key
                captured["timeout"] = http_options.timeout

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)

        GeminiOcrClient(_make_dummy_settings(gemini_api_key="image-key"))

        assert captured["api_key"] == "image-key"

    def test_image_client_does_not_duplicate_reference_inputs(self, monkeypatch, tmp_path):
        captured: dict[str, object] = {}

        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                captured["contents"] = contents
                return type("Response", (), {"parts": [FakePart(inline_data=FakeInlineData())]})()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)
        monkeypatch.setattr(
            "agents.webtoon.clients.types.Part.from_bytes",
            lambda *, data, mime_type: {"data": data, "mime_type": mime_type},
        )

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"path-image")
        client = GeminiImageClient(_make_dummy_settings(gemini_api_key="image-key"))

        client.generate_image(
            "prompt",
            reference_images=[(b"bytes-image", "image/png")],
            reference_image_paths=[ref_path],
        )

        contents = captured["contents"]
        assert len(contents) == 2
        assert contents[1]["data"] == b"bytes-image"

    def test_image_client_uses_configured_image_model(self, monkeypatch):
        captured_models: list[str] = []
        captured_modalities: list[list[str] | None] = []

        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                del contents
                captured_models.append(model)
                captured_modalities.append(list(config.response_modalities or []))
                return type("Response", (), {"parts": [FakePart(inline_data=FakeInlineData())]})()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)
        client = GeminiImageClient(
            _make_dummy_settings(
                gemini_api_key="image-key",
                image_model="gemini-3.1-flash-image-preview",
            )
        )

        image_bytes, mime_type, _ = client.generate_image("prompt", max_retries=2)

        assert image_bytes == b"fake-image"
        assert mime_type == "image/png"
        assert captured_models == ["gemini-3.1-flash-image-preview"]
        assert captured_modalities == [["IMAGE"]]
        assert client.last_generation_model == "gemini-3.1-flash-image-preview"

    def test_image_client_prunes_reference_inputs_before_last_retry(self, monkeypatch):
        captured_lengths: list[int] = []

        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeModels:
            def __init__(self):
                self.calls = 0

            def generate_content(self, *, model, contents, config):
                del model, config
                self.calls += 1
                captured_lengths.append(len(contents))
                if self.calls < 3:
                    return type("Response", (), {"parts": [FakePart(text="no image")]})()
                return type("Response", (), {"parts": [FakePart(inline_data=FakeInlineData())]})()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)
        monkeypatch.setattr(
            "agents.webtoon.clients.types.Part.from_bytes",
            lambda *, data, mime_type: {"data": data, "mime_type": mime_type},
        )
        monkeypatch.setattr("agents.webtoon.clients.time.sleep", lambda seconds: None)

        client = GeminiImageClient(_make_dummy_settings(gemini_api_key="image-key"))
        references = [(f"img-{idx}".encode(), "image/png") for idx in range(5)]

        image_bytes, mime_type, _ = client.generate_image("prompt", reference_images=references, max_retries=3)

        assert image_bytes == b"fake-image"
        assert mime_type == "image/png"
        assert captured_lengths == [6, 4, 4]

    def test_image_client_prunes_first_attempt_references_for_gemini_2_5(self, monkeypatch):
        captured_lengths: list[int] = []

        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                del model, config
                captured_lengths.append(len(contents))
                return type("Response", (), {"parts": [FakePart(inline_data=FakeInlineData())]})()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)
        monkeypatch.setattr(
            "agents.webtoon.clients.types.Part.from_bytes",
            lambda *, data, mime_type: {"data": data, "mime_type": mime_type},
        )

        client = GeminiImageClient(
            _make_dummy_settings(gemini_api_key="image-key", image_model="gemini-2.5-flash-image")
        )
        references = [(f"img-{idx}".encode(), "image/png") for idx in range(5)]

        image_bytes, mime_type, _ = client.generate_image("prompt", reference_images=references, max_retries=1)

        assert image_bytes == b"fake-image"
        assert mime_type == "image/png"
        assert captured_lengths == [3]

    def test_image_client_keeps_only_master_references_on_final_edit_retry(self, monkeypatch):
        captured_lengths: list[int] = []

        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeModels:
            def __init__(self):
                self.calls = 0

            def generate_content(self, *, model, contents, config):
                del model, config
                self.calls += 1
                captured_lengths.append(len(contents))
                if self.calls < 3:
                    return type("Response", (), {"parts": [FakePart(text="no image")]})()
                return type("Response", (), {"parts": [FakePart(inline_data=FakeInlineData())]})()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)
        monkeypatch.setattr(
            "agents.webtoon.clients.types.Part.from_bytes",
            lambda *, data, mime_type: {"data": data, "mime_type": mime_type},
        )
        monkeypatch.setattr("agents.webtoon.clients.time.sleep", lambda seconds: None)

        client = GeminiImageClient(_make_dummy_settings(gemini_api_key="image-key"))
        references = [(f"img-{idx}".encode(), "image/png") for idx in range(4)]

        image_bytes, mime_type, _ = client.generate_image(
            "prompt",
            reference_images=references,
            edit_image_bytes=b"base-image",
            max_retries=3,
        )

        assert image_bytes == b"fake-image"
        assert mime_type == "image/png"
        assert captured_lengths == [6, 5, 4]

    def test_image_client_reads_image_from_later_candidate(self, monkeypatch):
        class FakeInlineData:
            mime_type = "image/png"
            data = b"fake-image"

        class FakePart:
            def __init__(self, text=None, inline_data=None):
                self.text = text
                self.inline_data = inline_data

        class FakeContent:
            def __init__(self, parts):
                self.parts = parts

        class FakeCandidate:
            def __init__(self, parts):
                self.content = FakeContent(parts)
                self.finish_reason = "STOP"
                self.finish_message = None

        class FakeResponse:
            def __init__(self):
                self.parts = [FakePart(text="first candidate text only")]
                self.candidates = [
                    FakeCandidate([FakePart(text="first candidate text only")]),
                    FakeCandidate([FakePart(inline_data=FakeInlineData())]),
                ]

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                del model, contents, config
                return FakeResponse()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)

        client = GeminiImageClient(_make_dummy_settings(gemini_api_key="image-key"))

        image_bytes, mime_type, text = client.generate_image("prompt", max_retries=1)

        assert image_bytes == b"fake-image"
        assert mime_type == "image/png"
        assert "first candidate text only" in text

    def test_ocr_extract_text_uses_extract_model(self, monkeypatch):
        captured_models: list[str] = []

        class FakeResponse:
            text = "OK"

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                del contents, config
                captured_models.append(model)
                return FakeResponse()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()
                self.caches = type("Caches", (), {})()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)

        client = GeminiOcrClient(
            _make_dummy_settings(
                ocr_model="gemini-2.5-flash",
                ocr_extract_model="gemini-2.5-flash-lite",
            )
        )

        assert client.extract_text(b"img", mime_type="image/png") == "OK"
        assert captured_models == ["gemini-2.5-flash-lite"]

    def test_ocr_review_checks_use_llm_model(self, monkeypatch):
        captured_models: list[str] = []

        class FakeResponse:
            text = json.dumps(
                {
                    "background_texts": ["Willkommen in Deutschand"],
                    "has_errors": False,
                    "corrections": [],
                },
                ensure_ascii=False,
            )

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                del contents, config
                captured_models.append(model)
                return FakeResponse()

        class FakeGenaiClient:
            def __init__(self, *, api_key, http_options):
                del api_key, http_options
                self.models = FakeModels()
                self.caches = type("Caches", (), {})()

        monkeypatch.setattr("agents.webtoon.clients.genai.Client", FakeGenaiClient)

        client = GeminiOcrClient(
            _make_dummy_settings(
                llm_model="gemini-2.5-pro",
                ocr_model="gemini-2.5-flash",
                ocr_extract_model="gemini-2.5-flash-lite",
            )
        )

        client.check_background_text(b"img", "공항 입국장 전광판")

        assert captured_models == ["gemini-2.5-pro"]


class TestStoryReviewPrompts:
    def test_build_creative_brief_prompt_generalizes_thumbnail_and_background_extras(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())
        prompts: list[str] = []
        captured_kwargs: list[dict[str, object]] = []

        def fake_generate_text(prompt: str, **kwargs):
            prompts.append(prompt)
            captured_kwargs.append(kwargs)
            return json.dumps(
                {
                    "title": "제목",
                    "thumbnail_subtitle": "설명 부제목",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "입국부터 환승까지 이어지는 여정",
                    "image_prompt": "기본",
                    "thumbnail_scene_prompt": "대표 장면",
                    "caption": "캡션",
                    "hashtags": [],
                    "character_notes": "",
                    "panels": [{"panel_no": index} for index in range(1, 7)],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)
        monkeypatch.setattr(
            client,
            "_review_creative_brief",
            lambda topic, payload: {"has_issues": False, "issues": [], "rewrite_instruction": ""},
        )

        client.build_creative_brief("독일 첫 입국")

        assert prompts
        first_prompt = prompts[0]
        full_prompt = f"{captured_kwargs[0]['system_instruction']}\n{first_prompt}"
        assert captured_kwargs[0]["thinking_level"] == "high"
        assert captured_kwargs[0]["cache_key"] is None
        assert "생활형 에피소드 웹툰 기획자" in str(captured_kwargs[0]["system_instruction"])
        assert "독일생활 웹툰 기획자" not in full_prompt
        assert "출발 사건, 핵심 장소, 핵심 행동" in full_prompt
        assert "비주요 인간 배경 인물이나 대기 줄은 필요할 때만 허용" in full_prompt
        assert "같은 캐리어 배치나 같은 나란히 서기 구도는 피하세요" in full_prompt
        assert "thumbnail_subtitle은 썸네일 부제목" in full_prompt
        assert "사람이 올라서면 안 되는 표면" in full_prompt
        assert "같은 에피소드 범위를 약속해야 합니다" in full_prompt
        assert "여정형 이야기" in full_prompt
        assert 'episode_scope 필드에는 반드시 "single_location" 또는 "journey"' in full_prompt
        assert 'subtitle_scope 필드에도 반드시 "single_location" 또는 "journey"' in full_prompt
        assert "scope_summary 필드에는 title, thumbnail_subtitle, caption" in full_prompt
        assert "key_props 배열과 carryover_props 배열을 반드시 넣으세요" in full_prompt
        assert "특정 주제 예시를 하드코딩하지 말고" in full_prompt
        assert "공항 입국, 환승, 비행, 수하물 찾기, 플랫폼 이동" not in full_prompt

    def test_build_creative_brief_retries_with_compact_prompt_after_primary_failure(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())
        captured_kwargs: list[dict[str, object]] = []
        call_count = {"value": 0}

        def fake_generate_text(prompt: str, **kwargs):
            call_count["value"] += 1
            captured_kwargs.append(kwargs)
            if call_count["value"] == 1:
                raise RuntimeError("primary prompt timeout")
            return json.dumps(
                {
                    "title": "제목",
                    "thumbnail_subtitle": "설명 부제목",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "입국부터 환승까지 이어지는 여정",
                    "image_prompt": "기본",
                    "thumbnail_scene_prompt": "대표 장면",
                    "caption": "캡션",
                    "hashtags": [],
                    "character_notes": "",
                    "panels": [{"panel_no": index} for index in range(1, 7)],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)
        monkeypatch.setattr(
            client,
            "_review_creative_brief",
            lambda topic, payload: {"has_issues": False, "issues": [], "rewrite_instruction": ""},
        )

        payload = client.build_creative_brief("독일 첫 입국")

        assert payload["title"] == "제목"
        assert len(captured_kwargs) == 2
        assert captured_kwargs[0]["thinking_level"] == "high"
        assert captured_kwargs[0]["cache_key"] is None
        assert captured_kwargs[1]["thinking_level"] == "medium"
        assert captured_kwargs[1]["cache_key"] is None
        assert "6컷 웹툰용 기획안" in str(captured_kwargs[1]["system_instruction"])

    def test_build_creative_brief_uses_compact_prompt_for_gemini_2_5_models(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings(llm_model="gemini-2.5-flash"))
        captured_kwargs: list[dict[str, object]] = []

        def fake_generate_text(prompt: str, **kwargs):
            captured_kwargs.append(kwargs)
            return json.dumps(
                {
                    "title": "제목",
                    "thumbnail_subtitle": "설명 부제목",
                    "episode_scope": "journey",
                    "subtitle_scope": "journey",
                    "scope_summary": "입국부터 환승까지 이어지는 여정",
                    "image_prompt": "기본",
                    "thumbnail_scene_prompt": "대표 장면",
                    "caption": "캡션",
                    "hashtags": [],
                    "character_notes": "",
                    "panels": [{"panel_no": index} for index in range(1, 7)],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)
        monkeypatch.setattr(
            client,
            "_review_creative_brief",
            lambda topic, payload: {"has_issues": False, "issues": [], "rewrite_instruction": ""},
        )

        client.build_creative_brief("독일 첫 입국")

        assert captured_kwargs[0]["thinking_level"] == "medium"
        assert captured_kwargs[0]["cache_key"] is None
        assert "6컷 웹툰용 기획안" in str(captured_kwargs[0]["system_instruction"])

    def test_normalize_brief_payload_derives_short_title_and_subtitle(self):
        client = GeminiTextClient(_make_dummy_settings())

        payload = client._normalize_brief_payload(
            "독일 첫 입국",
            {
                "title": "독일 도착! 첫 관문은 기차표 끊기?",
                "episode_scope": "travel",
                "subtitle_scope": "single_location",
                "image_prompt": "기본",
                "thumbnail_scene_prompt": "대표 장면",
                "caption": "캡션",
                "hashtags": [],
                "character_notes": "",
                "panels": [
                    {
                        "panel_no": 1,
                        "location": "공항",
                        "key_props": ["여권", "입국 서류"],
                        "carryover_props": [],
                        "speaker_dialogues": [],
                    },
                    {
                        "panel_no": 2,
                        "location": "기차역",
                        "key_props": ["기차표"],
                        "carryover_props": ["기차표"],
                        "speaker_dialogues": [],
                    },
                    {"panel_no": 3, "location": "플랫폼", "speaker_dialogues": []},
                    {"panel_no": 4, "location": "기차 안", "speaker_dialogues": []},
                    {"panel_no": 5, "location": "기차 안", "speaker_dialogues": []},
                    {"panel_no": 6, "location": "기숙사 앞", "speaker_dialogues": []},
                ],
            },
        )

        assert payload["title"] == "독일 도착!"
        assert payload["thumbnail_subtitle"] == "첫 관문은 기차표 끊기?"
        assert payload["episode_scope"] == "journey"
        assert payload["subtitle_scope"] == "single_location"
        assert payload["scope_summary"]
        assert payload["panels"][0]["key_props"] == ["여권", "입국 서류"]
        assert payload["panels"][1]["carryover_props"] == ["기차표"]
        assert payload["character_notes"]
        assert payload["image_prompt"] == (
            "Korean digital webtoon style, bold clean outlines, vibrant colors, "
            "expressive exaggerated facial expressions, dynamic poses, "
            "anthropomorphic cats walking upright on two legs using front paws as hands, "
            "manga-style emotion effects, detailed background"
        )

    def test_normalize_brief_payload_backfills_default_dialogues_when_missing(self):
        client = GeminiTextClient(_make_dummy_settings())

        payload = client._normalize_brief_payload(
            "독일 첫 입국",
            {
                "title": "독일 도착",
                "thumbnail_subtitle": "긴장되는 첫 심사",
                "image_prompt": "기본",
                "thumbnail_scene_prompt": "대표 장면",
                "caption": "캡션",
                "hashtags": [],
                "character_notes": "콜라색 몸",
                "panels": [
                    {
                        "panel_no": index,
                        "story_role": "기" if index == 1 else "전",
                        "location": f"장소 {index}",
                        "speaker_dialogues": [],
                    }
                    for index in range(1, 7)
                ],
            },
        )

        assert "검은 단색 털" in payload["character_notes"]
        assert payload["panels"][0]["speaker_dialogues"][0]["dialogue_lines"]
        assert payload["panels"][0]["speaker_dialogues"][1]["dialogue_lines"]
        assert all(len(line) <= 38 for line in payload["panels"][0]["dialogue_lines"])
        assert "black-furred character with yellow eyes" in payload["panels"][0]["scene_prompt"]
        assert "cola bottle" not in payload["panels"][0]["scene_prompt"].lower()

    def test_normalize_brief_payload_replaces_overlong_dialogues_with_defaults(self):
        client = GeminiTextClient(_make_dummy_settings())

        payload = client._normalize_brief_payload(
            "독일 첫 입국",
            {
                "title": "독일 도착",
                "thumbnail_subtitle": "긴장되는 첫 심사",
                "thumbnail_scene_prompt": "대표 장면",
                "caption": "캡션",
                "hashtags": [],
                "panels": [
                    {
                        "panel_no": index,
                        "story_role": "결" if index == 6 else "전",
                        "location": f"장소 {index}",
                        "speaker_dialogues": [
                            {"speaker": "kolla", "dialogue_lines": ["이건 정말 말도 안 되게 긴 대사라서 기준을 넘어가도록 일부러 길게 만든 문장이다"]},
                            {"speaker": "zero", "dialogue_lines": ["나도 엄청 길게 말해서 제한을 넘겨 보자고 일부러 늘인 대사야"]},
                        ],
                    }
                    for index in range(1, 7)
                ],
            },
        )

        assert all(len(line) <= 38 for line in payload["panels"][0]["dialogue_lines"])
        assert payload["panels"][0]["speaker_dialogues"][0]["dialogue_lines"] != [
            "이건 정말 말도 안 되게 긴 대사라서 기준을 넘어가도록 일부러 길게 만든 문장이다"
        ]

    def test_normalize_episode_scope_prefers_single_location_when_only_sublocations_change(self):
        client = GeminiTextClient(_make_dummy_settings())

        payload = client._normalize_brief_payload(
            "독일 첫 입국",
            {
                "title": "독일 입국",
                "thumbnail_subtitle": "공항 안에서 벌어진 소동",
                "episode_scope": "journey",
                "subtitle_scope": "journey",
                "thumbnail_scene_prompt": "공항 대표 장면",
                "caption": "캡션",
                "hashtags": [],
                "character_notes": "",
                "panels": [
                    {"panel_no": 1, "location": "독일 공항, 도착 홀", "speaker_dialogues": []},
                    {"panel_no": 2, "location": "독일 공항, 입국 심사 줄", "speaker_dialogues": []},
                    {"panel_no": 3, "location": "독일 공항, 심사 데스크", "speaker_dialogues": []},
                    {"panel_no": 4, "location": "독일 공항, 통과 복도", "speaker_dialogues": []},
                    {"panel_no": 5, "location": "독일 공항, 수하물 벨트", "speaker_dialogues": []},
                    {"panel_no": 6, "location": "독일 공항, 출구 앞", "speaker_dialogues": []},
                ],
            },
        )

        assert payload["episode_scope"] == "single_location"

    def test_review_prompt_demands_thumbnail_distinct_from_panels(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps({"has_issues": False, "issues": [], "rewrite_instruction": ""}, ensure_ascii=False)

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client._review_creative_brief(
            "독일 첫 입국",
            {
                "thumbnail_scene_prompt": "공항 입국 심사장 앞 긴장 장면",
                "panels": [
                    {"panel_no": 1, "location": "공항 입국 심사장", "scene_prompt": "panel 1"},
                    {"panel_no": 2, "location": "수하물 벨트", "scene_prompt": "panel 2"},
                ],
            },
        )

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "패널 재사용이 아니라 별도의 대표 장면" in full_prompt
        assert "패널 1 또는 다른 패널의 location/scene_prompt와 사실상 같은 장면이면 실패" in full_prompt
        assert "주제를 대표하는 포괄적 배경" in full_prompt
        assert "배경 구조, 서브로케이션, 대표 간판, 대표 소품 배치가 본문 1~6컷 어디에서라도 다시 등장하면 실패" in full_prompt
        assert "캐릭터 포즈, 소품 배치, 시선 방향이 비슷하면 실패" in full_prompt
        assert "thumbnail_subtitle은 title을 반복하지 말고" in full_prompt
        assert 'episode_scope는 "single_location" 또는 "journey"' in full_prompt
        assert 'subtitle_scope도 "single_location" 또는 "journey"' in full_prompt
        assert "scope_summary는 title, thumbnail_subtitle, caption, panels가 공통으로 약속" in full_prompt
        assert "특정 장소/현장/상황을 약속하면 패널 전개와 결말도 그 범위 안에 머물러야" in full_prompt
        assert "여러 장소를 이동하는 여정형 이야기라면 thumbnail_subtitle도 그 전체를 포괄" in full_prompt
        assert "각 panel에는 key_props와 carryover_props가 있어야 합니다" in full_prompt
        assert "carryover_props에 적힌 이름은 이전 컷과 같은 표기를 유지" in full_prompt
        assert "건네기/올려놓기/집어들기 같은 전이 행동" in full_prompt

    def test_rewrite_prompt_demands_thumbnail_teaser_shot(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps(
                {"title": "제목", "thumbnail_subtitle": "부제목", "subtitle_scope": "single_location", "panels": []},
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client._rewrite_creative_brief(
            "독일 첫 입국",
            {"title": "원본", "panels": []},
            {"issues": ["썸네일과 패널 1이 동일함"], "rewrite_instruction": "다시 작성"},
        )

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "썸네일은 본문 어느 컷의 복사본이 아니라 별도의 티저 장면" in full_prompt
        assert "특히 패널 1과 같은 장면이면 안 됩니다" in full_prompt
        assert "포괄적 대표 배경" in full_prompt
        assert "콜라는 썸네일부터 패널 6까지 모든 이미지에서 제로보다 항상 더 크게 읽혀야" in full_prompt
        assert "thumbnail_subtitle은 설명형 보조 문구" in full_prompt
        assert 'subtitle_scope도 "single_location" 또는 "journey"' in full_prompt
        assert "thumbnail_subtitle이 약속하는 에피소드 범위와 panels의 location 전개를 반드시 일치" in full_prompt
        assert "scope_summary는 title, thumbnail_subtitle, caption, panels가 공통으로 약속" in full_prompt
        assert "다음 컷에서도 같은 물건으로 유지하세요" in full_prompt
        assert "key_props, carryover_props" in full_prompt
        assert "건네기/올려놓기/집어들기 같은 전이 행동" in full_prompt
        assert "특정 주제 예시를 박아넣지 말고" in full_prompt

    def test_review_flags_abrupt_prop_state_change_without_transfer(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())

        def fake_generate_text(prompt: str, **kwargs):
            return json.dumps({"has_issues": False, "issues": [], "rewrite_instruction": ""}, ensure_ascii=False)

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        review = client._review_creative_brief(
            "독일 첫 입국",
            {
                "thumbnail_scene_prompt": "공항 입구 전경",
                "panels": [
                    {
                        "panel_no": 1,
                        "location": "입국 심사 줄",
                        "scene_prompt": "콜라가 은색 캐리어를 손에 끌고 서 있다",
                        "key_props": ["은색 캐리어"],
                        "carryover_props": [],
                        "speaker_dialogues": [],
                    },
                    {
                        "panel_no": 2,
                        "location": "보안 검색대",
                        "scene_prompt": "은색 캐리어가 벨트 위에 지나가고 둘이 바라본다",
                        "key_props": ["은색 캐리어"],
                        "carryover_props": ["은색 캐리어"],
                        "speaker_dialogues": [],
                    },
                ],
            },
        )

        assert review["has_issues"] is True
        assert any("은색 캐리어" in issue and "전이 행동" in issue for issue in review["issues"])

    def test_review_creative_brief_falls_back_to_deterministic_checks_when_json_is_invalid(self, monkeypatch):
        client = GeminiTextClient(_make_dummy_settings())

        def fake_generate_text(prompt: str, **kwargs):
            return '{"has_issues": true, "issues": ["broken"]'

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        review = client._review_creative_brief(
            "독일 첫 입국",
            {
                "thumbnail_scene_prompt": "공항 입구 전경",
                "panels": [
                    {
                        "panel_no": 1,
                        "location": "입국 심사 줄",
                        "scene_prompt": "콜라가 은색 캐리어를 손에 끌고 서 있다",
                        "key_props": ["은색 캐리어"],
                        "carryover_props": [],
                        "speaker_dialogues": [],
                    },
                    {
                        "panel_no": 2,
                        "location": "보안 검색대",
                        "scene_prompt": "은색 캐리어가 벨트 위에 지나가고 둘이 바라본다",
                        "key_props": ["은색 캐리어"],
                        "carryover_props": ["은색 캐리어"],
                        "speaker_dialogues": [],
                    },
                ],
            },
        )

        assert review["has_issues"] is True
        assert any("은색 캐리어" in issue for issue in review["issues"])


class TestCharacterCompositionGate:
    def test_prompt_mentions_silhouette_and_quadruped_failures(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps(
                {
                    "has_issues": False,
                    "issues": [],
                    "edit_instruction": "",
                    "kolla_count": 1,
                    "zero_count": 1,
                    "extra_character_count": 0,
                    "silhouette_extra_count": 0,
                    "duplicate_scene_detected": False,
                    "bipedal_ok": True,
                    "scene_match_ok": True,
                    "kolla_larger_than_zero_ok": True,
                    "duplicate_character_detected": False,
                    "upper_margin_character_detected": False,
                    "quadruped_detected": False,
                    "quadruped_subjects": [],
                    "upright_pose_ok": True,
                    "forepaws_used_as_hands_ok": True,
                    "unsafe_surface_pose_detected": False,
                    "reference_like_copy_detected": False,
                    "cutin_or_sticker_detected": False,
                    "partial_body_duplicate_detected": False,
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client.check_character_composition(b"fake", "공항 입국 심사장")

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "실루엣, 뒷모습, 그림자처럼 보이는 추가 캐릭터" in full_prompt
        assert '"quadruped_detected"(bool)' in full_prompt
        assert '"upper_margin_character_detected"(bool)' in full_prompt
        assert '"kolla_larger_than_zero_ok"(bool)' in full_prompt
        assert '"kolla_size_gap_band_ok"(bool)' in full_prompt
        assert "비주요 인간 배경 인물은 장면 설명에 필요하면 허용" in full_prompt
        assert "캐리어, 가방, 표, 지도, 여권, 휴대폰 등 핵심 소품" in full_prompt
        assert "이전 컷 연속 소품 정보가 포함되어 있다면" in full_prompt
        assert "콜라가 제로와 비슷한 크기이거나 더 작아 보이면 오류" in full_prompt
        assert "썸네일부터 패널 6까지 모든 컷에 동일하게 적용" in full_prompt
        assert "목표는 약 12%이며 허용 범위는 8~15%" in full_prompt
        assert "사족보행, 앞발 체중 지지" in full_prompt
        assert "사람이 올라서면 안 되는 표면" in full_prompt
        assert '"unsafe_surface_pose_detected"(bool)' in full_prompt

    def test_detects_new_issue_flags_in_computed_result(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings())

        def fake_generate_text(prompt: str, **kwargs):
            return json.dumps(
                {
                    "has_issues": False,
                    "issues": [],
                    "edit_instruction": "",
                    "kolla_count": 1,
                    "zero_count": 1,
                    "extra_character_count": 0,
                    "silhouette_extra_count": 1,
                    "duplicate_scene_detected": False,
                    "bipedal_ok": True,
                    "scene_match_ok": True,
                    "kolla_larger_than_zero_ok": False,
                    "kolla_size_gap_band_ok": False,
                    "estimated_size_gap_percent": 33,
                    "duplicate_character_detected": False,
                    "upper_margin_character_detected": True,
                    "quadruped_detected": True,
                    "quadruped_subjects": ["zero"],
                    "upright_pose_ok": False,
                    "forepaws_used_as_hands_ok": False,
                    "unsafe_surface_pose_detected": True,
                    "reference_like_copy_detected": True,
                    "cutin_or_sticker_detected": False,
                    "partial_body_duplicate_detected": True,
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        result = client.check_character_composition(b"fake", "공항 입국 심사장")

        assert result["has_issues"] is True
        assert result["silhouette_extra_count"] == 1
        assert result["upper_margin_character_detected"] is True
        assert result["quadruped_detected"] is True
        assert result["quadruped_subjects"] == ["zero"]
        assert result["kolla_larger_than_zero_ok"] is False
        assert result["kolla_size_gap_band_ok"] is False
        assert result["estimated_size_gap_percent"] == 33
        assert result["unsafe_surface_pose_detected"] is True
        assert result["reference_like_copy_detected"] is True
        assert "two legs only" in result["edit_instruction"]


class TestBackgroundTextGate:
    def test_prompt_demands_short_placeholder_fixes(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps(
                {
                    "background_texts": ["Flight Indormasoon Onurdtseave"],
                    "has_errors": False,
                    "corrections": [],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client.check_background_text(b"fake", "공항 입국장 전광판 앞 장면")

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "correct 필드는 긴 설명문이 아니라" in full_prompt
        assert "래스터화된 글리프" in full_prompt
        assert "'INFO', 'FLIGHTS', 'EXIT', 'TICKET'" in full_prompt
        assert "'PASSPORT' 또는 'DOCUMENT'" in full_prompt
        assert "'STAMP' 같은 짧은 라벨" in full_prompt
        assert "icons only, blank bars only, one short label" in full_prompt

    def test_normalizes_long_corrections_to_short_labels(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings())

        def fake_generate_text(prompt: str, **kwargs):
            return json.dumps(
                {
                    "background_texts": ["FEUOPP HNS", "GOODBY REGULATION TEXT"],
                    "has_errors": True,
                    "corrections": [
                        {
                            "found": "FEUOPP HNS",
                            "correct": "This digital display screen should show legible flight information. It could be simplified to 'Departure Information' as a header, followed by a few lines of readable flight details.",
                            "reason": "The digital display is gibberish.",
                        },
                        {
                            "found": "GOODBY REGULATION TEXT",
                            "correct": "[Official Stamp - placeholder text, or simplified to 'STAMP']",
                            "reason": "Stamp area unreadable.",
                        },
                    ],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        result = client.check_background_text(b"fake", "입국 심사 도장과 전광판")

        assert result["corrections"][0]["correct"] == "INFO"
        assert result["corrections"][1]["correct"] == "STAMP"


class TestThumbnailPanelDistinctionGate:
    def test_prompt_mentions_thumbnail_not_copying_panels(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps(
                {
                    "has_issues": False,
                    "issues": [],
                    "edit_instruction": "",
                    "duplicated_panel_numbers": [],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client.check_thumbnail_panel_distinction(
            b"thumb",
            [(b"panel1", "image/png")],
            thumbnail_scene_prompt="공항 입국 심사대 앞 긴장 장면",
            panel_summaries=[{"panel_no": 1, "location": "공항 입국 심사장", "scene_prompt": "입국 심사 줄"}],
        )

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "썸네일이 본문 어느 패널의 배경, 장소, 사건 순간, 카메라 구도를 사실상 재사용" in full_prompt
        assert "본문 1~6 전체와 비교" in full_prompt
        assert "주제를 설명하는 포괄적 대표 배경" in full_prompt
        assert "본문 1~6컷 중 하나라도 썸네일과 같은 배경 구조" in full_prompt
        assert '"duplicated_panel_numbers"(array)' in full_prompt
        assert captured["model_name"] == "gemini-review-model"

    def test_detects_duplicated_panel_numbers_as_issue(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings())

        def fake_generate_text(prompt: str, **kwargs):
            return json.dumps(
                {
                    "has_issues": False,
                    "issues": ["패널 1과 같은 장면"],
                    "edit_instruction": "",
                    "duplicated_panel_numbers": [1],
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        result = client.check_thumbnail_panel_distinction(
            b"thumb",
            [(b"panel1", "image/png")],
            thumbnail_scene_prompt="공항 입국 심사대 앞 긴장 장면",
            panel_summaries=[{"panel_no": 1, "location": "공항 입국 심사장", "scene_prompt": "입국 심사 줄"}],
        )

        assert result["has_issues"] is True
        assert result["duplicated_panel_numbers"] == [1]
        assert "separate teaser shot" in result["edit_instruction"]


class TestFinalWebtoonPackageReview:
    def test_prompt_is_topic_generic_and_allows_minor_background_humans(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps(
                {
                    "hard_blockers": [],
                    "soft_scores": {
                        "topic_alignment": 0.9,
                        "story_flow": 0.85,
                        "background_progression": 0.88,
                        "thumbnail_distinction": 0.92,
                        "bubble_placement": 0.9,
                        "ending_resolution": 0.87,
                    },
                    "notes": [],
                    "summary": "ok",
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        result = client.review_final_webtoon_package(
            topic="독일 첫 입국",
            title="독일 첫 입국",
            thumbnail_subtitle="입국 심사 현장 대공개",
            caption="입국부터 첫 이동까지 우당탕 적응기",
            episode_scope="journey",
            subtitle_scope="single_location",
            scope_summary="입국 심사부터 첫 이동까지 이어지는 여정형 에피소드",
            thumbnail_scene_prompt="입국 심사 직전 긴장 장면",
            panel_summaries=[
                {
                    "panel_no": 1,
                    "story_role": "기",
                    "location": "입국 심사장",
                    "scene_prompt": "대기 줄",
                    "key_props": ["여권", "서류 폴더"],
                    "carryover_props": [],
                },
                {
                    "panel_no": 6,
                    "story_role": "결",
                    "location": "플랫폼",
                    "scene_prompt": "안도 장면",
                    "key_props": ["캐리어"],
                    "carryover_props": ["캐리어"],
                },
            ],
            slide_images=[(b"thumb", "image/png"), (b"panel", "image/png")],
            stage_gate_findings=["패널 6 캐릭터 구성 품질 게이트 실패: 장면 설명 불일치"],
        )

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "어떤 주제든 같은 기준으로만 판단" in full_prompt
        assert "비주요 인간 배경 인물, 승객, 직원, 줄은 장면상 필요하면 허용" in full_prompt
        assert "본문 1~6 전체와 비교" in full_prompt
        assert "상대 크기 비율은 썸네일부터 패널 6까지 거의 같은 좁은 범위" in full_prompt
        assert "썸네일 부제목: 입국 심사 현장 대공개" in full_prompt
        assert "인스타 캡션: 입국부터 첫 이동까지 우당탕 적응기" in full_prompt
        assert "에피소드 범위 타입: journey" in full_prompt
        assert "부제목 범위 타입: single_location" in full_prompt
        assert "상위 stage gate 점검 메모" in full_prompt
        assert "carryover_props" in full_prompt
        assert "상위 stage gate에서 문제를 지적했는데" in full_prompt
        assert '"scope_alignment"' in full_prompt
        assert '"caption_alignment"' in full_prompt
        assert captured["thinking_level"] == "high"
        assert captured["cache_key"] == "ocr-review-final-package-v2"
        assert captured["model_name"] == "gemini-review-model"
        assert result["soft_scores"]["story_flow"] == 0.85
        assert result["hard_blockers"] == []


class TestCharacterReferenceConsistencyPrompt:
    def test_prompt_mentions_size_and_bipedal_identity_failures(self, monkeypatch):
        client = GeminiOcrClient(_make_dummy_settings(llm_model="gemini-review-model", ocr_model="gemini-ocr-model"))
        captured: dict[str, object] = {}

        def fake_generate_text(prompt: str, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return json.dumps({"has_issues": False, "issues": [], "edit_instruction": ""}, ensure_ascii=False)

        monkeypatch.setattr(client, "_generate_text", fake_generate_text)

        client.check_character_reference_consistency(
            b"fake",
            "공항 입국 심사장",
            [(b"ref1", "image/png"), (b"ref2", "image/png")],
        )

        full_prompt = f"{captured['system_instruction']}\n{captured['prompt']}"
        assert "콜라가 제로보다 작거나 비슷하게 읽히는 경우" in full_prompt
        assert "썸네일부터 패널 6까지 어느 컷이든" in full_prompt
        assert "상대 크기 비율이 갑자기 무너져" in full_prompt
        assert "네 발 보행" in full_prompt
        assert "벨트/레일/기계 상판 위 탑승" in full_prompt
        assert captured["model_name"] == "gemini-review-model"


def _make_dummy_settings(**overrides) -> WebtoonSettings:
    defaults = {
        "google_oauth_client_secret_file": Path("/tmp/fake-secret.json"),
        "google_oauth_token_file": Path("/tmp/fake-token.json"),
        "google_drive_root_folder_id": "fake-folder-id",
        "gemini_api_key": "fake-image-key",
        "approval_default_user": "tester",
    }
    defaults.update(overrides)
    return WebtoonSettings(**defaults)
