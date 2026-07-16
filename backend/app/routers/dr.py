"""DR control API: live Remote Copy status + one-click failover/failback.

All endpoints require a valid JWT (same auth as the dashboard). State-changing
actions run as background jobs; the frontend polls ``/api/dr/jobs/{id}`` for
live step-by-step progress. Read/verify still uses SSH-CLI; the dashboard's
existing WSAPI read path is untouched.

Blocking SSH work runs in sync ``def`` endpoints so FastAPI executes them in a
threadpool rather than blocking the event loop.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..dr import jobs
from ..dr.ssh_client import SSHConfig
from ..dr.workflows import (
    DEFAULT_GROUP,
    DrError,
    gather_status,
    list_exports,
    list_hosts,
    primary_lun_map,
)
from ..security import get_current_user

router = APIRouter(prefix="/api/dr", tags=["dr"])


class OpRequest(BaseModel):
    dry_run: bool = True
    group: str = DEFAULT_GROUP


class PresentRequest(OpRequest):
    # Optional per-run host override (host name or 'set:<hostset>'); falls back
    # to DR_HOST_TARGET in .env when omitted.
    host: str | None = None


def _serialize_status(views) -> list[dict]:
    out: list[dict] = []
    for v in views:
        g = v.group
        out.append(
            {
                "role_label": v.role_label,
                "host": SSHConfig.clean_host(v.host),
                "system_status": v.status.system_status,
                "group": None
                if not g
                else {
                    "name": g.name,
                    "target": g.target,
                    "role": g.role,
                    "status": g.status,
                    "mode": g.mode,
                    "all_synced": g.all_synced(),
                    "is_primary": g.is_primary,
                    "is_secondary": g.is_secondary,
                    "is_reversed": g.is_reversed,
                    "volumes": [
                        {
                            "local_vv": vol.local_vv,
                            "remote_vv": vol.remote_vv,
                            "sync_status": vol.sync_status,
                        }
                        for vol in g.volumes
                    ],
                },
            }
        )
    return out


@router.get("/status")
def dr_status(
    group: str = DEFAULT_GROUP,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Live Remote Copy status for the group on both arrays (read-only SSH)."""
    try:
        views = gather_status(settings, group)
    except DrError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    active = jobs.active_job()
    return {
        "group": group,
        "arrays": _serialize_status(views),
        "active_job_id": active.id if active else None,
    }


def _start(kind: str, payload: OpRequest, settings: Settings) -> dict:
    try:
        job = jobs.start_job(settings, kind, payload.group, payload.dry_run)
    except jobs.JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DrError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job.id, "state": job.state, "kind": job.kind, "dry_run": job.dry_run}


