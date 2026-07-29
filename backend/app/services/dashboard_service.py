"""Dashboard service: return real dashboard data from the live array.

Data is read from the real HPE Alletra array over WSAPI. If the array is
unreachable or not configured the error is propagated as
:class:`DashboardUnavailableError` so the frontend shows nothing rather than
fabricated values — all displayed values are real, or not shown at all.
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..providers import StorageProvider
from ..providers.alletra import AlletraError, AlletraProvider
from ..schemas import DashboardData

logger = logging.getLogger("dr.service")


class DashboardUnavailableError(RuntimeError):
    """Raised when the live array cannot supply dashboard data."""


def _build_provider(settings: Settings) -> StorageProvider:
    return AlletraProvider(settings)


async def get_dashboard_data(settings: Settings) -> DashboardData:
    """Return real dashboard data from the live HPE Alletra array.

    Failures are surfaced as :class:`DashboardUnavailableError` rather than
    being masked with fabricated data, so the dashboard only ever shows real
    values.
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
