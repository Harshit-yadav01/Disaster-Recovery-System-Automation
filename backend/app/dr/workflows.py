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


# --------------------------------------------------------------------------- #
# Failover / Failback (chunk 2)
#
# WHERE each command runs is decided by the CONFIGURED roles (primary array vs
# recovery array), which is robust across the -Rev states. The exact group name
# and -t target are DISCOVERED per array from showrcopy. Verification checks the
# role transitions on the relevant array(s).
#
#   FAILOVER : stop on primary  ->  setrcopygroup failover -f -t <t> <dr_group>
#   FAILBACK : (all on the DR array that took over)
#              setrcopygroup recover -f -t <t> <dr_group>
#              syncrcopy <dr_group>  (wait Synced)
#              setrcopygroup restore -f -t <t> <dr_group>  (wait natural roles)
# --------------------------------------------------------------------------- #
def _primary_host(settings: Settings) -> str:
    return settings.alletra_primary_base_url or settings.alletra_base_url


def _recovery_host(settings: Settings) -> str:
    return settings.alletra_recovery_base_url


def _gather_groups(settings: Settings, base_group: str) -> dict[str, RcopyGroup | None]:
    """Return {clean_host: group_or_None} for every configured array."""
    out: dict[str, RcopyGroup | None] = {}
    for role_label, host in _configured_arrays(settings):
        key = SSHConfig.clean_host(host)
        try:
            with ArraySSH(_ssh_cfg(settings, host, role_label)) as arr:
                text = arr.run("showrcopy")
            out[key] = parse_showrcopy(text).find_group(base_group)
        except SSHError as exc:
            logger.warning("gather: %s (%s) SSH error: %s", role_label, host, exc)
            out[key] = None
    return out


def _exec_on(settings: Settings, host: str, command: str) -> tuple[bool, str]:
    """Run a single command on one array. Returns (ok, output_or_error)."""
    try:
        with ArraySSH(_ssh_cfg(settings, host, "target")) as arr:
            out = arr.run(command)
        return True, out.strip()
    except SSHError as exc:
        return False, str(exc)


def _poll(
    settings: Settings,
    base_group: str,
    predicate,
    timeout: int,
    poll_interval: int,
) -> tuple[bool, dict[str, RcopyGroup | None]]:
    """Poll both arrays' state until ``predicate(groups)`` holds or timeout."""
    deadline = time.time() + timeout
    while True:
        groups = _gather_groups(settings, base_group)
        if predicate(groups):
            return True, groups
        if time.time() >= deadline:
            return False, groups
        time.sleep(poll_interval)


def _g_detail(groups: dict[str, RcopyGroup | None], host: str) -> str:
    g = groups.get(SSHConfig.clean_host(host))
    if not g:
        return f"{SSHConfig.clean_host(host)}: group absent/unreachable"
    return (
        f"{SSHConfig.clean_host(host)}: role={g.role}, status={g.status}, "
        f"synced={g.all_synced()}"
    )


def failover(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 180,
    poll_interval: int = 5,
) -> list[StepResult]:
    """Planned failover: stop the primary group, then promote the DR group.

    NOTE: performs NO health check on the primary. The operator must ensure the
    primary site is failed/inaccessible (or, for a planned test, accept the stop).
    """
    results: list[StepResult] = []
    p_host = _primary_host(settings)
    d_host = _recovery_host(settings)
    if not p_host or not d_host:
        raise DrError("Both primary and recovery arrays must be configured for failover.")

    clean_p = SSHConfig.clean_host(p_host)
    clean_d = SSHConfig.clean_host(d_host)

    groups = _gather_groups(settings, base_group)
    p = groups.get(clean_p)
    d = groups.get(clean_d)
    if not p or not d:
        raise DrError(
            f"Group '{base_group}' not found on both arrays "
            f"(primary={p.role if p else 'absent'}, dr={d.role if d else 'absent'})."
        )
    if not (p.is_primary and d.is_secondary):
        raise DrError(
            "Failover precondition not met: expected primary=Primary, dr=Secondary; "
            f"saw primary={p.role}, dr={d.role}. Aborting to avoid an unsafe change."
        )

    stop_cmd = f"stoprcopygroup -f {p.name}"
    failover_cmd = f"setrcopygroup failover -f -t {d.target} {d.name}"

    results.append(
        StepResult(
            name="plan",
            command="",
            ok=True,
            detail=(
                f"stop primary {clean_p} '{p.name}'; then failover DR {clean_d} "
                f"'{d.name}' (-t {d.target})"
            ),
        )
    )

    if dry_run:
        results.append(StepResult("stop primary", stop_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("failover DR", failover_cmd, True, "DRY-RUN: not executed"))
        return results

    # 1) Stop the primary group.
    ok, out = _exec_on(settings, p_host, stop_cmd)
    results.append(StepResult("stop primary", stop_cmd, ok, out if ok else f"SSH error: {out}"))
    if not ok:
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_p)) and gs[clean_p].is_stopped,
        timeout, poll_interval,
    )
    results.append(StepResult("verify stop", "showrcopy", ok, _g_detail(groups, p_host)))
    if not ok:
        results.append(StepResult("abort", "", False, "primary did not reach Stopped; not failing over"))
        return results

    # 2) Fail over on the DR array.
    ok, out = _exec_on(settings, d_host, failover_cmd)
    results.append(StepResult("failover DR", failover_cmd, ok, out if ok else f"SSH error: {out}"))
    if not ok:
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and gs[clean_d].is_primary,
        timeout, poll_interval,
    )
    results.append(StepResult("verify failover", "showrcopy", ok, _g_detail(groups, d_host)))
    return results


