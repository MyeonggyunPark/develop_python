from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_LLM_MODEL = "gemini-2.5-flash"
DEFAULT_OCR_MODEL = "gemini-2.5-flash"
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_INSTAGRAM_GRAPH_VERSION = "v23.0"
DEFAULT_MAX_CORRECTION_ATTEMPTS = 2


def _read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class WebtoonSettings:
    llm_provider_api_key: str
    google_oauth_client_secret_file: Path
    google_oauth_token_file: Path
    google_drive_root_folder_id: str
    google_sheets_spreadsheet_id: str
    image_api_key: str
    ocr_api_key: str
    instagram_access_token: str
    instagram_business_account_id: str
    approval_default_user: str
    llm_model: str = DEFAULT_LLM_MODEL
    ocr_model: str = DEFAULT_OCR_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL
    instagram_graph_api_version: str = DEFAULT_INSTAGRAM_GRAPH_VERSION
    character_assets_dir: Path = REPO_ROOT / "agents" / "webtoon" / "assets" / "characters"
    font_assets_dir: Path = REPO_ROOT / "agents" / "webtoon" / "assets" / "fonts"
    font_file: Path | None = None
    max_correction_attempts: int = DEFAULT_MAX_CORRECTION_ATTEMPTS

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "WebtoonSettings":
        load_dotenv(env_path or DEFAULT_ENV_PATH)
        oauth_client_secret_file = Path(_read_required_env("GOOGLE_OAUTH_CLIENT_SECRET_FILE")).expanduser()
        oauth_token_file_value = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "").strip()
        oauth_token_file = (
            Path(oauth_token_file_value).expanduser()
            if oauth_token_file_value
            else REPO_ROOT / "agents" / "webtoon" / "secrets" / "google-oauth-token.json"
        )
        return cls(
            llm_provider_api_key=(os.getenv("LLM_PROVIDER_API_KEY", "").strip() or _read_required_env("IMAGE_API_KEY")),
            google_oauth_client_secret_file=oauth_client_secret_file,
            google_oauth_token_file=oauth_token_file,
            google_drive_root_folder_id=_read_required_env("GOOGLE_DRIVE_ROOT_FOLDER_ID"),
            google_sheets_spreadsheet_id=_read_required_env("GOOGLE_SHEETS_SPREADSHEET_ID"),
            image_api_key=_read_required_env("IMAGE_API_KEY"),
            ocr_api_key=(os.getenv("OCR_API_KEY", "").strip() or _read_required_env("IMAGE_API_KEY")),
            instagram_access_token=_read_required_env("INSTAGRAM_ACCESS_TOKEN"),
            instagram_business_account_id=_read_required_env("INSTAGRAM_BUSINESS_ACCOUNT_ID"),
            approval_default_user=_read_required_env("APPROVAL_DEFAULT_USER"),
            llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL,
            ocr_model=os.getenv("OCR_MODEL", DEFAULT_OCR_MODEL).strip() or DEFAULT_OCR_MODEL,
            image_model=os.getenv("IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL,
            instagram_graph_api_version=(
                os.getenv("INSTAGRAM_GRAPH_API_VERSION", DEFAULT_INSTAGRAM_GRAPH_VERSION).strip()
                or DEFAULT_INSTAGRAM_GRAPH_VERSION
            ),
            font_file=(
                Path(os.getenv("WEBTOON_FONT_FILE", "")).expanduser()
                if os.getenv("WEBTOON_FONT_FILE", "").strip()
                else None
            ),
            max_correction_attempts=max(
                0,
                int(os.getenv("MAX_CORRECTION_ATTEMPTS", str(DEFAULT_MAX_CORRECTION_ATTEMPTS)).strip() or DEFAULT_MAX_CORRECTION_ATTEMPTS),
            ),
        )
