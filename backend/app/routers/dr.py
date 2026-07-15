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
    current_user: str = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """List current VLUN exports on an array (read-only `showvlun -a`), optionally
    scoped to volumes matching `vv_pattern`. Also returns the primary LUN map so
    the UI can preview how DR volumes would be matched."""
    try:
        exports = list_exports(settings, which, vv_pattern)
    except DrError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"showvlun failed: {exc}")
    lun_map: dict = {}
    if which == "recovery":
        try:
            lun_map = primary_lun_map(settings)
        except Exception:  # noqa: BLE001 - primary may be down; not fatal
            lun_map = {}
    return {
        "which": which,
        "exports": [dataclasses.asdict(v) for v in exports],
        "primary_lun_map": lun_map,
    }
