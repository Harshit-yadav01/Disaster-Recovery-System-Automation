"""HPE Alletra / 3PAR Remote Copy disaster-recovery operations.

Drives the replication lifecycle over WSAPI (HTTPS, port 443):

    failover  ->  recover  ->  restore

Talks to the array directly with httpx - the same transport the dashboard
provider uses - so there is no heavy SDK dependency and the corporate proxy is
bypassed for the internal management IP (``trust_env=False``).

Following HPE Remote Copy semantics, ALL disaster-recovery actions are issued
against the SECONDARY (target / backup) array. auto_synchronize is intentionally
never enabled: each phase is performed explicitly so the operator keeps full
control of the replication cycle (the customer requirement).

WSAPI recover-from-disaster action codes (verified against HPE's
python-3parclient RC_ACTION_* constants and the WSAPI reference):

    7  RC_ACTION_CHANGE_TO_PRIMARY    -> failover (secondary becomes source, R/W)
    8  RC_ACTION_MIGRATE_GROUP        -> recover  (reverse-sync to old primary)
    9  RC_ACTION_CHANGE_TO_SECONDARY  -> restore  (return to original direction)

The action is applied via:  POST /api/v1/remotecopygroups/{name}  {"action": N}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("dr.remotecopy")

# Verified WSAPI action codes for POST /remotecopygroups/{name} {"action": N}.
# Start/stop/sync manage the replication link; failover/recover/restore drive
# the disaster-recovery role changes.
ACTION_START = 3     # start replication
ACTION_STOP = 4      # stop replication (required before a planned failover)
ACTION_SYNC = 5      # synchronize
ACTION_FAILOVER = 7  # RC_ACTION_CHANGE_TO_PRIMARY
ACTION_RECOVER = 8   # RC_ACTION_MIGRATE_GROUP
ACTION_RESTORE = 9   # RC_ACTION_CHANGE_TO_SECONDARY

ACTIONS = {
    "start": ACTION_START,
    "stop": ACTION_STOP,
    "sync": ACTION_SYNC,
    "failover": ACTION_FAILOVER,
    "recover": ACTION_RECOVER,
    "restore": ACTION_RESTORE,
}

# start/stop/sync modify the replication link -> PUT /remotecopygroups/{name}.
# failover/recover/restore are disaster-recovery role changes -> POST.
_PUT_PHASES = {"start", "stop", "sync"}


class DrError(RuntimeError):
    """Raised when the array cannot be reached or authenticated."""


@dataclass
class GroupResult:
    """Outcome of a single Remote Copy group (or volume) operation."""

    name: str
    ok: bool
    detail: str


class DrManager:
    """Drive Remote Copy DR actions against one array (the secondary/target).

    Use as a context manager so login/logout are handled automatically::

        with DrManager(host, user, pw, dry_run=True) as dr:
            dr.failover(groups=["rcg1", "rcg2"])
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        timeout: int = 30,
        verify: bool | str = False,
        dry_run: bool = True,
    ) -> None:
        self.host = self._clean_host(host)
        self._username = username
        self._password = password
        self.dry_run = dry_run
        self._base_url = f"https://{self.host}:443"
        # trust_env=False -> reach the internal array directly, never via the
        # corporate proxy (same fix used by the dashboard provider).
        self._client = httpx.Client(
            base_url=self._base_url, verify=verify, timeout=timeout, trust_env=False
        )
        self._key: str | None = None

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "DrManager":
        try:
            resp = self._client.post(
                "/api/v1/credentials",
                json={"user": self._username, "password": self._password},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._client.close()
            raise DrError(f"Login to array {self.host} failed: {exc}") from exc
        self._key = resp.json().get("key")
        if not self._key:
            self._client.close()
            raise DrError("Array did not return a WSAPI session key")
        self._client.headers.update(
            {"X-HP3PAR-WSAPI-SessionKey": self._key, "Accept": "application/json"}
        )
        logger.info("Logged in to array %s (dry_run=%s)", self.host, self.dry_run)
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            if self._key:
                self._client.delete(f"/api/v1/credentials/{self._key}")
                logger.info("Logged out of array %s", self.host)
        except httpx.HTTPError:  # best-effort cleanup
            pass
        finally:
            self._client.close()

    @staticmethod
    def _clean_host(raw: str) -> str:
        """Accept an IP, hostname, or URL and return just the host."""
        raw = raw.strip().rstrip("/")
        for prefix in ("https://", "http://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        return raw.split("/")[0].split(":")[0]

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            return body.get("desc") or body.get("message") or resp.text[:200]
        except Exception:  # noqa: BLE001
            return resp.text[:200]

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def list_groups(self) -> list[dict]:
        """Return all Remote Copy groups on this array."""
        resp = self._client.get("/api/v1/remotecopygroups")
        resp.raise_for_status()
        return resp.json().get("members", [])

    def _select(self, groups: list[str] | None) -> list[str]:
        """Resolve the target group names, defaulting to ALL groups.

        Group names are treated as variables (never hardcoded); an explicit
        list is validated against what actually exists on the array.
        """
        available = [g.get("name") for g in self.list_groups() if g.get("name")]
        if not groups:
            return available
        missing = [g for g in groups if g not in available]
        if missing:
            raise ValueError(
                f"Remote Copy group(s) not found on {self.host}: {missing}. "
                f"Available: {available}"
            )
        return groups

    # ------------------------------------------------------------------ #
    # Replication lifecycle
    # ------------------------------------------------------------------ #
    def _run_action(self, phase: str, groups: list[str] | None) -> list[GroupResult]:
        action = ACTIONS[phase]
        selected = self._select(groups)
        if not selected:
            logger.warning("No Remote Copy groups to %s on %s", phase, self.host)
            return []

        results: list[GroupResult] = []
        for name in selected:
            if self.dry_run:
                logger.info(
                    "[DRY-RUN] would %s group '%s' (action=%s) on %s",
                    phase, name, action, self.host,
                )
                results.append(GroupResult(name, True, "dry-run"))
                continue
            try:
                logger.info("%s group '%s' (action=%s) on %s ...", phase, name, action, self.host)
                # Body carries only {"action": N}: no auto_synchronize / auto
                # flags are ever sent, keeping each phase manual. start/stop/sync
                # use PUT; failover/recover/restore use POST.
                request = self._client.put if phase in _PUT_PHASES else self._client.post
                resp = request(
                    f"/api/v1/remotecopygroups/{name}", json={"action": action}
                )
                if resp.status_code >= 400:
                    detail = self._error_detail(resp)
                    logger.error("%s of '%s' FAILED (HTTP %s): %s",
                                 phase, name, resp.status_code, detail)
                    results.append(GroupResult(name, False, f"HTTP {resp.status_code}: {detail}"))
                else:
                    logger.info("%s of '%s' completed", phase, name)
                    results.append(GroupResult(name, True, "ok"))
            except httpx.HTTPError as exc:
                logger.error("%s of '%s' FAILED: %s", phase, name, exc)
                results.append(GroupResult(name, False, str(exc)))
        return results

    def failover(self, groups: list[str] | None = None) -> list[GroupResult]:
        """FAILOVER: promote the secondary array to primary (Read/Write).

        NOTE: performs NO health check on the original primary. The operator
        must ensure the primary site is failed/inaccessible first.
        """
        return self._run_action("failover", groups)

    def recover(self, groups: list[str] | None = None) -> list[GroupResult]:
        """RECOVER: reverse-replicate from the secondary back to the original
        primary (overwrites the old source with current secondary data)."""
        return self._run_action("recover", groups)

    def restore(self, groups: list[str] | None = None) -> list[GroupResult]:
        """RESTORE: return replication to its original direction (primary
        becomes source/RW, secondary becomes target/Read-Only)."""
        return self._run_action("restore", groups)

    def stop(self, groups: list[str] | None = None) -> list[GroupResult]:
        """STOP the replication link (required before a planned failover on a
        group that is still actively replicating)."""
        return self._run_action("stop", groups)

    def start(self, groups: list[str] | None = None) -> list[GroupResult]:
        """START (resume) the replication link."""
        return self._run_action("start", groups)

    # ------------------------------------------------------------------ #
    # Host presentation (VLUNs)
    # ------------------------------------------------------------------ #
    def present_group_volumes(
        self, host: str, groups: list[str] | None = None
    ) -> list[GroupResult]:
        """Export the volumes of the selected Remote Copy groups to a host at
        the secondary site (creates VLUNs with an auto-assigned LUN)."""
        selected = self._select(groups)
        by_name = {g["name"]: g for g in self.list_groups() if g.get("name")}
        results: list[GroupResult] = []
        for name in selected:
            for vol in by_name.get(name, {}).get("volumes", []):
                vol_name = vol.get("localVolumeName") or vol.get("name")
                if not vol_name:
                    continue
                if self.dry_run:
                    logger.info("[DRY-RUN] would present volume '%s' to host '%s'", vol_name, host)
                    results.append(GroupResult(vol_name, True, "dry-run"))
                    continue
                try:
                    resp = self._client.post(
                        "/api/v1/vluns",
                        json={"volumeName": vol_name, "hostname": host, "autoLun": True},
                    )
                    if resp.status_code >= 400:
                        detail = self._error_detail(resp)
                        logger.warning("Present '%s' -> '%s': HTTP %s %s",
                                       vol_name, host, resp.status_code, detail)
                        results.append(GroupResult(vol_name, False, f"HTTP {resp.status_code}: {detail}"))
                    else:
                        logger.info("Presented volume '%s' to host '%s'", vol_name, host)
                        results.append(GroupResult(vol_name, True, "presented"))
                except httpx.HTTPError as exc:
                    logger.warning("Present '%s' -> '%s': %s", vol_name, host, exc)
                    results.append(GroupResult(vol_name, False, str(exc)))
        return results