def failback(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 300,
    poll_interval: int = 5,
) -> list[StepResult]:
    """Failback (Option 2): recover -> sync -> restore, back to natural direction.

    All commands run on the DR array that took over during failover.
    """
    results: list[StepResult] = []
    p_host = _primary_host(settings)
    d_host = _recovery_host(settings)
    if not p_host or not d_host:
        raise DrError("Both primary and recovery arrays must be configured for failback.")

    clean_p = SSHConfig.clean_host(p_host)
    clean_d = SSHConfig.clean_host(d_host)

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")
    if not d.is_primary:
        raise DrError(
            "Failback precondition not met: the DR array is not holding the group as "
            f"primary (role={d.role}). Run a failover first. Aborting."
        )

    recover_cmd = f"setrcopygroup recover -f -t {d.target} {d.name}"
    sync_cmd = f"syncrcopy {d.name}"
    restore_cmd = f"setrcopygroup restore -f -t {d.target} {d.name}"

    results.append(
        StepResult(
            name="plan",
            command="",
            ok=True,
            detail=(
                f"on DR {clean_d} '{d.name}' (-t {d.target}): "
                f"recover -> sync -> restore; verify primary back on {clean_p}"
            ),
        )
    )

    if dry_run:
        results.append(StepResult("recover", recover_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("sync", sync_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("restore", restore_cmd, True, "DRY-RUN: not executed"))
        return results

    # 1) Recover: reverse replication (original primary becomes secondary).
    ok, out = _exec_on(settings, d_host, recover_cmd)
    results.append(StepResult("recover", recover_cmd, ok, out if ok else f"SSH error: {out}"))
    if not ok:
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_p)) and gs[clean_p].is_secondary,
        timeout, poll_interval,
    )
    results.append(StepResult("verify recover", "showrcopy", ok, _g_detail(groups, p_host)))
    if not ok:
        return results

    # 2) Sync DR -> original primary and wait until Synced.
    ok, out = _exec_on(settings, d_host, sync_cmd)
    results.append(StepResult("sync", sync_cmd, ok, out if ok else f"SSH error: {out}"))
    if not ok:
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and gs[clean_d].all_synced(),
        timeout, poll_interval,
    )
    results.append(StepResult("verify sync", "showrcopy", ok, _g_detail(groups, d_host)))
    if not ok:
        return results

    # 3) Restore: return to natural direction (primary R/W, DR read-only).
    ok, out = _exec_on(settings, d_host, restore_cmd)
    results.append(StepResult("restore", restore_cmd, ok, out if ok else f"SSH error: {out}"))
    if not ok:
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: (
            bool(gs.get(clean_p)) and gs[clean_p].is_primary
            and bool(gs.get(clean_d)) and gs[clean_d].is_secondary
        ),
        timeout, poll_interval,
    )
    detail = f"{_g_detail(groups, p_host)} | {_g_detail(groups, d_host)}"
    results.append(StepResult("verify restore", "showrcopy", ok, detail))
    return results
