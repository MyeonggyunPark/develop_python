from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agents.webtoon.config import (
    DEFAULT_GOOGLE_OAUTH_CLIENT_SECRET_FILE,
    DEFAULT_GOOGLE_OAUTH_TOKEN_FILE,
    WebtoonSettings,
)
from agents.webtoon.main import _required_settings_fields


def _clear_webtoon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env_names = [
        "GOOGLE_OAUTH_CLIENT_SECRET_FILE",
        "GOOGLE_OAUTH_TOKEN_FILE",
        "GOOGLE_DRIVE_ROOT_FOLDER_ID",
        "GEMINI_API_KEY",
        "APPROVAL_DEFAULT_USER",
        "LLM_MODEL",
        "LLM_THINKING_LEVEL",
        "OCR_MODEL",
        "OCR_EXTRACT_MODEL",
        "OCR_THINKING_LEVEL",
        "OCR_EXTRACT_THINKING_LEVEL",
        "IMAGE_MODEL",
        "WEBTOON_FONT_FILE",
        "MAX_CORRECTION_ATTEMPTS",
        "API_PARALLELISM",
        "ENABLE_CONTEXT_CACHING",
        "CONTEXT_CACHE_TTL",
    ]
    for name in env_names:
        monkeypatch.delenv(name, raising=False)


class TestRequiredSettingsFields:
    def test_google_auth_status_only_requires_no_sensitive_env(self):
        args = argparse.Namespace(command="google-auth", status_only=True)
        assert _required_settings_fields(args) == set()

    def test_smoke_drive_only_requires_drive_field(self):
        args = argparse.Namespace(command="smoke", services=["drive"])
        assert _required_settings_fields(args) == {"google_drive_root_folder_id"}

    def test_pipeline_requires_drive_and_core_fields(self):
        args = argparse.Namespace(command="pipeline")
        required = _required_settings_fields(args)
        assert "google_drive_root_folder_id" in required
        assert "gemini_api_key" in required
        assert "approval_default_user" in required


class TestWebtoonSettingsFromEnv:
    def test_optional_fields_can_be_empty_for_status_commands(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_webtoon_env(monkeypatch)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")

        settings = WebtoonSettings.from_env(env_file, required_fields=set())

        assert settings.google_oauth_client_secret_file == DEFAULT_GOOGLE_OAUTH_CLIENT_SECRET_FILE
        assert settings.google_oauth_token_file == DEFAULT_GOOGLE_OAUTH_TOKEN_FILE
        assert settings.google_drive_root_folder_id == ""

    def test_required_field_still_raises_clear_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_webtoon_env(monkeypatch)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="GOOGLE_DRIVE_ROOT_FOLDER_ID"):
            WebtoonSettings.from_env(env_file, required_fields={"google_drive_root_folder_id"})

    def test_image_key_is_used_for_generation_clients(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_webtoon_env(monkeypatch)
        env_file = tmp_path / "llm.env"
        env_file.write_text("GEMINI_API_KEY=image-key\n", encoding="utf-8")

        settings = WebtoonSettings.from_env(env_file, required_fields={"gemini_api_key"})

        assert settings.gemini_api_key == "image-key"

    def test_image_model_uses_default_when_not_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_webtoon_env(monkeypatch)
        env_file = tmp_path / "image.env"
        env_file.write_text("GEMINI_API_KEY=image-key\n", encoding="utf-8")

        settings = WebtoonSettings.from_env(env_file, required_fields={"gemini_api_key"})

        assert settings.image_model == "gemini-3.1-flash-image-preview"

    def test_thinking_and_parallel_defaults_are_loaded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_webtoon_env(monkeypatch)
        env_file = tmp_path / "thinking.env"
        env_file.write_text("GEMINI_API_KEY=image-key\n", encoding="utf-8")

        settings = WebtoonSettings.from_env(env_file, required_fields={"gemini_api_key"})

        assert settings.llm_model == "gemini-3-flash-preview"
        assert settings.llm_thinking_level == "high"
        assert settings.ocr_model == "gemini-3-flash-preview"
        assert settings.ocr_thinking_level == "high"
        assert settings.ocr_extract_model == "gemini-3-flash-preview"
        assert settings.ocr_extract_thinking_level == "high"
        assert settings.api_parallelism == 3
        assert settings.enable_context_caching is True
