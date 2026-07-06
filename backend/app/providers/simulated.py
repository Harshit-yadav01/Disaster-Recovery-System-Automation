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
    NetworkStat,
    PerformanceBar,
    PerformanceCharts,
    ReplicationHealth,
    SiteStatus,
    StorageUsage,
    TimelineEvent,
    VirtualMachine,
)
from . import StorageProvider


class SimulatedProvider(StorageProvider):
    name = "simulated"

    async def get_dashboard(self) -> DashboardData:
        # A little jitter so the dashboard visibly "lives" on refresh.
        protected = 160 + random.randint(0, 12)
        primary_util = random.randint(68, 76)
        recovery_util = random.randint(50, 60)
        cpu = random.randint(32, 45)
        memory = random.randint(58, 68)
        iops = round(random.uniform(7.5, 9.0), 1)
        readiness_pct = random.randint(96, 99)

        return DashboardData(
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=self.name,
            cards=[
                MetricCard(
                    title="Protected VMs",
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
                InfraItem(label="ESXi Hosts", value="12", subtext="All Connected", icon="fa-server"),
                InfraItem(label="Datastores", value="18", subtext="Healthy", icon="fa-hard-drive"),
                InfraItem(label="Networks", value="8", subtext="No Packet Loss", icon="fa-network-wired"),
                InfraItem(label="CPU Usage", value=f"{cpu}%", subtext="Optimal", icon="fa-microchip"),
            ],
            storage=[
                StorageUsage(label="Primary Array", percent=primary_util, detail=f"{primary_util}% Utilized"),
                StorageUsage(label="Recovery Array", percent=recovery_util, detail=f"{recovery_util}% Utilized"),
                StorageUsage(label="Replication Bandwidth", percent=84, detail="1.8 Gbps"),
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
            performance_bars=[
                PerformanceBar(label="CPU Utilization", percent=cpu, value=f"{cpu}%"),
                PerformanceBar(label="Memory Usage", percent=memory, value=f"{memory}%"),
                PerformanceBar(label="Storage IOPS", percent=82, value=f"{iops}K IOPS"),
            ],
            performance_charts=PerformanceCharts(
                cpu_labels=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                cpu_series=[random.randint(30, 70) for _ in range(7)],
                memory_labels=["Host1", "Host2", "Host3", "Host4"],
                memory_series=[random.randint(55, 85) for _ in range(4)],
            ),
            virtual_machines=[
                VirtualMachine(name="ERP-APP-01", host="ESXi-01", status="Running", status_tone="healthy", replication="Healthy"),
                VirtualMachine(name="Oracle-DB", host="ESXi-02", status="Running", status_tone="healthy", replication="Healthy"),
                VirtualMachine(name="FileServer", host="ESXi-03", status="Warning", status_tone="warning", replication="Syncing"),
                VirtualMachine(name="WebServer", host="ESXi-04", status="Running", status_tone="healthy", replication="Healthy"),
            ],
            network=[
                NetworkStat(label="Network Latency", value="2.3 ms", detail="Excellent"),
                NetworkStat(label="Bandwidth", value="1.8 Gbps", detail="Replication Link"),
                NetworkStat(label="Datastore Usage", value="68% Used", detail="68% Used", percent=68),
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
