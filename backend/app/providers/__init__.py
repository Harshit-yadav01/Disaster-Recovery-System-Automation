"""Storage provider abstraction.

A provider knows how to produce a full ``DashboardData`` payload. The
``alletra`` provider talks to a real HPE Alletra array over its WSAPI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import DashboardData


class StorageProvider(ABC):
    """Interface implemented by every storage data source."""

    #: short identifier reported back to the frontend ("alletra")
    name: str = "base"

    @abstractmethod
    async def get_dashboard(self) -> DashboardData:
        """Return the full dashboard payload."""
        raise NotImplementedError
