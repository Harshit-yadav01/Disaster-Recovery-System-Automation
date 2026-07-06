# HPE DR Automation — Backend

FastAPI backend that authenticates dashboard users and serves live disaster
recovery data. It talks to one or two **HPE Alletra Storage MP** arrays (a
Primary and an optional Recovery) over the **WSAPI** (HTTPS, port 443), and
falls back to **simulated data** so the whole stack runs with zero hardware.

## Architecture

```
Frontend (Dashboard-DR)  ──HTTP──▶  FastAPI (backend/app)
   login.html / index.html            /api/auth/login  → JWT
   api.js / script.js                 /api/dashboard   → live data
                                          │
                                          ▼
                                   StorageProvider
                                   ├─ SimulatedProvider   (demo data)
                                   └─ AlletraProvider  ──▶ HPE Alletra MP WSAPI
                                                           (Primary + Recovery)
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

| Method | Path                 | Auth   | Description                     |
| ------ | -------------------- | ------ | ------------------------------- |
| POST   | `/api/auth/login`    | no     | Returns a JWT bearer token      |
| GET    | `/api/auth/me`       | bearer | Current user (token validation) |
| GET    | `/api/dashboard`     | bearer | Full dashboard payload          |
| GET    | `/api/health`        | no     | Service + provider status       |

## Layout

```
backend/
  app/
    main.py                 FastAPI app, CORS, serves frontend
    config.py               Settings from .env
    security.py             JWT + credential check
    schemas.py              Pydantic API contract
    routers/
      auth.py               /api/auth/*
      dashboard.py          /api/dashboard
    services/
      dashboard_service.py  provider selection + safe fallback
    providers/
      __init__.py           StorageProvider interface
      simulated.py          demo data
      alletra.py            real HPE Alletra MP WSAPI client (2 arrays)
  _conn_test.py             read-only array connectivity checker
  requirements.txt
  .env.example
```
