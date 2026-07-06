"""Pydantic schemas that define the API contract consumed by the frontend."""
from __future__ import annotations

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str
    password: str
    environment: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


# --------------------------------------------------------------------------- #
# Dashboard building blocks
# --------------------------------------------------------------------------- #
class MetricCard(BaseModel):
    """Top summary card (Protected VMs, Replication, DR Readiness, Storage)."""

    title: str
    value: str
    subtext: str
    tone: str = "green"  # green | blue | warning | red


class SiteStatus(BaseModel):
    name: str
    array_model: str
    status: str
    tone: str = "green"


class ReplicationHealth(BaseModel):
    primary: SiteStatus
    recovery: SiteStatus


class InfraItem(BaseModel):
    label: str
    value: str
    subtext: str
    icon: str  # font-awesome class, e.g. "fa-server"


class StorageUsage(BaseModel):
    label: str
    percent: int
    detail: str


class Alert(BaseModel):
    time: str
    event: str
    status: str
    tone: str = "green"


class TimelineEvent(BaseModel):
    title: str
    detail: str


class PerformanceBar(BaseModel):
    label: str
    percent: int
    value: str


class PerformanceCharts(BaseModel):
    cpu_labels: list[str]
    cpu_series: list[int]
    memory_labels: list[str]
    memory_series: list[int]


class VirtualMachine(BaseModel):
    name: str
    host: str
    status: str
    status_tone: str  # healthy | warning | red
    replication: str


class NetworkStat(BaseModel):
    label: str
    value: str
    detail: str
    percent: int | None = None


class DrReadiness(BaseModel):
    percent: int
    headline: str
    checks: list[str]


# --------------------------------------------------------------------------- #
# Full dashboard payload
# --------------------------------------------------------------------------- #
class DashboardData(BaseModel):
    generated_at: str
    source: str  # "simulated" or "alletra"
    cards: list[MetricCard]
    replication: ReplicationHealth
    infrastructure: list[InfraItem]
    storage: list[StorageUsage]
    alerts: list[Alert]
    timeline: list[TimelineEvent]
    performance_bars: list[PerformanceBar]
    performance_charts: PerformanceCharts
    virtual_machines: list[VirtualMachine]
    network: list[NetworkStat]
    readiness: DrReadiness
