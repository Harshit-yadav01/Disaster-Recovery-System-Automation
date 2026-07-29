"""Live health collector: run read-only stat/show commands over SSH and assemble
a real-data payload for the dashboard's extra tiles.

Every command is read-only and runs against the configured Primary array in a
single SSH session. Each section is collected independently: if one command
fails or is unlicensed, that section is returned as ``None`` so the frontend can
hide the tile (real-or-remove) instead of showing a fabricated value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import Settings
from . import stats
from .ssh_client import ArraySSH, SSHConfig, SSHError

logger = logging.getLogger("dr.health")

#: Newest N alerts to surface on the dashboard.
_MAX_ALERTS = 8


class HealthError(RuntimeError):
    """Raised when the primary array cannot be reached for a health snapshot."""


def _primary_host(settings: Settings) -> str:
    return settings.alletra_primary_base_url or settings.alletra_base_url


def _ssh_cfg(settings: Settings, host: str) -> SSHConfig:
    return SSHConfig(
        host=host,
        username=settings.alletra_username,
        password=settings.alletra_password,
        port=settings.alletra_ssh_port,
        timeout=settings.alletra_timeout,
        role="primary",
    )


def _safe_run(arr: ArraySSH, command: str) -> str | None:
    """Run a command, returning None (not raising) if it fails."""
    try:
        return arr.run(command)
    except SSHError as exc:
        logger.warning("health: command %r failed: %s", command, exc)
        return None


def _cpu_section(text: str | None) -> dict | None:
    if not text:
        return None
    cpu = stats.parse_statcpu(text)
    if not cpu.per_node:
        return None
    return {
        "percent": cpu.overall,
        "nodes": [{"node": n, "percent": p} for n, p in sorted(cpu.per_node.items())],
    }


def _perf_section(text: str | None) -> dict | None:
    if not text:
        return None
    perf = stats.parse_statvv(text)
    if perf is None:
        return None
    section = {
        "iops": perf.iops,
        "throughput_kbps": perf.throughput_kbps,
        "latency_ms": perf.latency_ms,
        "vv_count": perf.vv_count,
    }
    if perf.busiest_name and perf.busiest_iops > 0:
        section["busiest"] = {"name": perf.busiest_name, "iops": perf.busiest_iops}
    return section


def _capacity_section(text: str | None) -> dict | None:
    if not text:
        return None
    cpgs = stats.parse_showcpg_space(text)
    if not cpgs:
        return None
    used = sum(c.used_mib for c in cpgs)
    total = sum(c.total_mib for c in cpgs)
    return {
        "used_pct": round(used / total * 100) if total else 0,
        "used_human": stats.human_mib(used),
        "total_human": stats.human_mib(total),
        "cpgs": [
            {
                "name": c.name,
                "used_pct": c.used_pct,
                "used_human": stats.human_mib(c.used_mib),
                "total_human": stats.human_mib(c.total_mib),
            }
            for c in cpgs
        ],
    }


def _alerts_section(text: str | None) -> list[dict] | None:
    if not text:
        return None
    alerts = stats.parse_showalert(text)
    if not alerts:
        return None
    # Newest first (timestamps are lexically sortable minus the trailing zone).
    alerts.sort(key=lambda a: a.time, reverse=True)
    return [
        {
            "time": a.time,
            "severity": a.severity,
            "tone": stats.severity_tone(a.severity),
            "type": a.type,
            "message": a.message,
        }
        for a in alerts[:_MAX_ALERTS]
    ]


def _replication_section(text: str | None) -> dict | None:
    if not text:
        return None
    system_status, groups = stats.parse_showrcopy_groups(text)
    if not groups:
        return None
    return {
        "system_status": system_status,
        "groups": [
            {
                "name": g.name,
                "status": g.status,
                "role": g.role,
                "mode": g.mode,
                "synced": g.synced,
                "total": g.total,
                "all_synced": g.all_synced,
                "last_sync": g.last_sync,
            }
            for g in groups
        ],
    }


def collect_health(settings: Settings) -> dict:
    """Open one SSH session to the primary array and collect all real tiles."""
    host = _primary_host(settings)
    if not host:
        raise HealthError("No primary array configured (set ALLETRA_PRIMARY_BASE_URL).")

    try:
        with ArraySSH(_ssh_cfg(settings, host)) as arr:
            cpu_txt = _safe_run(arr, "statcpu -iter 1")
            vv_txt = _safe_run(arr, "statvv -iter 1")
            cpg_txt = _safe_run(arr, "showcpg -space")
            alert_txt = _safe_run(arr, "showalert -n")
            rc_txt = _safe_run(arr, "showrcopy -d groups")
    except SSHError as exc:
        raise HealthError(str(exc)) from exc

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": SSHConfig.clean_host(host),
        "cpu": _cpu_section(cpu_txt),
        "performance": _perf_section(vv_txt),
        "capacity": _capacity_section(cpg_txt),
        "alerts": _alerts_section(alert_txt),
        "replication": _replication_section(rc_txt),
    }
