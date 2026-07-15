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
from .showvlun import Host, Vlun, parse_showhost, parse_showvlun
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
    # Structured both-array state after this step, so the dashboard topology can
    # animate the role/direction changes: {"primary": {...}|None, "dr": {...}|None}.
    snapshot: dict | None = None


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
# Present-to-host: read-only discovery (showhost / showvlun)
# --------------------------------------------------------------------------- #
def list_hosts(settings: Settings, which: str = "recovery") -> list[Host]:
    """Return the hosts defined on the primary or recovery (DR) array (read-only)."""
    host = _recovery_host(settings) if which == "recovery" else _primary_host(settings)
    if not host:
        raise DrError(f"No {which} array configured.")
    with ArraySSH(_ssh_cfg(settings, host, which)) as arr:
        text = arr.run("showhost")
    return parse_showhost(text)


def list_exports(
    settings: Settings, which: str = "recovery", vv_pattern: str | None = None,
    active_only: bool = True, templates: bool = False,
) -> list[Vlun]:
    """Return current VLUN exports on the primary or recovery array (read-only).

    ``vv_pattern`` optionally scopes to specific volumes (glob), e.g. the DR
    group's volumes, so we never list unrelated exports.

    Command selection:
      * ``templates=True``  -> ``showvlun -t``  (VLUN *templates* only)
      * ``active_only=True`` -> ``showvlun -a``  (ACTIVE VLUNs only, default)
      * otherwise            -> ``showvlun``     (both active + templates)

    A freshly created ``createvlun`` is a TEMPLATE that only becomes an
    "active" VLUN once the host initiator logs in; when the DR host has no
    active paths (common for a test/PoC host) the export exists ONLY as a
    template, so present/unpresent verification must look at templates.
    """
    host = _recovery_host(settings) if which == "recovery" else _primary_host(settings)
    if not host:
        raise DrError(f"No {which} array configured.")
    if templates:
        base = "showvlun -t"
    elif active_only:
        base = "showvlun -a"
    else:
        base = "showvlun"
    cmd = base + (f" -v {vv_pattern}" if vv_pattern else "")
    with ArraySSH(_ssh_cfg(settings, host, which)) as arr:
        text = arr.run(cmd)
    return parse_showvlun(text)


