# Disaster-Recovery-System-Automation

## Overview

A localhost tool that automates **storage-level disaster recovery (DR)** for
workloads protected by **HPE Alletra Storage MP / 3PAR Remote Copy** replication.
It combines a FastAPI backend, a browser dashboard, and command-line tools to
give one-click **Failover**, **Failback**, and replication link control
(**start / stop / sync**) for a single Remote Copy group — safely, with a
verified, step-by-step workflow.

> Scope note: this project automates the **array replication layer** only.
> VMware SRM VM orchestration, Ansible application recovery, and Terraform/DNS
> redirection are **not** implemented. Virtualization figures shown on the
> dashboard (ESXi hosts, VMs) are derived/simulated, not read from vCenter.

## Architecture

```
Dashboard-DR (static frontend)        backend/app (FastAPI)
  login.html   (entry point)            /api/auth/login   → JWT
  index.html  (Replication /            /api/dashboard    → live/simulated data
   Failover / Failback tabs)            /api/dr/status    → showrcopy (read-only)
  api.js / dr.js / script.js            /api/dr/failover|failback|start|stop|sync
                                        /api/dr/jobs[/{id}] → live job progress
        │                                        │
        └────────────── HTTP ────────────────────┤
                                                  ├─ StorageProvider (dashboard data)
                                                  │    ├─ SimulatedProvider  (demo data)
                                                  │    └─ AlletraProvider ─▶ Alletra WSAPI (HTTPS/443)
                                                  └─ DR workflows ─▶ Alletra/3PAR CLI over SSH
                                                       (showrcopy, startrcopygroup,
                                                        stoprcopygroup, syncrcopy,
                                                        setrcopygroup failover/recover/restore)
```

A single `uvicorn app.main:app` process serves both the REST API and the static
frontend; opening the root redirects to the login page.

## What it does (implemented)

- **JWT authentication** — `/api/auth/login` issues a bearer token; every
  dashboard and DR endpoint requires it. Credentials come from config
  (single user; no Operator/Admin roles).
- **Live dashboard data** — `/api/dashboard` returns metrics via a provider
  abstraction: real **HPE Alletra (WSAPI)** or **Simulated** demo data, with
  automatic fallback to simulated if an array is unreachable.
- **Replication status** — `/api/dr/status` parses `showrcopy` on both arrays
  (read-only) and reports role, sync state, and per-volume detail for the group.
- **One-click DR actions** — all operations run as **background jobs**; the
  frontend polls `/api/dr/jobs/{id}` for live, step-by-step progress, and
  `/api/dr/jobs` lists recent operations:
  - `start` / `stop` / `sync` — replication link control
  - `failover` — stop on primary, promote the DR group
  - `failback` — full three-phase failback (recover → sync → restore)
  - `recover` / `restore` — individual failback phases (granular control)
  - `revert` — discard DR writes and reverse a failover without syncing back
  - `present` / `unpresent` — export (or remove) the failed-over group's DR
    volumes to a DR ESXi host or host set after failover
- **Single-flight job lock** — only one DR job can run at a time; a 409 is
  returned if a second action is attempted while one is already running.
- **Command-line tools** mirroring the API: `dr_ctl.py`, `dr_automation.py`,
  `dr_status.py`, `identify_arrays.py` (plus a `showrcopy` / `showvlun` /
  `showhost` parser and tests).

## Safety model

- **Single-group scope** — only ever acts on one named Remote Copy group
  (default `Intern_Automation`); never uses globs and never touches other
  production groups.
- **Runtime discovery** — the target/primary array is discovered live from
  `showrcopy` (which array currently holds the group as Primary), never hardcoded.
- **Dry-run by default** — API and CLI preview the exact command without
  executing; execution requires an explicit flag (`--execute` / `dry_run: false`)
  and, on the CLI, a typed confirmation.
- **Verified transitions** — every state-changing command is confirmed by
  polling `showrcopy` until the expected state is reached or a timeout occurs.

## Workflows

**Replication link ops** (`start` / `stop` / `sync`)
- Resolve the array holding the group as Primary → run the command there →
  verify the resulting state.

**Failover** (`setrcopygroup failover`)
- Stop the group on the primary, then promote the DR group. Performs **no**
  health check on the primary — the operator must ensure the primary is
  failed/inaccessible (or accept the stop for a planned test).

**Revert Failover** (`setrcopygroup recover -local -current`)
- Discards DR writes and reverses the failover without syncing data back.
  Use to cancel a test failover quickly.

**Failback** (all on the array that took over)
- Full three-phase sequence: `setrcopygroup recover` → `syncrcopy` (wait
  Synced) → `setrcopygroup restore` → wait for natural roles to return.
- The `recover` and `restore` phases can also be triggered individually for
  granular control.

**Present / Unpresent**
- After failover, `present` runs `createvlun` on the DR array to export each
  group volume to the configured DR host or host set (LUN matching the
  primary-side assignment by default). `unpresent` reverses this with
  `removevlun`. Both verify the VLUN template state via `showvlun -t`.

## Setup & run

From the `backend/` folder (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env    # then edit .env (see below)
uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000/** (redirects to login). API docs at `/docs`,
health at `/api/health`.

- Default login: `admin` / `admin` (change `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD` in `.env`).

### Connecting to real arrays

```env
STORAGE_PROVIDER=alletra
ALLETRA_PRIMARY_BASE_URL=<primary-array-mgmt-ip>
ALLETRA_RECOVERY_BASE_URL=<recovery-array-mgmt-ip>   # optional
ALLETRA_USERNAME=<array-username>
ALLETRA_PASSWORD=<password>
ALLETRA_VERIFY_SSL=false
ALLETRA_SSH_PORT=22      # CLI DR automation reuses ALLETRA_USERNAME/PASSWORD

# Present-to-host (export DR volumes after failover)
DR_HOST_TARGET=set:DR_Intern_Automation   # host set or single host name
DR_PRESENT_LUN=match                      # match primary LUN or auto

# RTO/RPO compliance targets in seconds (0 = disabled)
RTO_TARGET_SECONDS=0
RPO_TARGET_SECONDS=0
```

Leave `STORAGE_PROVIDER=simulated` (the default) to run the entire stack with
no hardware.

## Requirements

- Python 3.11+ (`backend/requirements.txt`: FastAPI, Uvicorn, httpx, Paramiko,
  pydantic-settings, python-jose, etc.).
- For live mode: HPE Alletra MP / 3PAR arrays with Remote Copy configured and
  WSAPI + SSH reachable, using the credentials above.
- Secrets provided via environment variables / `.env` (never committed).

## Not implemented / out of scope

VMware SRM VM orchestration, Ansible application recovery, Terraform/DNS or
load-balancer redirection, role-based access (Operator vs Admin), config-drift
detection, and automated RTO/RPO SLA enforcement. The dashboard's Reports tab
displays measured RTO/RPO values and compliance against configured targets
(`RTO_TARGET_SECONDS` / `RPO_TARGET_SECONDS`) but does not generate or export
formal compliance reports. ESXi host and VMware VM figures on the dashboard
are array-derived or simulated, not read from vCenter.
