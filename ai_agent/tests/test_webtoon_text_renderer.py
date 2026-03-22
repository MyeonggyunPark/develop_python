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
    render_thumbnail_card,
    render_text_boxes,
    review_bubble_layout,
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

    def test_breaks_single_long_token_to_fit_width(self):
        result = _wrap_text(self.draw, "SUPERCALIFRAGILISTICEXPIALIDOCIOUS", self.font, 30)
        assert len(result) >= 2
        for line in result:
            bbox = self.draw.textbbox((0, 0), line, font=self.font)
            assert bbox[2] - bbox[0] <= 30


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


class TestRenderTextBoxes:
    def _make_base_image(self, size=(768, 768)):
        img = Image.new("RGB", size, color=(245, 245, 245))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_uses_panel_number_for_vertical_bubble_placement(self):
        settings = _make_dummy_settings()
        odd_panel = {
            "panel_no": 1,
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["공항 도착!"]},
                {"speaker": "zero", "dialogue_lines": ["환승부터 하자"]},
            ],
            "dialogue_lines": ["공항 도착!", "환승부터 하자"],
        }
        even_panel = {
            "panel_no": 2,
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["표 좀 뽑아볼게"]},
                {"speaker": "zero", "dialogue_lines": ["버튼이 너무 많아"]},
            ],
            "dialogue_lines": ["표 좀 뽑아볼게", "버튼이 너무 많아"],
        }

        odd_result = render_text_boxes(self._make_base_image(), [odd_panel], settings)
        even_result = render_text_boxes(self._make_base_image(), [even_panel], settings)

        odd_first_box = odd_result["layout"][0]["bubble_boxes"][0]
        even_first_box = even_result["layout"][0]["bubble_boxes"][0]
        assert odd_first_box[1] != even_first_box[1]
        assert odd_first_box[1] > 400
        assert even_first_box[1] < 120

    def test_higher_render_version_reduces_bubble_overlap(self):
        settings = _make_dummy_settings()
        panel = {
            "panel_no": 1,
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["드디어 도착했다", "공항이 생각보다 더 복잡하다"]},
                {"speaker": "zero", "dialogue_lines": ["전광판부터 확인하고", "입국장으로 바로 이동하자"]},
            ],
            "dialogue_lines": [
                "드디어 도착했다",
                "공항이 생각보다 더 복잡하다",
                "전광판부터 확인하고",
                "입국장으로 바로 이동하자",
            ],
        }

        v1 = render_text_boxes(self._make_base_image(), [panel], settings, render_version=1)
        v3 = render_text_boxes(self._make_base_image(), [panel], settings, render_version=3)
        review_v1 = review_bubble_layout(v1["layout"])
        review_v3 = review_bubble_layout(v3["layout"])

        box_v1 = v1["layout"][0]["bubble_boxes"][0]
        box_v3 = v3["layout"][0]["bubble_boxes"][0]
        area_v1 = (box_v1[2] - box_v1[0]) * (box_v1[3] - box_v1[1])
        area_v3 = (box_v3[2] - box_v3[0]) * (box_v3[3] - box_v3[1])

        assert area_v3 < area_v1
        assert review_v3["panel_scores"][0]["max_overlap_ratio"] <= review_v1["panel_scores"][0]["max_overlap_ratio"]

    def test_long_dialogue_text_stays_inside_bubble_bounds(self):
        settings = _make_dummy_settings()
        panel = {
            "panel_no": 1,
            "speaker_dialogues": [
                {
                    "speaker": "kolla",
                    "dialogue_lines": [
                        "독일 입국은 처음이라서 표지판 하나하나를 다 읽어보게 된다",
                        "그래도 너무 긴장하지 말고 순서대로 가 보자",
                    ],
                },
                {
                    "speaker": "zero",
                    "dialogue_lines": [
                        "여권이랑 입국 서류부터 다시 한 번 확인하자",
                        "말풍선 밖으로 텍스트가 넘치면 안 된다",
                    ],
                },
            ],
            "dialogue_lines": [
                "독일 입국은 처음이라서 표지판 하나하나를 다 읽어보게 된다",
                "그래도 너무 긴장하지 말고 순서대로 가 보자",
                "여권이랑 입국 서류부터 다시 한 번 확인하자",
                "말풍선 밖으로 텍스트가 넘치면 안 된다",
            ],
        }

        result = render_text_boxes(self._make_base_image(), [panel], settings, render_version=3)
        layout = result["layout"][0]
        review = review_bubble_layout(result["layout"])

        assert review["has_issues"] is False
        assert all(layout["text_fit_flags"])
        for text_box in layout["text_boxes"]:
            assert any(
                text_box[0] >= bubble_box[0]
                and text_box[1] >= bubble_box[1]
                and text_box[2] <= bubble_box[2]
                and text_box[3] <= bubble_box[3]
                for bubble_box in layout["bubble_boxes"]
            )

    def test_text_boxes_stay_inside_bubbles_for_dense_dialogue(self):
        settings = _make_dummy_settings()
        panel = {
            "panel_no": 1,
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["이 줄은 꽤 길어서 말풍선 안에서 여러 줄로 안정적으로 접혀야 해."]},
                {"speaker": "zero", "dialogue_lines": ["여기도 마찬가지로 말풍선 밖으로 절대 튀어나가면 안 돼."]},
            ],
            "dialogue_lines": [
                "이 줄은 꽤 길어서 말풍선 안에서 여러 줄로 안정적으로 접혀야 해.",
                "여기도 마찬가지로 말풍선 밖으로 절대 튀어나가면 안 돼.",
            ],
        }

        result = render_text_boxes(self._make_base_image(), [panel], settings, render_version=3)

        assert result["layout"]
        layout = result["layout"][0]
        assert layout["text_boxes"]
        assert layout["text_fit_flags"] == [True, True]
        for text_box in layout["text_boxes"]:
            assert any(
                text_box[0] >= bubble_box[0]
                and text_box[1] >= bubble_box[1]
                and text_box[2] <= bubble_box[2]
                and text_box[3] <= bubble_box[3]
                for bubble_box in layout["bubble_boxes"]
            )

    def test_long_unspaced_tokens_stay_inside_bubbles(self):
        settings = _make_dummy_settings()
        panel = {
            "panel_no": 1,
            "speaker_dialogues": [
                {"speaker": "kolla", "dialogue_lines": ["SUPERCALIFRAGILISTICEXPIALIDOCIOUS", "독일입국심사대기줄이생각보다훨씬길다"]},
                {"speaker": "zero", "dialogue_lines": ["BAGGAGECLAIMGATEA12ONTIME", "여권이랑서류를다시한번확인해보자"]},
            ],
            "dialogue_lines": [
                "SUPERCALIFRAGILISTICEXPIALIDOCIOUS",
                "독일입국심사대기줄이생각보다훨씬길다",
                "BAGGAGECLAIMGATEA12ONTIME",
                "여권이랑서류를다시한번확인해보자",
            ],
        }

        result = render_text_boxes(self._make_base_image(), [panel], settings, render_version=2)

        assert result["layout"]
        layout = result["layout"][0]
        assert layout["text_fit_flags"] == [True, True]
        for text_box in layout["text_boxes"]:
            assert any(
                text_box[0] >= bubble_box[0]
                and text_box[1] >= bubble_box[1]
                and text_box[2] <= bubble_box[2]
                and text_box[3] <= bubble_box[3]
                for bubble_box in layout["bubble_boxes"]
            )


