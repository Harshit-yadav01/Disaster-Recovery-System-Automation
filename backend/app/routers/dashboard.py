"""Dashboard routes: return live data from the configured storage provider."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..schemas import DashboardData
from ..security import get_current_user
from ..services.dashboard_service import get_dashboard_data

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardData)
async def dashboard(
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> DashboardData:
    """Full dashboard payload. Requires a valid bearer token."""
    return await get_dashboard_data(settings)