@router.post("/failover")
def dr_failover(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _start("failover", payload, settings)


@router.post("/failback")
def dr_failback(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _start("failback", payload, settings)


@router.post("/recover")
def dr_recover(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Reverse Sync: recover + sync on the DR array, wait until Synced."""
    return _start("recover", payload, settings)


@router.post("/restore")
def dr_restore(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Restore: return the group to its natural direction, wait for Primary."""
    return _start("restore", payload, settings)


@router.post("/revert")
def dr_revert(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Revert Failover: reverse -local -current on the DR array, discarding DR writes."""
    return _start("revert", payload, settings)


def _start_with_params(kind: str, payload: "PresentRequest", settings: Settings) -> dict:
    try:
        job = jobs.start_job(settings, kind, payload.group, payload.dry_run,
                             {"host": payload.host})
    except jobs.JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DrError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job.id, "state": job.state, "kind": job.kind, "dry_run": job.dry_run}


@router.post("/present")
def dr_present(
    payload: PresentRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Present-to-host: export the failed-over group's DR volumes to the DR host."""
    return _start_with_params("present", payload, settings)


@router.post("/unpresent")
def dr_unpresent(
    payload: PresentRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Remove the DR exports of the group's volumes (reverse of present)."""
    return _start_with_params("unpresent", payload, settings)


@router.post("/start")
def dr_start(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _start("start", payload, settings)


@router.post("/stop")
def dr_stop(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _start("stop", payload, settings)


@router.post("/sync")
def dr_sync(
    payload: OpRequest,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _start("sync", payload, settings)


@router.get("/jobs")
def dr_jobs(limit: int = 10, current_user: str = Depends(get_current_user)) -> dict:
    """Recent DR jobs (newest first) for the operation-history view."""
    return {
        "jobs": [
            {
                "id": j.id,
                "kind": j.kind,
                "state": j.state,
                "dry_run": j.dry_run,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
            }
            for j in jobs.recent_jobs(limit)
        ]
    }


@router.get("/jobs/{job_id}")
def dr_job(job_id: str, current_user: str = Depends(get_current_user)) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


def _parse_array_time(text: str) -> datetime | None:
    """Best-effort parse of a Remote Copy ``last_sync_time`` into UTC.

    Handles ISO strings and the common 3PAR/Alletra ``YYYY-MM-DD HH:MM:SS``
    layout (optionally with a trailing timezone token, which is ignored).
    Returns None when it cannot be parsed, so RPO simply reads as unknown.
    """
    if not text:
        return None
    raw = text.strip()
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    core = " ".join(raw.split(" ")[:2])  # drop a trailing tz token if present
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(core, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@router.get("/metrics")
def dr_metrics(
    group: str = DEFAULT_GROUP,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Recovery objectives for the Reports view (read-only):

    * **RTO** - *measured* from real (execute) failover/failback job durations.
    * **RPO** - derived from live replication mode + sync state / last-sync time.
      Synchronous + fully synced == 0s (zero data loss); periodic estimates the
      time since the last successful sync.
    """
    # ---- RTO: measured from job history ---------------------------------
    recovery_kinds = {"failover", "failback"}
    durations: list[float] = []
    last_failover: float | None = None
    for j in jobs.recent_jobs(100):
        if (j.kind in recovery_kinds and j.state == "succeeded" and not j.dry_run
                and j.created_at and j.finished_at):
            try:
                secs = (datetime.fromisoformat(j.finished_at)
                        - datetime.fromisoformat(j.created_at)).total_seconds()
            except ValueError:
                continue
            if secs < 0:
                continue
            durations.append(secs)
            if j.kind == "failover" and last_failover is None:
                last_failover = secs
    rto_target = settings.rto_target_seconds or 0
    rto_value = last_failover if last_failover is not None else (
        durations[0] if durations else None)
    rto = {
        "seconds": round(rto_value) if rto_value is not None else None,
        "avg_seconds": round(sum(durations) / len(durations)) if durations else None,
        "samples": len(durations),
        "target_seconds": rto_target or None,
        "met": bool(rto_value is not None and rto_target and rto_value <= rto_target)
        if rto_target else None,
        "basis": ("last successful failover" if last_failover is not None
                  else "last successful recovery op" if durations
                  else "no recovery operations recorded yet"),
    }

    # ---- RPO: derived from live replication ------------------------------
    rpo: dict = {
        "seconds": None, "mode": None, "synced": None, "last_sync_time": None,
        "target_seconds": settings.rpo_target_seconds or None, "met": None,
        "detail": "replication state unavailable",
    }
    try:
        views = gather_status(settings, group)
    except DrError:
        views = []
    src = next((v.group for v in views if v.group and v.group.is_primary), None)
    if src is None:
        src = next((v.group for v in views if v.group), None)
    if src is not None:
        mode = (src.mode or "").strip()
        synced = src.all_synced()
        last_sync = ""
        for vol in src.volumes:
            t = (vol.last_sync_time or "").strip()
            if t and t.upper() not in ("-", "NA", "N/A") and t > last_sync:
                last_sync = t
        is_sync = mode.lower().startswith("sync")
        rpo_seconds: int | None
        if is_sync and synced:
            rpo_seconds = 0
            detail = "Synchronous replication, all volumes synced - zero data loss."
        elif is_sync:
            rpo_seconds = None
            detail = "Synchronous but not all volumes synced - RPO at risk."
        else:
            rpo_seconds = None
            detail = f"{mode or 'Periodic'} replication."
            parsed = _parse_array_time(last_sync)
            if parsed is not None:
                rpo_seconds = max(
                    0, round((datetime.now(timezone.utc) - parsed).total_seconds()))
                detail += f" ~{rpo_seconds}s since last sync."
        rpo_target = settings.rpo_target_seconds or 0
        rpo = {
            "seconds": rpo_seconds,
            "mode": mode or "unknown",
            "synced": synced,
            "last_sync_time": last_sync or None,
            "target_seconds": rpo_target or None,
            "met": bool(rpo_seconds is not None and rpo_target and rpo_seconds <= rpo_target)
            if rpo_target else None,
            "detail": detail,
        }

    return {"group": group, "rto": rto, "rpo": rpo}


# --------------------------------------------------------------------------- #
# Present-to-host: read-only discovery
# --------------------------------------------------------------------------- #
@router.get("/hosts")
def dr_hosts(
    which: str = "recovery",
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """List hosts on the recovery (default) or primary array, for picking a
    present-to-host target. Read-only (`showhost`)."""
    try:
        hosts = list_hosts(settings, which)
    except DrError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface SSH/parse issues cleanly
        raise HTTPException(status_code=502, detail=f"showhost failed: {exc}")
    return {
        "which": which,
        "hosts": [dataclasses.asdict(h) for h in hosts],
        "configured_target": settings.dr_host_target,
    }


@router.get("/exports")
def dr_exports(
    which: str = "recovery",
    vv_pattern: str | None = None,
    templates: bool = False,
    host: str | None = None,
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """List current VLUN exports on an array (read-only), optionally scoped to
    volumes matching `vv_pattern`. With `templates=true` it reads VLUN templates
    (`showvlun -t`) instead of active VLUNs (`showvlun -a`); `host` filters the
    result to one host/hostset. Also returns the primary LUN map so the UI can
    preview how DR volumes would be matched."""
    try:
        exports = list_exports(settings, which, vv_pattern, templates=templates)
    except DrError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"showvlun failed: {exc}")
    if host:
        h = host.strip().lower()
        exports = [v for v in exports if (v.host_name or "").strip().lower() == h]
    lun_map: dict = {}
    if which == "recovery":
        try:
            lun_map = primary_lun_map(settings)
        except Exception:  # noqa: BLE001 - primary may be down; not fatal
            lun_map = {}
    return {
        "which": which,
        "host": host,
        "templates": templates,
        "exports": [dataclasses.asdict(v) for v in exports],
        "primary_lun_map": lun_map,
    }
