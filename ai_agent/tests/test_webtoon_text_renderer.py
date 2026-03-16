from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw, ImageFont

from agents.webtoon.text_renderer import (
    _compute_text_lines,
    _fit_and_crop,
    _load_font,
    _wrap_text,
    compose_four_panel_canvas,
    flatten_panel_dialogues,
    get_four_panel_boxes,
    get_single_panel_box,
    resolve_font_path,
    split_dialogue_groups,
)
from agents.webtoon.config import WebtoonSettings


class TestLoadFont:
    def test_none_path_returns_default_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agents.webtoon.text_renderer"):
            font = _load_font(None, 24)
        assert font is not None
        assert "사용 가능한 폰트가 없어" in caplog.text

    def test_invalid_path_returns_default_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agents.webtoon.text_renderer"):
            font = _load_font(Path("/nonexistent/font.ttf"), 24)
        assert font is not None
        assert "폰트 로딩 실패" in caplog.text


class TestFlattenPanelDialogues:
    def test_basic(self):
        panels = [
            {"dialogue_lines": ["안녕", "하세요"]},
            {"dialogue_lines": ["반갑습니다"]},
        ]
        assert flatten_panel_dialogues(panels) == ["안녕", "하세요", "반갑습니다"]

    def test_empty_panels(self):
        assert flatten_panel_dialogues([]) == []

    def test_skips_empty_lines(self):
        panels = [{"dialogue_lines": ["", "  ", "hello"]}]
        assert flatten_panel_dialogues(panels) == ["hello"]

    def test_missing_dialogue_lines_key(self):
        panels = [{"scene_prompt": "test"}]
        assert flatten_panel_dialogues(panels) == []


class TestSplitDialogueGroups:
    def test_one_line_returns_single_group(self):
        assert split_dialogue_groups(["안녕"]) == [["안녕"]]

    def test_two_lines_split_by_speaker(self):
        assert split_dialogue_groups(["먼저 말해", "이제 내 차례"]) == [["먼저 말해"], ["이제 내 차례"]]

    def test_four_lines_split_into_two_and_two(self):
        assert split_dialogue_groups(["1", "2", "3", "4"]) == [["1", "2"], ["3", "4"]]


class TestWrapText:
    def setup_method(self):
        img = Image.new("RGB", (500, 500))
        self.draw = ImageDraw.Draw(img)
        self.font = ImageFont.load_default()

    def test_short_text_single_line(self):
        result = _wrap_text(self.draw, "hello", self.font, 500)
        assert len(result) == 1
        assert result[0] == "hello"

    def test_wraps_long_text(self):
        long_text = " ".join(["word"] * 50)
        result = _wrap_text(self.draw, long_text, self.font, 100)
        assert len(result) > 1

    def test_empty_text(self):
        result = _wrap_text(self.draw, "", self.font, 100)
        assert result == [""]


class TestComputeTextLines:
    def setup_method(self):
        img = Image.new("RGB", (500, 500))
        self.draw = ImageDraw.Draw(img)
        self.font = ImageFont.load_default()

    def test_cjk_text_wraps_by_character(self):
        text = "한글텍스트입니다이것은매우긴텍스트"
        result = _compute_text_lines(self.draw, text, self.font, 50)
        assert len(result) >= 1

    def test_space_separated_uses_wrap_text(self):
        text = "hello world test"
        result = _compute_text_lines(self.draw, text, self.font, 500)
        assert result == ["hello world test"]


class TestFitAndCrop:
    def test_basic_fit(self):
        img = Image.new("RGB", (200, 100))
        result = _fit_and_crop(img, (100, 100))
        assert result.size == (100, 100)

    def test_upscale(self):
        img = Image.new("RGB", (50, 50))
        result = _fit_and_crop(img, (100, 100))
        assert result.size == (100, 100)


class TestGetFourPanelBoxes:
    def test_returns_four_boxes(self):
        boxes = get_four_panel_boxes(1600, 1600)
        assert len(boxes) == 4

    def test_boxes_dont_overlap(self):
        boxes = get_four_panel_boxes(1600, 1600)
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            assert x2 > x1
            assert y2 > y1
            for j, (ox1, oy1, ox2, oy2) in enumerate(boxes):
                if i == j:
                    continue
                # No overlap: one must be fully left, right, above, or below
                assert x1 >= ox2 or x2 <= ox1 or y1 >= oy2 or y2 <= oy1


class TestGetSinglePanelBox:
    def test_basic(self):
        box = get_single_panel_box(800, 600)
        x1, y1, x2, y2 = box
        assert x1 == 28
        assert y1 == 24
        assert x2 == 772
        assert y2 == 576


class TestComposeFourPanelCanvas:
    def _make_panel_bytes(self, size=(200, 200)):
        img = Image.new("RGB", size, color=(100, 100, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_requires_exactly_four(self):
        with pytest.raises(ValueError, match="exactly four"):
            compose_four_panel_canvas([self._make_panel_bytes()] * 3)

    def test_returns_valid_image(self):
        panels = [self._make_panel_bytes() for _ in range(4)]
        result = compose_four_panel_canvas(panels)
        assert "image_bytes" in result
        assert result["image_bytes"][:8] == b"\x89PNG\r\n\x1a\n"
        assert len(result["panel_boxes"]) == 4
        assert len(result["canvas_size"]) == 2
