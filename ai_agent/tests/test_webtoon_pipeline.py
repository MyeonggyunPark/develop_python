from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from agents.webtoon.pipeline import (
    build_bubble_only_ocr_image,
    build_panel_generation_prompt,
    build_run_identity,
    build_thumbnail_generation_prompt,
    build_versioned_path,
    create_artifact_paths,
    list_character_reference_files,
)
from agents.webtoon.config import WebtoonSettings


class TestBuildRunIdentity:
    def test_returns_week_key_and_run_id(self):
        week_key, run_id = build_run_identity()
        assert week_key.startswith("20")
        assert "-W" in week_key
        assert run_id.startswith("run-")
        assert len(run_id) == 12  # "run-" + 8 hex chars

    def test_deterministic_week_key_for_same_date(self):
        dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        wk1, _ = build_run_identity(dt)
        wk2, _ = build_run_identity(dt)
        assert wk1 == wk2

    def test_run_id_is_unique(self):
        dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        _, rid1 = build_run_identity(dt)
        _, rid2 = build_run_identity(dt)
        assert rid1 != rid2


class TestBuildVersionedPath:
    def test_format(self):
        path = build_versioned_path(Path("/tmp/run"), "panel_01_final", 3, "png")
        assert path == Path("/tmp/run/panel_01_final_v3.png")


class TestCreateArtifactPaths:
    def test_paths_are_in_temp_dir(self):
        artifacts = create_artifact_paths("run-abc12345", "2026-W12")
        assert artifacts.run_dir.exists()
        assert "webtoon-run-abc12345" in artifacts.run_dir.name
        assert artifacts.metadata_path.name == "run_metadata.json"
        import shutil
        shutil.rmtree(artifacts.run_dir, ignore_errors=True)


class TestBuildThumbnailGenerationPrompt:
    def test_contains_required_elements(self):
        prompt = build_thumbnail_generation_prompt(
            "기본 프롬프트",
            title="테스트 제목",
            topic="테스트 주제",
            character_notes="검은 고양이",
        )
        assert "기본 프롬프트" in prompt
        assert "테스트 주제" in prompt
        assert "검은 고양이" in prompt
        assert "표지" in prompt or "커버" in prompt
        assert "10~15%" in prompt
        assert "상단 하늘" in prompt
        assert "마스터 레퍼런스" in prompt
        assert "멀티패널" in prompt

    def test_empty_character_notes(self):
        prompt = build_thumbnail_generation_prompt(
            "기본 프롬프트",
            title="제목",
            topic="주제",
            character_notes="",
        )
        assert "캐릭터 일관성 메모" not in prompt


class TestBuildPanelGenerationPrompt:
    def test_contains_scene_prompt(self):
        panel = {"panel_no": 2, "scene_prompt": "마트에서 장보기"}
        prompt = build_panel_generation_prompt("기본", panel, "", ["안녕하세요"])
        assert "마트에서 장보기" in prompt
        assert "2컷" in prompt
        assert "안녕하세요" in prompt
        assert "10~15%" in prompt
        assert "같은 바닥 평면" in prompt
        assert "마스터 레퍼런스" in prompt
        assert "단일 패널 한 컷" in prompt

    def test_empty_dialogue(self):
        panel = {"panel_no": 1, "scene_prompt": "장면"}
        prompt = build_panel_generation_prompt("기본", panel, "", [])
        assert "텍스트 없음" in prompt


class TestListCharacterReferenceFiles:
    def test_empty_dir(self):
        settings = _make_dummy_settings()
        result = list_character_reference_files(settings)
        assert result == []

    def test_finds_preferred_files(self, tmp_path):
        char_dir = tmp_path / "characters"
        char_dir.mkdir()
        (char_dir / "black_cat.png").write_bytes(b"fake")
        (char_dir / "gray_cat.png").write_bytes(b"fake")
        (char_dir / "other.jpg").write_bytes(b"fake")

        settings = _make_dummy_settings(character_assets_dir=char_dir)
        result = list_character_reference_files(settings)
        names = [p.name for p in result]
        assert names[0] == "black_cat.png"
        assert names[1] == "gray_cat.png"
        assert "other.jpg" in names


class TestBuildBubbleOnlyOcrImage:
    def _make_test_image_bytes(self, size=(800, 600)):
        img = Image.new("RGB", size, color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_no_bubbles_returns_original(self):
        image_bytes = self._make_test_image_bytes()
        result = build_bubble_only_ocr_image(image_bytes, [])
        assert result == image_bytes

    def test_with_bubble_boxes(self):
        image_bytes = self._make_test_image_bytes()
        layout = [{"bubble_box": [100, 100, 300, 200]}]
        result = build_bubble_only_ocr_image(image_bytes, layout)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert result != image_bytes

    def test_multiple_bubbles(self):
        image_bytes = self._make_test_image_bytes()
        layout = [
            {"bubble_box": [10, 10, 200, 100]},
            {"bubble_box": [10, 200, 200, 300]},
        ]
        result = build_bubble_only_ocr_image(image_bytes, layout)
        with Image.open(io.BytesIO(result)) as img:
            assert img.height > 100  # combined height of two crops


def _make_dummy_settings(**overrides) -> WebtoonSettings:
    defaults = {
        "llm_provider_api_key": "fake-key",
        "google_oauth_client_secret_file": Path("/tmp/fake-secret.json"),
        "google_oauth_token_file": Path("/tmp/fake-token.json"),
        "google_drive_root_folder_id": "fake-folder-id",
        "google_sheets_spreadsheet_id": "fake-sheet-id",
        "image_api_key": "fake-image-key",
        "ocr_api_key": "fake-ocr-key",
        "instagram_access_token": "fake-token",
        "instagram_business_account_id": "fake-account-id",
        "approval_default_user": "tester",
        "character_assets_dir": Path("/tmp/nonexistent-chars"),
    }
    defaults.update(overrides)
    return WebtoonSettings(**defaults)
