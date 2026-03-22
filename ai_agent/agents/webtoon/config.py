from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_GOOGLE_OAUTH_CLIENT_SECRET_FILE = REPO_ROOT / "agents" / "webtoon" / "secrets" / "google-oauth-client-secret.json"
DEFAULT_GOOGLE_OAUTH_TOKEN_FILE = REPO_ROOT / "agents" / "webtoon" / "secrets" / "google-oauth-token.json"
DEFAULT_LLM_MODEL = "gemini-3-flash-preview"
DEFAULT_OCR_MODEL = "gemini-3-flash-preview"
DEFAULT_OCR_EXTRACT_MODEL = DEFAULT_OCR_MODEL
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_LLM_THINKING_LEVEL = "high"
DEFAULT_OCR_THINKING_LEVEL = "high"
DEFAULT_OCR_EXTRACT_THINKING_LEVEL = "high"
DEFAULT_MAX_CORRECTION_ATTEMPTS = 1
DEFAULT_MAX_STORY_REPLAN_ATTEMPTS = 1
DEFAULT_API_PARALLELISM = 3
DEFAULT_ENABLE_CONTEXT_CACHING = True
DEFAULT_CONTEXT_CACHE_TTL = "3600s"
WEBTOON_REQUIRED_FIELDS = frozenset(
    {
        "google_oauth_client_secret_file",
        "google_drive_root_folder_id",
        "gemini_api_key",
        "approval_default_user",
    }
)


def _read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _read_env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def _read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _read_env_alias(primary: str, *aliases: str, default: str = "") -> str:
    for name in (primary, *aliases):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _read_required_env_alias(primary: str, *aliases: str) -> str:
    value = _read_env_alias(primary, *aliases, default="")
    if not value:
        raise ValueError(f"Missing required environment variable: {primary}")
    return value


def _read_optional_path(name: str, default: Path) -> Path:
    value = _read_env(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class WebtoonSettings:
    google_oauth_client_secret_file: Path
    google_oauth_token_file: Path
    google_drive_root_folder_id: str
    gemini_api_key: str
    approval_default_user: str
    llm_model: str = DEFAULT_LLM_MODEL
    ocr_model: str = DEFAULT_OCR_MODEL
    ocr_extract_model: str = DEFAULT_OCR_EXTRACT_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL
    llm_thinking_level: str = DEFAULT_LLM_THINKING_LEVEL
    ocr_thinking_level: str = DEFAULT_OCR_THINKING_LEVEL
    ocr_extract_thinking_level: str = DEFAULT_OCR_EXTRACT_THINKING_LEVEL
    character_assets_dir: Path = REPO_ROOT / "agents" / "webtoon" / "assets" / "characters"
    font_assets_dir: Path = REPO_ROOT / "agents" / "webtoon" / "assets" / "fonts"
    font_file: Path | None = None
    max_correction_attempts: int = DEFAULT_MAX_CORRECTION_ATTEMPTS
    max_story_replan_attempts: int = DEFAULT_MAX_STORY_REPLAN_ATTEMPTS
    api_parallelism: int = DEFAULT_API_PARALLELISM
    enable_context_caching: bool = DEFAULT_ENABLE_CONTEXT_CACHING
    context_cache_ttl: str = DEFAULT_CONTEXT_CACHE_TTL

    @classmethod
    def from_env(
        cls,
        env_path: Path | None = None,
        *,
        required_fields: set[str] | frozenset[str] | None = None,
    ) -> "WebtoonSettings":
        load_dotenv(env_path or DEFAULT_ENV_PATH)
        required = WEBTOON_REQUIRED_FIELDS if required_fields is None else frozenset(required_fields)
        oauth_client_secret_file = _read_optional_path(
            "GOOGLE_OAUTH_CLIENT_SECRET_FILE",
            DEFAULT_GOOGLE_OAUTH_CLIENT_SECRET_FILE,
        )
        oauth_token_file = _read_optional_path("GOOGLE_OAUTH_TOKEN_FILE", DEFAULT_GOOGLE_OAUTH_TOKEN_FILE)
        if "google_oauth_client_secret_file" in required and not _read_env("GOOGLE_OAUTH_CLIENT_SECRET_FILE"):
            raise ValueError("Missing required environment variable: GOOGLE_OAUTH_CLIENT_SECRET_FILE")
        return cls(
            google_oauth_client_secret_file=oauth_client_secret_file,
            google_oauth_token_file=oauth_token_file,
            google_drive_root_folder_id=(
                _read_required_env("GOOGLE_DRIVE_ROOT_FOLDER_ID")
                if "google_drive_root_folder_id" in required
                else _read_env("GOOGLE_DRIVE_ROOT_FOLDER_ID")
            ),
            gemini_api_key=(
                _read_required_env_alias("GEMINI_API_KEY", "IMAGE_API_KEY")
                if "gemini_api_key" in required
                else _read_env_alias("GEMINI_API_KEY", "IMAGE_API_KEY")
            ),
            approval_default_user=(
                _read_required_env("APPROVAL_DEFAULT_USER")
                if "approval_default_user" in required
                else _read_env("APPROVAL_DEFAULT_USER")
            ),
            llm_model=_read_env("LLM_MODEL", DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
            ocr_model=_read_env("OCR_MODEL", DEFAULT_OCR_MODEL) or DEFAULT_OCR_MODEL,
            ocr_extract_model=_read_env("OCR_EXTRACT_MODEL", DEFAULT_OCR_EXTRACT_MODEL) or DEFAULT_OCR_EXTRACT_MODEL,
            image_model=_read_env("IMAGE_MODEL", DEFAULT_IMAGE_MODEL) or DEFAULT_IMAGE_MODEL,
            llm_thinking_level=_read_env("LLM_THINKING_LEVEL", DEFAULT_LLM_THINKING_LEVEL) or DEFAULT_LLM_THINKING_LEVEL,
            ocr_thinking_level=_read_env("OCR_THINKING_LEVEL", DEFAULT_OCR_THINKING_LEVEL) or DEFAULT_OCR_THINKING_LEVEL,
            ocr_extract_thinking_level=(
                _read_env("OCR_EXTRACT_THINKING_LEVEL", DEFAULT_OCR_EXTRACT_THINKING_LEVEL)
                or DEFAULT_OCR_EXTRACT_THINKING_LEVEL
            ),
            font_file=(
                Path(_read_env("WEBTOON_FONT_FILE")).expanduser()
                if _read_env("WEBTOON_FONT_FILE")
                else None
            ),
            max_correction_attempts=max(
                0,
                int(_read_env("MAX_CORRECTION_ATTEMPTS", str(DEFAULT_MAX_CORRECTION_ATTEMPTS)) or DEFAULT_MAX_CORRECTION_ATTEMPTS),
            ),
            max_story_replan_attempts=max(
                0,
                int(
                    _read_env(
                        "MAX_STORY_REPLAN_ATTEMPTS",
                        str(DEFAULT_MAX_STORY_REPLAN_ATTEMPTS),
                    )
                    or DEFAULT_MAX_STORY_REPLAN_ATTEMPTS
                ),
            ),
            api_parallelism=max(
                1,
                int(_read_env("API_PARALLELISM", str(DEFAULT_API_PARALLELISM)) or DEFAULT_API_PARALLELISM),
            ),
            enable_context_caching=_read_bool_env("ENABLE_CONTEXT_CACHING", DEFAULT_ENABLE_CONTEXT_CACHING),
            context_cache_ttl=_read_env("CONTEXT_CACHE_TTL", DEFAULT_CONTEXT_CACHE_TTL) or DEFAULT_CONTEXT_CACHE_TTL,
        )
