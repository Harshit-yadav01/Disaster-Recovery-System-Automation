"""Dashboard service: pick a storage provider and return dashboard data.

When the real Alletra provider is selected, its data is returned as-is; if the
array is unreachable the error is propagated so the frontend shows an explicit
"unavailable" state instead of misleading simulated values. Simulated data is
only ever returned when it is the explicitly configured provider.
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..providers import StorageProvider
from ..providers.alletra import AlletraError, AlletraProvider
from ..providers.simulated import SimulatedProvider
from ..schemas import DashboardData

logger = logging.getLogger("dr.service")


class DashboardUnavailableError(RuntimeError):
    """Raised when the configured live provider cannot supply dashboard data."""


def _build_provider(settings: Settings) -> StorageProvider:
    if settings.storage_provider.lower() == "alletra":
        return AlletraProvider(settings)
    return SimulatedProvider()


async def get_dashboard_data(settings: Settings) -> DashboardData:
    """Return dashboard data from the configured provider.

    For the live (alletra) provider, failures are surfaced as
    :class:`DashboardUnavailableError` rather than being masked with simulated
    data, so the dashboard only ever shows real values.
    """
    provider = _build_provider(settings)
    try:
        return await provider.get_dashboard()
    except AlletraError as exc:
        logger.warning("Alletra provider unavailable: %s", exc)
        raise DashboardUnavailableError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Provider error: %s", exc)
        raise DashboardUnavailableError(str(exc)) from exc
