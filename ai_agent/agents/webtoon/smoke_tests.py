from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from .clients import (
    GeminiOcrClient,
    GeminiImageClient,
    GeminiTextClient,
    GoogleWorkspaceClient,
    build_smoke_test_image_bytes,
)
from .config import WebtoonSettings


def _run_step(results: dict[str, Any], name: str, action) -> None:
    started_at = perf_counter()
    try:
        data = action()
        results["services"][name] = {
            "ok": True,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "data": data,
        }
    except Exception as error:  # noqa: BLE001
        results["services"][name] = {
            "ok": False,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "error": f"{type(error).__name__}: {error}",
        }


def run_smoke_tests(settings: WebtoonSettings, services: list[str] | None = None) -> dict[str, Any]:
    selected = set(services or ["llm", "ocr", "image", "drive"])
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "services": {},
    }
    google_client: GoogleWorkspaceClient | None = None

    if "llm" in selected:
        _run_step(results, "llm", lambda: GeminiTextClient(settings).smoke_test())

    if "ocr" in selected:
        smoke_image = build_smoke_test_image_bytes()
        _run_step(results, "ocr", lambda: GeminiOcrClient(settings).smoke_test(smoke_image))

    if "image" in selected:
        _run_step(results, "image", lambda: GeminiImageClient(settings).smoke_test())

    if "drive" in selected:
        if google_client is None:
            google_client = GoogleWorkspaceClient(settings)
        _run_step(results, "drive", lambda: google_client.smoke_test_drive())

    return results
