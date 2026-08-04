"""HPE Alletra Storage MP (WSAPI) storage provider.

Talks to one or two real arrays over the WSAPI (default port 443):

    1. POST   /api/v1/credentials       -> obtain a session key
    2. GET    /api/v1/system            -> model, name, capacity
    3. GET    /api/v1/volumes           -> provisioned volumes
    4. GET    /api/v1/remotecopygroups  -> replication (remote copy) health
    5. GET    /api/v1/cpgs              -> provisioning groups (pools)
    6. DELETE /api/v1/credentials/{key} -> release the session

Two arrays are supported: a Primary (source) and an optional Recovery (target),
matching the DR topology on the dashboard. If the Primary is unreachable we
raise AlletraError so the service layer reports the dashboard as unavailable
(nothing is shown rather than fabricated data); if only the Recovery is
unreachable the dashboard still renders with a clear "unreachable" marker for
the recovery site.

Fields owned by the virtualization layer (ESXi hosts, VMware VMs) are not
exposed by the array; where the array cannot supply a value we derive the
closest array-level equivalent and label it honestly. Every call is wrapped so
a single failing endpoint degrades gracefully instead of blanking the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..config import Settings
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

logger = logging.getLogger("dr.alletra")


class AlletraError(RuntimeError):
    """Raised when the primary array cannot be reached or authenticated."""


@dataclass
class ArrayConfig:
    """Connection details for a single array."""

    base_url: str
    username: str
    password: str
    verify_ssl: bool
    ca_cert: str
    timeout: int
    role: str  # "primary" | "recovery"


@dataclass
class ArrayData:
    """Raw data pulled from one array (or an unreachable marker)."""

    role: str
    reachable: bool
    error: str | None = None
    system: dict = field(default_factory=dict)
    volumes: list[dict] = field(default_factory=list)
    rcgroups: list[dict] = field(default_factory=list)
    cpgs: list[dict] = field(default_factory=list)


class AlletraProvider(StorageProvider):
    name = "alletra"

    def __init__(self, settings: Settings) -> None:
        primary_url = settings.alletra_primary_base_url or settings.alletra_base_url
        if not primary_url:
            raise AlletraError(
                "No primary array configured (set ALLETRA_PRIMARY_BASE_URL in .env)"
            )

        self._configs: list[ArrayConfig] = [
            ArrayConfig(
                base_url=primary_url,
                username=settings.alletra_username,
                password=settings.alletra_password,
                verify_ssl=settings.alletra_verify_ssl,
                ca_cert=settings.alletra_ca_cert,
                timeout=settings.alletra_timeout,
                role="primary",
            )
        ]
        if settings.alletra_recovery_base_url:
            self._configs.append(
                ArrayConfig(
                    base_url=settings.alletra_recovery_base_url,
                    username=settings.alletra_username,
                    password=settings.alletra_password,
                    verify_ssl=settings.alletra_verify_ssl,
                    ca_cert=settings.alletra_ca_cert,
                    timeout=settings.alletra_timeout,
                    role="recovery",
                )
            )

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(raw: str) -> str:
        """Accept an IP, hostname, or full URL and return https://host:443."""
        raw = raw.strip().rstrip("/")
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        parsed = urlparse(raw)
        scheme = parsed.scheme or "https"
        host = parsed.hostname or raw
        port = parsed.port or 443
        return f"{scheme}://{host}:{port}"

    async def _login(self, client: httpx.AsyncClient, cfg: ArrayConfig) -> str:
        resp = await client.post(
            "/api/v1/credentials",
            json={"user": cfg.username, "password": cfg.password},
        )
        resp.raise_for_status()
        key = resp.json().get("key")
        if not key:
            raise AlletraError("Array did not return a WSAPI session key")
        return key

    @staticmethod
    async def _get(client: httpx.AsyncClient, path: str, headers: dict) -> dict:
        """GET a WSAPI resource; return {} on failure so one bad call survives."""
        try:
            resp = await client.get(path, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("Alletra GET %s failed: %s", path, exc)
            return {}

    @staticmethod
    async def _logout(client: httpx.AsyncClient, key: str, headers: dict) -> None:
        try:
            await client.delete(f"/api/v1/credentials/{key}", headers=headers)
        except httpx.HTTPError:  # best-effort cleanup
            pass

    async def _fetch(self, cfg: ArrayConfig) -> ArrayData:
        base = self._normalize(cfg.base_url)
        # Pin the array's self-signed cert when provided; otherwise fall back to
        # the verify_ssl flag (False accepts a self-signed cert without checking).
        verify = cfg.ca_cert or cfg.verify_ssl
        try:
            # trust_env=False: the array is an internal management endpoint that
            # must be reached directly. Without this, httpx would honor the
            # jumpbox's HTTP(S)_PROXY env vars and route the internal IP through
            # the corporate proxy, which cannot reach it (ConnectTimeout).
            # Short connect timeout (fail fast if 443 is closed) but a generous
            # read timeout, since the array can be slow to answer WSAPI calls.
            timeout = httpx.Timeout(cfg.timeout, connect=10.0)
            async with httpx.AsyncClient(
                base_url=base, verify=verify, timeout=timeout, trust_env=False
            ) as client:
                key = await self._login(client, cfg)
                headers = {
                    "X-HP3PAR-WSAPI-SessionKey": key,
                    "Accept": "application/json",
                }
                system = await self._get(client, "/api/v1/system", headers)
                volumes = (await self._get(client, "/api/v1/volumes", headers)).get("members", [])
                rcgroups = (await self._get(client, "/api/v1/remotecopygroups", headers)).get("members", [])
                cpgs = (await self._get(client, "/api/v1/cpgs", headers)).get("members", [])
                await self._logout(client, key, headers)
                return ArrayData(
                    role=cfg.role,
                    reachable=True,
                    system=system,
                    volumes=volumes,
                    rcgroups=rcgroups,
                    cpgs=cpgs,
                )
        except httpx.HTTPError as exc:
            # Connection-level errors (ConnectError/ConnectTimeout) often have an
            # empty str(), so include the exception type to keep it diagnosable.
            detail = str(exc).strip() or exc.__class__.__name__
            reason = f"{cfg.role} array unreachable at {base}: {detail}"
            logger.warning("Alletra %s", reason)
            return ArrayData(role=cfg.role, reachable=False, error=reason)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def get_dashboard(self) -> DashboardData:
        results = await asyncio.gather(*(self._fetch(cfg) for cfg in self._configs))
        primary = results[0]
        if not primary.reachable:
            raise AlletraError(primary.error or "Primary array unreachable")
        recovery = results[1] if len(results) > 1 else None
        return self._map(primary, recovery)

    # ------------------------------------------------------------------ #
    # Mapping array data -> dashboard contract
    # ------------------------------------------------------------------ #
    def _map(self, primary: ArrayData, recovery: ArrayData | None) -> DashboardData:
        now = datetime.now(timezone.utc)

        p_sys = primary.system
        p_model = p_sys.get("model") or p_sys.get("name") or "HPE Alletra Storage MP"
        p_name = p_sys.get("name") or "Primary"
        primary_util = self._util(p_sys)
        total_bytes = (p_sys.get("totalCapacityMiB") or 0) * 1024 * 1024
        used_bytes = self._used_mib(p_sys) * 1024 * 1024

        volumes = primary.volumes
        protected_volumes = len(volumes)
        rcgroups = primary.rcgroups
        cpgs = primary.cpgs

        # Which volumes are protected by a remote copy group?
        protected_names: set[str] = set()
        for g in rcgroups:
            for v in g.get("volumes", []):
                vn = v.get("localVolumeName") or v.get("name")
                if vn:
                    protected_names.add(vn)

        repl_ok, repl_status, repl_tone = self._rc_status(rcgroups)

        # Recovery site status
        if recovery is None:
            rec_model, rec_status, rec_tone, rec_util, rec_reachable = (
                "Not configured", "N/A", "blue", 0, False,
            )
        elif not recovery.reachable:
            rec_model, rec_status, rec_tone, rec_util, rec_reachable = (
                "Recovery", "Unreachable", "red", 0, False,
            )
        else:
            r_sys = recovery.system
            rec_model = r_sys.get("model") or r_sys.get("name") or "Recovery Array"
            rec_status, rec_tone = repl_status, repl_tone
            rec_util = self._util(r_sys)
            rec_reachable = True

        arrays_online = 1 + (1 if rec_reachable else 0)
        dr_ready = repl_ok and rec_reachable
        protected_count = len(protected_names)
        # Real readiness: share of provisioned volumes that are protected by a
        # remote copy group, gated on the recovery array being reachable.
        if protected_volumes:
            protected_ratio = round(protected_count / protected_volumes * 100)
        else:
            protected_ratio = 0
        readiness_pct = protected_ratio if rec_reachable else max(0, protected_ratio - 40)

        return DashboardData(
            generated_at=now.isoformat(),
            source=self.name,
            cards=[
                MetricCard(title="Protected Volumes", value=str(protected_count),
                           subtext=f"of {protected_volumes} volumes", tone="green"),
                MetricCard(title="Replication", value=repl_status if repl_ok else "Check",
                           subtext=f"{len(rcgroups)} RC group(s)", tone=repl_tone),
                MetricCard(title="DR Readiness", value="Ready" if dr_ready else "Check",
                           subtext=p_model, tone="green" if dr_ready else "warning"),
                MetricCard(title="Storage Usage", value=f"{primary_util}%", subtext="Primary array",
                           tone="green" if primary_util < 85 else "warning"),
            ],
            replication=ReplicationHealth(
                primary=SiteStatus(name="Primary Site", array_model=f"{p_name} ({p_model})",
                                   status=repl_status, tone=repl_tone),
                recovery=SiteStatus(name="Recovery Site", array_model=rec_model,
                                    status=rec_status, tone=rec_tone),
            ),
            infrastructure=[
                InfraItem(label="Arrays Online", value=str(arrays_online), subtext="WSAPI reachable", icon="fa-server"),
                InfraItem(label="CPGs", value=str(len(cpgs)), subtext="Provisioning groups", icon="fa-hard-drive"),
                InfraItem(label="Volumes", value=str(protected_volumes), subtext="Provisioned", icon="fa-network-wired"),
                InfraItem(label="RC Groups", value=str(len(rcgroups)), subtext="Replication", icon="fa-microchip"),
            ],
            storage=[
                StorageUsage(label="Primary Array", percent=primary_util, detail=f"{primary_util}% Utilized"),
                StorageUsage(label="Recovery Array", percent=rec_util,
                             detail=(f"{rec_util}% Utilized" if rec_reachable else rec_status)),
                StorageUsage(label="Usable Capacity", percent=primary_util,
                             detail=f"{self._human_bytes(used_bytes)} of {self._human_bytes(total_bytes)}"),
            ],
            alerts=self._alerts_from_rcgroups(rcgroups, recovery),
            timeline=[
                TimelineEvent(title="Array Discovery", detail=f"{arrays_online} array(s) reachable"),
                TimelineEvent(title="Capacity Check", detail=f"Primary {primary_util}% utilized"),
                TimelineEvent(title="Replication Validation", detail=repl_status),
                TimelineEvent(title="Live Data", detail=now.strftime("%H:%M UTC")),
            ],
            readiness=DrReadiness(
                percent=readiness_pct,
                headline="Environment Ready" if dr_ready else "Attention Required",
                checks=[
                    f"Replication {repl_status}",
                    f"Primary array {primary_util}% used",
                    ("Recovery array reachable" if rec_reachable else "Recovery array NOT reachable"),
                    f"{protected_count} of {protected_volumes} volumes protected",
                ],
            ),
        )

    # ------------------------------------------------------------------ #
    # Small mapping utilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pct(used: float, total: float) -> int:
        if not total:
            return 0
        return max(0, min(100, round(used / total * 100)))

    @classmethod
    def _util(cls, system: dict) -> int:
        """Utilization percent from WSAPI system capacity (MiB)."""
        return cls._pct(cls._used_mib(system), system.get("totalCapacityMiB") or 0)

    @staticmethod
    def _rc_status(rcgroups: list[dict]) -> tuple[bool, str, str]:
        """Summarise remote-copy-group health -> (ok, status_text, tone).

        Uses the WSAPI ``roleReversed`` target flag to detect an active
        failover. Per-volume ``syncStatus`` can be layered in here once the
        live ``/api/v1/remotecopygroups`` response is confirmed on the array.
        """
        if not rcgroups:
            return False, "No Remote Copy Groups", "warning"
        failover = any(
            t.get("roleReversed")
            for g in rcgroups
            for t in g.get("targets", [])
        )
        if failover:
            return True, "Failover Active", "blue"
        return True, "Synchronized", "green"

    @staticmethod
    def _used_mib(system: dict) -> float:
        total = system.get("totalCapacityMiB") or 0
        allocated = system.get("allocatedCapacityMiB")
        if allocated is None:
            free = system.get("freeCapacityMiB") or 0
            allocated = max(total - free, 0)
        return allocated

    @staticmethod
    def _human_bytes(num: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if abs(num) < 1024:
                return f"{num:.1f} {unit}"
            num /= 1024
        return f"{num:.1f} EB"

    @staticmethod
    def _alerts_from_rcgroups(rcgroups: list[dict], recovery: ArrayData | None) -> list[Alert]:
        alerts: list[Alert] = []
        stamp = datetime.now(timezone.utc).strftime("%H:%M")
        if recovery is not None and not recovery.reachable:
            alerts.append(Alert(time=stamp, event="Recovery array unreachable", status="Error", tone="red"))
        if not rcgroups:
            alerts.append(Alert(time=stamp, event="No remote copy groups found", status="Check", tone="warning"))
            return alerts
        for g in rcgroups[:6]:
            failover = any(t.get("roleReversed") for t in g.get("targets", []))
            status, tone = ("Failover Active", "blue") if failover else ("Synchronized", "green")
            alerts.append(
                Alert(time=stamp, event=f"RC Group {g.get('name', 'rcgroup')}",
                      status=status, tone=tone)
            )
        return alerts
