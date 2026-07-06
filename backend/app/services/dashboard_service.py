"""Dashboard service: pick a storage provider and return dashboard data.

If the real Alletra provider is selected but the array is unreachable, we log a
warning and fall back to simulated data so the frontend never breaks.
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..providers import StorageProvider
from ..providers.alletra import AlletraError, AlletraProvider
from ..providers.simulated import SimulatedProvider
from ..schemas import DashboardData

logger = logging.getLogger("dr.service")


def _build_provider(settings: Settings) -> StorageProvider:
    if settings.storage_provider.lower() == "alletra":
        return AlletraProvider(settings)
    return SimulatedProvider()


async def get_dashboard_data(settings: Settings) -> DashboardData:
    """Return dashboard data from the configured provider, with safe fallback."""
    try:
        provider = _build_provider(settings)
        return await provider.get_dashboard()
    except AlletraError as exc:
        logger.warning("Alletra provider unavailable, using simulated data: %s", exc)
        return await SimulatedProvider().get_dashboard()
    except Exception as exc:  # noqa: BLE001 - never break the dashboard
        logger.exception("Unexpected provider error, using simulated data: %s", exc)
        return await SimulatedProvider().get_dashboard()