def primary_lun_map(settings: Settings, base_group: str = DEFAULT_GROUP) -> dict[str, int]:
    """Map each PRIMARY-side volume name -> its LUN, read from the primary array.

    Used to present DR volumes on the same LUN as their primary twin. Only
    reachable when the primary is up (planned failover/test); callers should
    cache the result for use during an unplanned failover.
    """
    out: dict[str, int] = {}
    for v in list_exports(settings, which="primary"):
        if v.lun is not None and v.vv_name and v.vv_name not in out:
            out[v.vv_name] = v.lun
    return out


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
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Run a start/stop/sync operation on ``base_group`` and verify the result.

    Dry-run (default) resolves + prints the exact command without executing it.
    ``sink`` lets a background job observe steps live as they are appended.
    """
    if op not in LINK_OPS:
        raise DrError(f"Unsupported op '{op}'. Expected one of {LINK_OPS}.")

    results: list[StepResult] = sink if sink is not None else []

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
        out_stripped = out.strip()
        if _looks_like_error(out_stripped):
            results.append(StepResult(op, cmd, False, f"command error: {out_stripped}"))
            return results
        results.append(StepResult(op, cmd, True, out_stripped or "command issued"))

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


def _looks_like_error(output: str) -> bool:
    """Detect a CLI error in command output (3PAR/Alletra print 'Error: ...')."""
    low = output.strip().lower()
    return low.startswith("error") or "error:" in low


def _run_critical(
    settings: Settings, host: str, name: str, command: str, results: list[StepResult]
) -> bool:
    """Run a must-succeed command. Appends a StepResult; returns False (and marks
    the step failed) if SSH fails OR the CLI output looks like an error."""
    ok, out = _exec_on(settings, host, command)
    if not ok:
        results.append(StepResult(name, command, False, f"SSH error: {out}"))
        return False
    if _looks_like_error(out):
        results.append(StepResult(name, command, False, f"command error: {out}"))
        return False
    results.append(StepResult(name, command, True, out or "command issued"))
    return True


def _one_snap(g: RcopyGroup | None) -> dict | None:
    if not g:
        return None
    return {
        "name": g.name,
        "role": g.role,
        "status": g.status,
        "synced": g.all_synced(),
        "is_primary": g.is_primary,
        "is_secondary": g.is_secondary,
        "is_reversed": g.is_reversed,
    }


def _snapshot(settings: Settings, groups: dict[str, RcopyGroup | None]) -> dict:
    """Both-array state keyed by configured role, for the dashboard topology."""
    p = groups.get(SSHConfig.clean_host(_primary_host(settings)))
    d = groups.get(SSHConfig.clean_host(_recovery_host(settings)))
    return {
        "primary": {"host": SSHConfig.clean_host(_primary_host(settings)), **(_one_snap(p) or {})}
        if p else {"host": SSHConfig.clean_host(_primary_host(settings))},
        "dr": {"host": SSHConfig.clean_host(_recovery_host(settings)), **(_one_snap(d) or {})}
        if d else {"host": SSHConfig.clean_host(_recovery_host(settings))},
    }


def failover(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 180,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Planned failover: stop the primary group, then promote the DR group.

    NOTE: performs NO health check on the primary. The operator must ensure the
    primary site is failed/inaccessible (or, for a planned test, accept the stop).
    ``sink`` lets a background job observe steps live as they are appended.
    """
    results: list[StepResult] = sink if sink is not None else []
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
    precond_ok = p.is_primary and d.is_secondary
    precond_msg = (
        None if precond_ok
        else f"expected primary=Primary, dr=Secondary; saw primary={p.role}, dr={d.role}"
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
            snapshot=_snapshot(settings, groups),
        )
    )

    if dry_run:
        if precond_msg:
            results.append(StepResult(
                "precondition", "", False,
                f"execution would be BLOCKED: {precond_msg}"))
        results.append(StepResult("stop primary", stop_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("failover DR", failover_cmd, True, "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError(
            "Failover precondition not met: " + precond_msg
            + ". Aborting to avoid an unsafe change."
        )

    # 1) Stop the primary group.
    if not _run_critical(settings, p_host, "stop primary", stop_cmd, results):
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_p)) and gs[clean_p].is_stopped,
        timeout, poll_interval,
    )
    results.append(StepResult("verify stop", "showrcopy", ok, _g_detail(groups, p_host),
                              snapshot=_snapshot(settings, groups)))
    if not ok:
        results.append(StepResult("abort", "", False, "primary did not reach Stopped; not failing over"))
        return results

    # 2) Fail over on the DR array.
    if not _run_critical(settings, d_host, "failover DR", failover_cmd, results):
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and gs[clean_d].is_primary,
        timeout, poll_interval,
    )
    results.append(StepResult("verify failover", "showrcopy", ok, _g_detail(groups, d_host),
                              snapshot=_snapshot(settings, groups)))
    return results


