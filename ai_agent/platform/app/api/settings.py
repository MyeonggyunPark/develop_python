from fastapi import APIRouter

from app.services.mock_data import get_settings_context

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def settings() -> dict:
    return get_settings_context()
