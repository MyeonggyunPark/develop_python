from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .clients import (
    GeminiOcrClient,
    GeminiImageClient,
    GeminiTextClient,
    GoogleWorkspaceClient,
    InstagramGraphClient,
    build_smoke_test_image_bytes,
)
from .config import WebtoonSettings


def _run_step(results: dict[str, Any], name: str, action) -> None:
    try:
        results["services"][name] = {"ok": True, "data": action()}
    except Exception as error:  # noqa: BLE001
        results["services"][name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}


def run_smoke_tests(settings: WebtoonSettings, services: list[str] | None = None) -> dict[str, Any]:
    selected = set(services or ["llm", "ocr", "image", "drive", "sheets", "instagram"])
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "services": {},
    }

    if "llm" in selected:
        _run_step(results, "llm", lambda: GeminiTextClient(settings).smoke_test())

    if "ocr" in selected:
        smoke_image = build_smoke_test_image_bytes()
        _run_step(results, "ocr", lambda: GeminiOcrClient(settings).smoke_test(smoke_image))

    if "image" in selected:
        _run_step(results, "image", lambda: GeminiImageClient(settings).smoke_test())

    google_client = GoogleWorkspaceClient(settings)
    if "drive" in selected:
        _run_step(results, "drive", lambda: google_client.smoke_test_drive())

    if "sheets" in selected:
        _run_step(results, "sheets", lambda: google_client.smoke_test_sheets())

    if "instagram" in selected:
        _run_step(results, "instagram", lambda: InstagramGraphClient(settings).smoke_test())

    return results
