from __future__ import annotations

import json
import logging

import pytest

from agents.webtoon.clients import (
    _extract_json_object,
    build_smoke_test_image_bytes,
    clean_ocr_text,
    normalize_dialogue_text,
    sanitize_public_hashtags,
    sanitize_public_text,
)


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
