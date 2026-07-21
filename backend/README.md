# HPE DR Automation — Backend

FastAPI backend that authenticates dashboard users, serves live disaster
recovery data, and drives one-click DR operations on HPE Alletra Storage MP /
3PAR arrays. It talks to one or two arrays over the **WSAPI** (HTTPS, port 443)
for dashboard data, and over **SSH** (port 22) for all DR automation (status,
failover, failback, present/unpresent, and replication link control). Falls back
to **simulated data** so the whole stack runs with zero hardware.

## Architecture

```
Frontend (Dashboard-DR)  ──HTTP──▶  FastAPI (backend/app)
   login.html / index.html            /api/auth/login  → JWT
   api.js / dr.js / script.js         /api/dashboard   → live data
                                      /api/dr/*        → DR operations (jobs)
                                          │
                              ┌─────────┴─────────┐
                              │ StorageProvider        │ DR workflows (SSH)
                              │ ├─ SimulatedProvider   │ ├─ showrcopy (status)
                              │ └─ AlletraProvider     │ ├─ failover / failback
                              │     (WSAPI, 2 arrays)  │ ├─ recover / restore
                              │                        │ ├─ revert
                              │                        │ └─ present / unpresent
                              └────────────────────────┘
                                   │                │
                              WSAPI (443)        SSH (22)
                         Primary + Recovery   Primary + Recovery
```

## Setup

From the `backend/` folder:

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
Copy-Item .env.example .env
#   edit .env to set SECRET_KEY, dashboard credentials, and (optionally)
#   the real array connection under STORAGE_PROVIDER=alletra

# 4. Run the server (serves the API *and* the frontend)
uvicorn app.main:app --reload --port 8000
```

Then open **http://127.0.0.1:8000/** — you'll be redirected to the login page.

- Default login: `admin` / `admin` (change in `.env`)
- API docs (Swagger): **http://127.0.0.1:8000/docs**
- Health check: **http://127.0.0.1:8000/api/health**

## Connecting to real HPE Alletra Storage MP arrays

Set these in `.env`:

```env
STORAGE_PROVIDER=alletra
ALLETRA_PRIMARY_BASE_URL=<primary-array-mgmt-ip>
ALLETRA_RECOVERY_BASE_URL=<recovery-array-mgmt-ip>   # optional; blank for one array
ALLETRA_USERNAME=<array-username>
ALLETRA_PASSWORD=<password>
ALLETRA_VERIFY_SSL=false
ALLETRA_SSH_PORT=22    # reuses ALLETRA_USERNAME / ALLETRA_PASSWORD for SSH

# Present-to-host: export DR volumes to this host/set after failover
DR_HOST_TARGET=set:DR_Intern_Automation   # or a single host name
DR_PRESENT_LUN=match                      # match primary LUN or auto

# RTO/RPO compliance targets in seconds (0 = disabled)
RTO_TARGET_SECONDS=0
RPO_TARGET_SECONDS=0
```

Provide the management IP or hostname only — the provider adds `https://…:443`
automatically. (For an HTTP-only WSAPI or a non-standard port, pass a full URL,
e.g. `http://<ip>:8080`.)

The arrays use **self-signed certificates**. Two options:

- `ALLETRA_VERIFY_SSL=false` (default) — accepts the self-signed cert without
  verifying it. Fine on a trusted management network.
- `ALLETRA_CA_CERT=/path/to/array-cert.pem` — **more secure**: pins and verifies
  against the array's own certificate. Export the cert from the array (or with
  `openssl s_client -connect <ip>:443 -showcerts`) and point this at the PEM
  file. When set, it takes precedence over `ALLETRA_VERIFY_SSL`.

WSAPI must be enabled on each array. On the array CLI:

```
showwsapi          # -State- should be Enabled/Active, HTTPS_Port 443
startwsapi         # enable it if it is not
```

