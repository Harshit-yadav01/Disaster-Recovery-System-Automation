"""DR replication workflows (chunk 1: link operations).

Drives the replication-link lifecycle for ONE Remote Copy group over SSH:

    start  - startrcopygroup <group>      (enable replication; sync groups auto-sync)
    stop   - stoprcopygroup -f <group>    (quiesce; -f = non-interactive)
    sync   - syncrcopy <group>            (manual resync)

Safety model:
  * Scoped to a single named group (default ``Intern_Automation``). NEVER acts on
    all groups and never uses glob/-pat - other production groups exist on the
    arrays and must not be touched.
  * The target array is DISCOVERED at runtime as the one currently holding the
    group in a Primary (or Primary-Rev) role - never hardcoded.
  * The exact group name is read from ``showrcopy`` per array (primary side
    ``Intern_Automation`` vs DR side ``Intern_Automation.r188150``).
  * Every state-changing op is verified by polling ``showrcopy`` until the
    expected state is reached (or a timeout).

Failover / recover / restore are added in a later chunk.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..config import Settings
from .showrcopy import RcopyGroup, RcopyStatus, parse_showrcopy
from .ssh_client import ArraySSH, SSHConfig, SSHError

logger = logging.getLogger("dr.workflows")

#: The only group this automation is allowed to act on by default.
DEFAULT_GROUP = "Intern_Automation"

#: Link operations supported in this chunk.
LINK_OPS = ("start", "stop", "sync")


class DrError(RuntimeError):
    """Raised when a workflow cannot proceed (no primary, group missing, etc.)."""


@dataclass
class StepResult:
    """Outcome of a single workflow step (for CLI output and the live dashboard)."""

    name: str
    command: str
    ok: bool
    detail: str
    role: str | None = None
    status: str | None = None
    synced: bool | None = None


@dataclass
class ArrayView:
    """A single array's parsed state plus the target group on it (if present)."""

    role_label: str  # configured label: "primary" | "recovery"
    host: str
    status: RcopyStatus
    group: RcopyGroup | None


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #
def _configured_arrays(settings: Settings) -> list[tuple[str, str]]:
    """Return [(role_label, host)] for the configured Primary and Recovery arrays."""
    primary = settings.alletra_primary_base_url or settings.alletra_base_url
    recovery = settings.alletra_recovery_base_url
    out: list[tuple[str, str]] = []
    if primary:
        out.append(("primary", primary))
    if recovery:
        out.append(("recovery", recovery))
    return out


def _ssh_cfg(settings: Settings, host: str, role_label: str) -> SSHConfig:
    return SSHConfig(
        host=host,
        username=settings.alletra_username,
        password=settings.alletra_password,
        port=settings.alletra_ssh_port,
        timeout=settings.alletra_timeout,
        role=role_label,
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def gather_status(settings: Settings, base_group: str = DEFAULT_GROUP) -> list[ArrayView]:
    """Run ``showrcopy`` on every configured array and return parsed views."""
    views: list[ArrayView] = []
    for role_label, host in _configured_arrays(settings):
        try:
            with ArraySSH(_ssh_cfg(settings, host, role_label)) as arr:
                text = arr.run("showrcopy")
            status = parse_showrcopy(text)
            views.append(ArrayView(role_label, host, status, status.find_group(base_group)))
        except SSHError as exc:
            logger.warning("status: %s (%s) SSH error: %s", role_label, host, exc)
            views.append(ArrayView(role_label, host, RcopyStatus(system_status="unreachable"), None))
    return views


def resolve_primary(
    settings: Settings, base_group: str = DEFAULT_GROUP
) -> tuple[str, str, RcopyGroup]:
    """Find the array where ``base_group`` currently holds a Primary role.

    Returns ``(host, group_name_on_that_array, group)``. Raises :class:`DrError`
    if the group is not Primary on any reachable array.
    """
    views = gather_status(settings, base_group)
    for v in views:
        if v.group and v.group.is_primary:
            return v.host, v.group.name, v.group
    seen = [
        (v.host, v.group.role if v.group else "absent/unreachable") for v in views
    ]
    raise DrError(
        f"No reachable array holds group '{base_group}' as Primary. Seen: {seen}"
    )


# --------------------------------------------------------------------------- #
# Link operations
# --------------------------------------------------------------------------- #
def _command_for(op: str, group_name: str) -> str:
    if op == "stop":
        # -f: run without the interactive confirmation prompt.
        return f"stoprcopygroup -f {group_name}"
    if op == "start":
        return f"startrcopygroup {group_name}"
    if op == "sync":
        return f"syncrcopy {group_name}"
    raise DrError(f"Unknown link operation: {op}")


def _verify_predicate(op: str):
    if op == "stop":
        return lambda g: g.is_stopped
    if op == "start":
        return lambda g: g.is_started and g.all_synced()
    if op == "sync":
        return lambda g: g.all_synced()
    raise DrError(f"Unknown link operation: {op}")


def _snapshot(group: RcopyGroup | None) -> tuple[str | None, str | None, bool | None]:
    if group is None:
        return None, None, None
    return group.role, group.status, group.all_synced()


def run_link_op(
    settings: Settings,
    op: str,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 180,
    poll_interval: int = 5,
) -> list[StepResult]:
    """Run a start/stop/sync operation on ``base_group`` and verify the result.

    Dry-run (default) resolves + prints the exact command without executing it.
    """
    if op not in LINK_OPS:
        raise DrError(f"Unsupported op '{op}'. Expected one of {LINK_OPS}.")

    results: list[StepResult] = []

    host, group_name, group = resolve_primary(settings, base_group)
    role, status, synced = _snapshot(group)
    results.append(
        StepResult(
            name="resolve target",
            command="showrcopy",
            ok=True,
            detail=(
                f"'{base_group}' is Primary on {SSHConfig.clean_host(host)} "
                f"as '{group_name}' (status={status}, synced={synced})"
            ),
            role=role,
            status=status,
            synced=synced,
        )
    )

    cmd = _command_for(op, group_name)

    if dry_run:
        results.append(
            StepResult(
                name=op,
                command=cmd,
                ok=True,
                detail="DRY-RUN: command not executed",
            )
        )
        return results

    with ArraySSH(_ssh_cfg(settings, host, "target")) as arr:
        try:
            out = arr.run(cmd)
        except SSHError as exc:
            results.append(StepResult(op, cmd, False, f"SSH error: {exc}"))
            return results
        results.append(StepResult(op, cmd, True, out.strip() or "command issued"))

        # Verify by polling showrcopy on the same connection.
        predicate = _verify_predicate(op)
        ok = False
        final: RcopyGroup | None = None
        deadline = time.time() + timeout
        while True:
            text = arr.run("showrcopy")
            final = parse_showrcopy(text).find_group(base_group)
            if final and predicate(final):
                ok = True
                break
            if time.time() >= deadline:
                break
            time.sleep(poll_interval)

        role, status, synced = _snapshot(final)
        detail = (
            f"role={role}, status={status}, synced={synced}"
            if final
            else "group not found after operation"
        )
        if not ok:
            detail = f"TIMEOUT after {timeout}s waiting for expected state - " + detail
        results.append(
            StepResult(
                name=f"verify {op}",
                command="showrcopy",
                ok=ok,
                detail=detail,
                role=role,
                status=status,
                synced=synced,
            )
        )

    return results
