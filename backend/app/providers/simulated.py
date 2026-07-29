"""Simulated storage provider.

Produces realistic, slightly randomised dashboard data so the whole stack can
run and be demoed without a physical array. This mirrors exactly the shape the
real Alletra provider returns.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from ..schemas import (
    Alert,
    DashboardData,
    DrReadiness,
    InfraItem,
    MetricCard,
    ReplicationHealth,
    SiteStatus,
    StorageUsage,
    TimelineEvent,
)
from . import StorageProvider


class SimulatedProvider(StorageProvider):
    name = "simulated"

    async def get_dashboard(self) -> DashboardData:
        # A little jitter so the dashboard visibly "lives" on refresh.
        protected = 160 + random.randint(0, 12)
        primary_util = random.randint(68, 76)
        recovery_util = random.randint(50, 60)
        readiness_pct = random.randint(96, 99)

        return DashboardData(
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=self.name,
            cards=[
                MetricCard(
                    title="Protected Volumes",
                    value=str(protected),
                    subtext=f"+{random.randint(4, 14)} Today",
                    tone="green",
                ),
                MetricCard(
                    title="Replication",
                    value="Healthy",
                    subtext="0 Errors",
                    tone="green",
                ),
                MetricCard(
                    title="DR Readiness",
                    value=f"{readiness_pct}%",
                    subtext="Excellent",
                    tone="green",
                ),
                MetricCard(
                    title="Storage Usage",
                    value=f"{primary_util}%",
                    subtext="Healthy",
                    tone="green",
                ),
            ],
            replication=ReplicationHealth(
                primary=SiteStatus(
                    name="Primary Site",
                    array_model="HPE Alletra 6000",
                    status="Healthy",
                    tone="green",
                ),
                recovery=SiteStatus(
                    name="Recovery Site",
                    array_model="HPE Alletra 6000",
                    status="Synchronized",
                    tone="green",
                ),
            ),
            infrastructure=[
                InfraItem(label="Arrays Online", value="2", subtext="WSAPI reachable", icon="fa-server"),
                InfraItem(label="CPGs", value="4", subtext="Provisioning groups", icon="fa-hard-drive"),
                InfraItem(label="Volumes", value=str(protected), subtext="Provisioned", icon="fa-network-wired"),
                InfraItem(label="RC Groups", value="3", subtext="Replication", icon="fa-microchip"),
            ],
            storage=[
                StorageUsage(label="Primary Array", percent=primary_util, detail=f"{primary_util}% Utilized"),
                StorageUsage(label="Recovery Array", percent=recovery_util, detail=f"{recovery_util}% Utilized"),
                StorageUsage(label="Usable Capacity", percent=100, detail="120.0 TB"),
            ],
            alerts=[
                Alert(time="10:14 AM", event="Replication Completed", status="Success", tone="green"),
                Alert(time="10:18 AM", event="Storage Health Verified", status="Healthy", tone="green"),
                Alert(time="10:21 AM", event="Witness Connected", status="Online", tone="green"),
                Alert(time="10:29 AM", event="Recovery Plan Validated", status="Ready", tone="blue"),
            ],
            timeline=[
                TimelineEvent(title="Environment Discovery", detail="Completed Successfully"),
                TimelineEvent(title="Replication Validation", detail="All Volumes Synchronized"),
                TimelineEvent(title="DR Readiness Check", detail=f"{readiness_pct}% Ready"),
                TimelineEvent(title="Recovery Plan Generated", detail="Ready for Execution"),
            ],
            readiness=DrReadiness(
                percent=readiness_pct,
                headline="Environment Ready",
                checks=[
                    "Replication Healthy",
                    "Storage Online",
                    "Witness Connected",
                    "Recovery Plans Validated",
                ],
            ),
        )