class TestRenderThumbnailCard:
    def _make_base_image(self, size=(768, 768)):
        img = Image.new("RGB", size, color=(80, 100, 120))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_prefers_explicit_subtitle_over_topic(self):
        settings = _make_dummy_settings()

        result = render_thumbnail_card(
            self._make_base_image(),
            title="독일 도착!",
            subtitle="첫 관문은 기차표 끊기?",
            topic="독일 첫 입국",
            settings=settings,
        )

        assert result["layout"]["title_lines"] == ["독일 도착!"]
        assert "첫 관문은 기차표 끊기?" in result["layout"]["subtitle_lines"]
        assert result["layout"]["subtitle"] == "첫 관문은 기차표 끊기?"
        assert result["layout"]["topic"] == "독일 첫 입국"


class TestReviewBubbleLayout:
    def test_returns_hard_issue_for_severe_overlap(self):
        review = review_bubble_layout(
            [
                {
                    "panel_no": 1,
                    "panel_box": [28, 24, 772, 576],
                    "bubble_boxes": [[110, 120, 360, 360]],
                }
            ]
        )

        assert review["has_issues"] is True
        assert review["panel_scores"][0]["max_overlap_ratio"] >= 0.18

    def test_scores_safe_layout_as_pass(self):
        review = review_bubble_layout(
            [
                {
                    "panel_no": 1,
                    "panel_box": [28, 24, 772, 576],
                    "bubble_boxes": [[360, 40, 430, 90]],
                }
            ]
        )

        assert review["has_issues"] is False
        assert review["soft_score"] > 0.8

    def test_flags_text_overflow_outside_bubble(self):
        review = review_bubble_layout(
            [
                {
                    "panel_no": 1,
                    "panel_box": [28, 24, 772, 576],
                    "bubble_boxes": [[360, 40, 430, 90]],
                    "text_boxes": [[350, 45, 440, 80]],
                    "text_fit_flags": [False],
                }
            ]
        )

        assert review["has_issues"] is True
        assert any("말풍선 텍스트가 버블" in issue for issue in review["issues"])


class TestRenderTextBoxesLayout:
    def _make_base_image(self, size=(928, 1120)):
        img = Image.new("RGB", size, color=(245, 240, 232))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_odd_panel_keeps_top_bubble_band_clear_of_character_safe_boxes(self):
        settings = _make_dummy_settings()
        result = render_text_boxes(
            self._make_base_image(),
            [
                {
                    "panel_no": 1,
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["드디어 도착했다!", "공기부터 다르네!"]},
                        {"speaker": "zero", "dialogue_lines": ["이제 시작이야.", "여권 잘 챙겼지?"]},
                    ],
                    "dialogue_lines": ["드디어 도착했다!", "공기부터 다르네!", "이제 시작이야.", "여권 잘 챙겼지?"],
                }
            ],
            settings,
            render_version=2,
        )

        review = review_bubble_layout(result["layout"])

        assert review["has_issues"] is False
        assert review["panel_scores"][0]["max_overlap_ratio"] < 0.18

    def test_even_panel_keeps_bottom_bubble_band_clear_of_character_safe_boxes(self):
        settings = _make_dummy_settings()
        result = render_text_boxes(
            self._make_base_image(),
            [
                {
                    "panel_no": 2,
                    "speaker_dialogues": [
                        {"speaker": "kolla", "dialogue_lines": ["줄이 왜 이렇게 길어?", "금방 끝나겠지?"]},
                        {"speaker": "zero", "dialogue_lines": ["질문에 대답 잘해야 해.", "긴장 늦추지 마."]},
                    ],
                    "dialogue_lines": ["줄이 왜 이렇게 길어?", "금방 끝나겠지?", "질문에 대답 잘해야 해.", "긴장 늦추지 마."],
                }
            ],
            settings,
            render_version=2,
        )

        review = review_bubble_layout(result["layout"])

        assert review["has_issues"] is False
        assert review["panel_scores"][0]["max_overlap_ratio"] < 0.18


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
