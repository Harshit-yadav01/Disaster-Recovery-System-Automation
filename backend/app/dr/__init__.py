"""Disaster-recovery automation for HPE Alletra / 3PAR Remote Copy.

Exposes :class:`DrManager`, a thin, safety-focused wrapper around the official
HPE ``python-3parclient`` that drives the replication lifecycle over WSAPI.
"""
from .remote_copy import ACTIONS, DrError, DrManager, GroupResult

__all__ = ["ACTIONS", "DrError", "DrManager", "GroupResult"]