def failback(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 300,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Failback (Option 2): recover -> sync -> restore, back to natural direction.

    All commands run on the DR array that took over during failover.
    ``sink`` lets a background job observe steps live as they are appended.
    """
    results: list[StepResult] = sink if sink is not None else []
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
    precond_ok = d.is_primary
    precond_msg = (
        None if precond_ok
        else f"DR array is not holding the group as primary (role={d.role}); run a failover first"
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
            snapshot=_snapshot(settings, groups),
        )
    )

    if dry_run:
        if precond_msg:
            results.append(StepResult(
                "precondition", "", False,
                f"execution would be BLOCKED: {precond_msg}"))
        results.append(StepResult("recover", recover_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("sync", sync_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("restore", restore_cmd, True, "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError(
            "Failback precondition not met: " + precond_msg + ". Aborting."
        )

    # 1) Recover: reverse replication (original primary becomes secondary).
    if not _run_critical(settings, d_host, "recover", recover_cmd, results):
        return results
    # Wait until the original primary flips to secondary AND the DR group is
    # Started - recover starts+syncs the group, and syncing before it is Started
    # fails with "Group isn't started".
    ok, groups = _poll(
        settings, base_group,
        lambda gs: (
            bool(gs.get(clean_p)) and gs[clean_p].is_secondary
            and bool(gs.get(clean_d)) and gs[clean_d].is_started
        ),
        timeout, poll_interval,
    )
    results.append(StepResult(
        "verify recover", "showrcopy", ok,
        f"{_g_detail(groups, p_host)} | {_g_detail(groups, d_host)}",
        snapshot=_snapshot(settings, groups)))
    if not ok:
        return results

    # 2) Sync DR -> original primary and wait until Synced. The command may warn
    # if recover already synced; the verify gate below is authoritative, so a
    # command warning does not halt the workflow.
    ok, out = _exec_on(settings, d_host, sync_cmd)
    if not ok:
        results.append(StepResult("sync", sync_cmd, False, f"SSH error: {out}"))
        return results
    cmd_warn = _looks_like_error(out)
    results.append(StepResult(
        "sync", sync_cmd, not cmd_warn,
        (f"non-fatal warning (verify is authoritative): {out}" if cmd_warn else (out or "sync issued"))))
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and gs[clean_d].all_synced(),
        timeout, poll_interval,
    )
    results.append(StepResult("verify sync", "showrcopy", ok, _g_detail(groups, d_host),
                              snapshot=_snapshot(settings, groups)))
    if not ok:
        return results

    # 3) Restore: return to natural direction (primary R/W, DR read-only).
    if not _run_critical(settings, d_host, "restore", restore_cmd, results):
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
    results.append(StepResult("verify restore", "showrcopy", ok, detail,
                              snapshot=_snapshot(settings, groups)))
    return results


# --------------------------------------------------------------------------- #
# Failback split into two operator-driven steps for the unified DR Operations
# panel. ``failback`` above still runs the whole recover -> sync -> restore
# sequence in one go (unchanged). ``recover`` and ``restore`` below let the
# operator drive the same phases individually:
#
#   recover : setrcopygroup recover -f -t <t> <dr_group>  (reverse replication)
#             syncrcopy <dr_group>  (wait until Synced)      -> "Reverse Sync"
#   restore : setrcopygroup restore -f -t <t> <dr_group>   (natural direction)
#
# ``restore`` may only run after ``recover`` has left the DR group Primary and
# fully Synced; the precondition below enforces that.
# --------------------------------------------------------------------------- #
def recover(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 300,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Reverse Sync (failback step 1): recover -> sync, until the DR is Synced.

    Reverses replication so the DR array that took over becomes the source and
    copies its changes back to the original Primary, then waits until every
    volume is Synced. Leaves the group reversed + synced, ready for ``restore``.
    All commands run on the DR array. ``sink`` lets a background job observe
    steps live as they are appended.
    """
    results: list[StepResult] = sink if sink is not None else []
    p_host = _primary_host(settings)
    d_host = _recovery_host(settings)
    if not p_host or not d_host:
        raise DrError("Both primary and recovery arrays must be configured for recover.")

    clean_p = SSHConfig.clean_host(p_host)
    clean_d = SSHConfig.clean_host(d_host)

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")
    precond_ok = d.is_primary
    precond_msg = (
        None if precond_ok
        else f"DR array is not holding the group as primary (role={d.role}); run a failover first"
    )

    recover_cmd = f"setrcopygroup recover -f -t {d.target} {d.name}"
    sync_cmd = f"syncrcopy {d.name}"

    results.append(
        StepResult(
            name="plan",
            command="",
            ok=True,
            detail=(
                f"on DR {clean_d} '{d.name}' (-t {d.target}): recover -> sync; "
                f"reverse replication and copy DR changes back to {clean_p}"
            ),
            snapshot=_snapshot(settings, groups),
        )
    )

    if dry_run:
        if precond_msg:
            results.append(StepResult(
                "precondition", "", False,
                f"execution would be BLOCKED: {precond_msg}"))
        results.append(StepResult("recover", recover_cmd, True, "DRY-RUN: not executed"))
        results.append(StepResult("sync", sync_cmd, True, "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError(
            "Recover precondition not met: " + precond_msg + ". Aborting."
        )

    # 1) Recover: reverse replication (original primary becomes secondary).
    if not _run_critical(settings, d_host, "recover", recover_cmd, results):
        return results
    ok, groups = _poll(
        settings, base_group,
        lambda gs: (
            bool(gs.get(clean_p)) and gs[clean_p].is_secondary
            and bool(gs.get(clean_d)) and gs[clean_d].is_started
        ),
        timeout, poll_interval,
    )
    results.append(StepResult(
        "verify recover", "showrcopy", ok,
        f"{_g_detail(groups, p_host)} | {_g_detail(groups, d_host)}",
        snapshot=_snapshot(settings, groups)))
    if not ok:
        return results

    # 2) Sync DR -> original primary and wait until Synced. A command warning is
    # non-fatal; the verify gate below is authoritative.
    ok, out = _exec_on(settings, d_host, sync_cmd)
    if not ok:
        results.append(StepResult("sync", sync_cmd, False, f"SSH error: {out}"))
        return results
    cmd_warn = _looks_like_error(out)
    results.append(StepResult(
        "sync", sync_cmd, not cmd_warn,
        (f"non-fatal warning (verify is authoritative): {out}" if cmd_warn else (out or "sync issued"))))
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and gs[clean_d].all_synced(),
        timeout, poll_interval,
    )
    results.append(StepResult("verify sync", "showrcopy", ok, _g_detail(groups, d_host),
                              snapshot=_snapshot(settings, groups)))
    return results


def restore(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 300,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Restore (failback step 2): return the group to its natural direction.

    Precondition: the reverse sync (``recover``) has completed, so the DR array
    holds the group as Primary and is fully Synced. Runs ``setrcopygroup
    restore`` on the DR array and waits until the original Primary is Primary
    again (R/W) and the DR array is back to Secondary (Read-Only). ``sink`` lets
    a background job observe steps live as they are appended.
    """
    results: list[StepResult] = sink if sink is not None else []
    p_host = _primary_host(settings)
    d_host = _recovery_host(settings)
    if not p_host or not d_host:
        raise DrError("Both primary and recovery arrays must be configured for restore.")

    clean_p = SSHConfig.clean_host(p_host)
    clean_d = SSHConfig.clean_host(d_host)

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")
    precond_ok = d.is_primary and d.all_synced()
    precond_msg = (
        None if precond_ok
        else (
            f"reverse sync not complete (DR role={d.role}, "
            f"synced={d.all_synced()}); run Reverse Sync first"
        )
    )

    restore_cmd = f"setrcopygroup restore -f -t {d.target} {d.name}"

    results.append(
        StepResult(
            name="plan",
            command="",
            ok=True,
            detail=(
                f"on DR {clean_d} '{d.name}' (-t {d.target}): restore; "
                f"return to natural direction (Primary {clean_p} R/W, DR Read-Only)"
            ),
            snapshot=_snapshot(settings, groups),
        )
    )

    if dry_run:
        if precond_msg:
            results.append(StepResult(
                "precondition", "", False,
                f"execution would be BLOCKED: {precond_msg}"))
        results.append(StepResult("restore", restore_cmd, True, "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError(
            "Restore precondition not met: " + precond_msg + ". Aborting."
        )

    # Restore: return to natural direction (primary R/W, DR read-only).
    if not _run_critical(settings, d_host, "restore", restore_cmd, results):
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
    results.append(StepResult("verify restore", "showrcopy", ok, detail,
                              snapshot=_snapshot(settings, groups)))
    return results


# --------------------------------------------------------------------------- #
# Revert failover (discard DR writes) — the ALTERNATIVE to failback/recover.
#
# After a failover the DR array is Primary (R/W). If the DR site's changes are
# NOT to be kept, this reverses the group's direction back to the original
# Primary and DISCARDS any data written to the promoted (DR) volumes since the
# group was stopped at failover:
#
#   setrcopygroup reverse -f -local -current <dr_group>   (on the DR array)
#
#   -local   : act on the DR array without requiring the (possibly down) primary
#   -current : governs which side's data is kept; here discards the DR writes
#   -f       : skip the interactive "y/n" confirmation prompt
#
# This is mutually exclusive with recover/restore (which PRESERVE DR changes).
# --------------------------------------------------------------------------- #
def revert_failover(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    dry_run: bool = True,
    timeout: int = 300,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Revert a failover, DISCARDING DR writes (``setrcopygroup reverse``).

    Runs ``setrcopygroup reverse -f -local -current <dr_group>`` on the DR array
    that took over, reversing the group's direction so the original Primary is
    the source again and discarding any data written to the promoted (DR)
    volumes since the group was stopped. Use this as the ALTERNATIVE to failback
    when the DR site's changes are not to be kept. ``sink`` lets a background job
    observe steps live as they are appended.
    """
    results: list[StepResult] = sink if sink is not None else []
    p_host = _primary_host(settings)
    d_host = _recovery_host(settings)
    if not d_host:
        raise DrError("Recovery (DR) array must be configured for revert failover.")

    clean_p = SSHConfig.clean_host(p_host) if p_host else "primary"
    clean_d = SSHConfig.clean_host(d_host)

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")
    precond_ok = d.is_primary
    precond_msg = (
        None if precond_ok
        else (
            f"DR array is not holding the group as primary (role={d.role}); "
            "a failover must be active to revert"
        )
    )

    reverse_cmd = f"setrcopygroup reverse -f -local -current {d.name}"

    results.append(
        StepResult(
            name="plan",
            command="",
            ok=True,
            detail=(
                f"on DR {clean_d} '{d.name}': reverse -local -current; revert the "
                f"failover and DISCARD DR writes made since the group was stopped "
                f"(original primary {clean_p} becomes source again)"
            ),
            snapshot=_snapshot(settings, groups),
        )
    )

    if dry_run:
        if precond_msg:
            results.append(StepResult(
                "precondition", "", False,
                f"execution would be BLOCKED: {precond_msg}"))
        results.append(StepResult("reverse", reverse_cmd, True, "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError(
            "Revert precondition not met: " + precond_msg + ". Aborting."
        )

    # Reverse locally on the DR array. This starts an array task and reverts the
    # group's direction, discarding the promoted (DR) volumes' post-stop writes.
    if not _run_critical(settings, d_host, "reverse", reverse_cmd, results):
        return results

    # The DR array relinquishes the Primary role as the reversal completes.
    ok, groups = _poll(
        settings, base_group,
        lambda gs: bool(gs.get(clean_d)) and not gs[clean_d].is_primary,
        timeout, poll_interval,
    )
    results.append(StepResult(
        "verify revert", "showrcopy", ok, _g_detail(groups, d_host),
        snapshot=_snapshot(settings, groups)))
    return results


# --------------------------------------------------------------------------- #
# Present-to-host  (after failover: export DR volumes to the DR ESXi host)
#
#   createvlun -f <dr_vv> <LUN|auto> <host_target>     ← per group volume, on DR
#
# LUN: with strategy "match", reuse each volume's primary-side LUN (read from the
# primary array); if that volume isn't exported on the primary (unknown LUN) or
# strategy is "auto", the array auto-assigns. Scoped STRICTLY to the group's own
# volumes - never touches other volumes/exports.
# --------------------------------------------------------------------------- #
def present_to_host(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    host_target: str | None = None,
    dry_run: bool = True,
    timeout: int = 120,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Export the failed-over group's DR volumes to the DR host (present-to-host)."""
    results: list[StepResult] = sink if sink is not None else []
    d_host = _recovery_host(settings)
    if not d_host:
        raise DrError("Recovery (DR) array must be configured for present-to-host.")
    clean_d = SSHConfig.clean_host(d_host)

    target = (host_target or settings.dr_host_target or "").strip()
    if not target:
        raise DrError(
            "No host target set. Configure DR_HOST_TARGET in .env (e.g. a host "
            "name or 'set:<hostset>') or pass one explicitly."
        )

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")

    precond_ok = d.is_primary
    precond_msg = (
        None if precond_ok
        else f"DR array is not holding the group as Primary (role={d.role}); run a failover first"
    )

    # Build per-volume LUN plan.
    strategy = (settings.dr_present_lun or "match").strip().lower()
    lun_map: dict[str, int] = {}
    if strategy == "match":
        try:
            lun_map = primary_lun_map(settings, base_group)
        except Exception as exc:  # noqa: BLE001 - primary may be down; fall back to auto
            logger.warning("present: primary LUN map unavailable (%s); using auto", exc)

    plan: list[tuple[str, str]] = []
    for vol in d.volumes:
        lun = lun_map.get(vol.remote_vv)  # primary-side counterpart's LUN
        plan.append((vol.local_vv, str(lun) if lun is not None else "auto"))

    results.append(StepResult(
        name="plan",
        command="",
        ok=True,
        detail=(
            f"present {len(plan)} volume(s) of '{d.name}' to '{target}' on DR "
            f"{clean_d} (LUN strategy: {strategy})"
        ),
    ))

    if dry_run:
        if precond_msg:
            results.append(StepResult("precondition", "", False,
                                      f"execution would be BLOCKED: {precond_msg}"))
        for vv, lun in plan:
            results.append(StepResult(
                f"export {vv}", f"createvlun -f {vv} {lun} {target}", True,
                "DRY-RUN: not executed"))
        return results

    if not precond_ok:
        raise DrError("Present precondition not met: " + precond_msg + ". Aborting.")

    for vv, lun in plan:
        cmd = f"createvlun -f {vv} {lun} {target}"
        _run_critical(settings, d_host, f"export {vv}", cmd, results)

    # Verify: every group volume now appears exported on the DR array. A fresh
    # createvlun is a TEMPLATE until the host logs in (for a host with no active
    # paths it stays a template), so verify against `showvlun -t`.
    planned = [vv for vv, _ in plan]
    try:
        found = list_exports(settings, "recovery", templates=True)
    except SSHError as exc:
        results.append(StepResult("verify present", "showvlun -t", False, f"SSH error: {exc}"))
        return results
    exported = {v.vv_name for v in found}
    missing = [vv for vv in planned if vv not in exported]
    ok = not missing
    if ok:
        detail = (f"all {len(planned)} group volume(s) exported as VLUN template(s) "
                  f"on '{target}': {planned}")
    else:
        detail = (f"not exported yet: {missing}. planned={planned}. "
                  f"templates currently on DR ({len(exported)}): {sorted(exported)}")
    results.append(StepResult("verify present", "showvlun -t", ok, detail))
    return results


# --------------------------------------------------------------------------- #
# Unpresent-from-host  (reverse of present: remove the DR exports)
#
#   removevlun -f <dr_vv> <LUN> <host_target>          ← per current export, on DR
#   removevlun -dr ...                                 ← native dry-run preview
# --------------------------------------------------------------------------- #
def unpresent_from_host(
    settings: Settings,
    base_group: str = DEFAULT_GROUP,
    *,
    host_target: str | None = None,
    dry_run: bool = True,
    timeout: int = 120,
    poll_interval: int = 5,
    sink: list[StepResult] | None = None,
) -> list[StepResult]:
    """Remove the DR exports of the group's volumes (reverse of present-to-host)."""
    results: list[StepResult] = sink if sink is not None else []
    d_host = _recovery_host(settings)
    if not d_host:
        raise DrError("Recovery (DR) array must be configured.")
    clean_d = SSHConfig.clean_host(d_host)

    target = (host_target or settings.dr_host_target or "").strip()

    groups = _gather_groups(settings, base_group)
    d = groups.get(clean_d)
    if not d:
        raise DrError(f"Group '{base_group}' not found on the DR array {clean_d}.")
    dr_vols = {vol.local_vv for vol in d.volumes}

    # Find the CURRENT exports of the group's volumes on the DR array. Include
    # templates so template-only exports (host with no active paths) are removed.
    try:
        active = list_exports(settings, "recovery", active_only=True)
        tmpl = list_exports(settings, "recovery", templates=True)
    except SSHError as exc:
        raise DrError(f"showvlun failed on DR array: {exc}")
    seen: set[tuple[str, int]] = set()
    exports = []
    for v in [*active, *tmpl]:
        if v.vv_name in dr_vols and v.lun is not None and (v.vv_name, v.lun) not in seen:
            seen.add((v.vv_name, v.lun))
            exports.append(v)

    results.append(StepResult(
        name="plan",
        command="",
        ok=True,
        detail=(
            f"remove {len(exports)} export(s) of '{d.name}' volumes on DR {clean_d}"
            + (f" for target '{target}'" if target else "")
        ),
    ))

    if not exports:
        results.append(StepResult("unpresent", "", True,
                                   "no group exports present - nothing to remove"))
        return results

    if dry_run:
        for v in exports:
            tgt = target or v.host_name
            results.append(StepResult(
                f"unexport {v.vv_name}", f"removevlun -dr -f {v.vv_name} {v.lun} {tgt}",
                True, "DRY-RUN: not executed (removevlun -dr also previews natively)"))
        return results

    for v in exports:
        tgt = target or v.host_name
        cmd = f"removevlun -f {v.vv_name} {v.lun} {tgt}"
        _run_critical(settings, d_host, f"unexport {v.vv_name}", cmd, results)

    # Verify: none of the group's volumes remain exported (active or template).
    try:
        active2 = list_exports(settings, "recovery", active_only=True)
        tmpl2 = list_exports(settings, "recovery", templates=True)
    except SSHError as exc:
        results.append(StepResult("verify unpresent", "showvlun", False, f"SSH error: {exc}"))
        return results
    still = sorted({v.vv_name for v in [*active2, *tmpl2] if v.vv_name in dr_vols})
    ok = not still
    results.append(StepResult(
        "verify unpresent", "showvlun", ok,
        "all group exports removed" if ok else f"still exported: {still}"))
    return results