The `AlletraProvider` (`app/providers/alletra.py`) logs in via
`POST /api/v1/credentials` to obtain a session key, then reads `system`,
`volumes`, `remotecopygroups` and `cpgs` from each array and maps them to the
dashboard contract. Only the **primary** is required: if it is unreachable the
service logs a warning and returns simulated data so the UI never breaks; if
only the **recovery** is unreachable the primary still renders and the recovery
site is marked "Unreachable". The client is strictly read-only for storage data
(the only writes are session login/logout).

### Verify connectivity

Before starting the server you can run the bundled read-only check (prints no
password):

```powershell
.\.venv\Scripts\python.exe _conn_test.py
```

It reports TCP/TLS reachability and whether a WSAPI login succeeds against the
configured primary array.

> Note: ESXi host / VMware VM figures come from vCenter/SRM, not the array. Those
> tiles show array-derived values when using the real provider; wire in a
> vCenter client later to populate them fully.

## API

| Method | Path                      | Auth   | Description                                        |
| ------ | ------------------------- | ------ | -------------------------------------------------- |
| POST   | `/api/auth/login`         | no     | Returns a JWT bearer token                         |
| GET    | `/api/auth/me`            | bearer | Current user (token validation)                    |
| GET    | `/api/dashboard`          | bearer | Full dashboard payload                             |
| GET    | `/api/health`             | no     | Service + provider status                          |
| GET    | `/api/dr/status`          | bearer | Live `showrcopy` state for both arrays             |
| POST   | `/api/dr/failover`        | bearer | Stop on primary, promote DR group (background job) |
| POST   | `/api/dr/failback`        | bearer | Full three-phase failback (background job)         |
| POST   | `/api/dr/recover`         | bearer | Phase 1 of failback: `setrcopygroup recover`       |
| POST   | `/api/dr/restore`         | bearer | Phase 3 of failback: `setrcopygroup restore`       |
| POST   | `/api/dr/revert`          | bearer | Discard DR writes, reverse failover                |
| POST   | `/api/dr/present`         | bearer | Export DR volumes to DR host after failover        |
| POST   | `/api/dr/unpresent`       | bearer | Remove DR volume exports                           |
| POST   | `/api/dr/start`           | bearer | Start replication group (background job)           |
| POST   | `/api/dr/stop`            | bearer | Stop replication group (background job)            |
| POST   | `/api/dr/sync`            | bearer | Manual resync of replication group                 |
| GET    | `/api/dr/jobs`            | bearer | Recent DR jobs (newest first)                      |
| GET    | `/api/dr/jobs/{id}`       | bearer | Live step-by-step progress for a job               |

## Layout

```
backend/
  app/
    main.py                 FastAPI app, CORS, serves frontend
    config.py               Settings from .env (storage, SSH, DR targets, RTO/RPO)
    security.py             JWT + credential check
    schemas.py              Pydantic API contract
    routers/
      auth.py               /api/auth/*
      dashboard.py          /api/dashboard
      dr.py                 /api/dr/* (status, failover, failback, recover, restore,
                                       revert, present, unpresent, start, stop, sync,
                                       jobs, jobs/{id})
    services/
      dashboard_service.py  provider selection + safe fallback
    providers/
      __init__.py           StorageProvider interface
      simulated.py          demo data
      alletra.py            real HPE Alletra MP WSAPI client (2 arrays)
    dr/
      ssh_client.py         Paramiko SSH session + exec/shell mode auto-detection
      workflows.py          DR workflow logic (link ops, failover, failback,
                            recover, restore, revert, present, unpresent)
      jobs.py               In-memory background-job store (single-flight lock)
      showrcopy.py          showrcopy output parser
      showvlun.py           showvlun / showhost output parser
      remote_copy.py        Remote Copy group data model helpers
  _conn_test.py             Read-only array connectivity checker
  identify_arrays.py        Identify Primary/Recovery roles from showrcopy
  dr_ctl.py                 CLI for DR operations (mirrors the API)
  dr_automation.py          CLI automation helper
  dr_status.py              CLI status reporter
  dr_present.py             CLI present/unpresent helper
  requirements.txt
  .env.example
```
