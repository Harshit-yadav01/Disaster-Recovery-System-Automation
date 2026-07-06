# HPE DR Automation — Backend

FastAPI backend that authenticates dashboard users and serves live disaster
recovery data. It talks to an **HPE Alletra 6000** array over its REST API, and
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
                                   └─ AlletraProvider  ──▶ HPE Alletra REST API
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

## Connecting to a real HPE Alletra array

Set these in `.env`:

```env
STORAGE_PROVIDER=alletra
ALLETRA_BASE_URL=https://<array-mgmt-ip>:5392
ALLETRA_USERNAME=admin
ALLETRA_PASSWORD=<password>
ALLETRA_VERIFY_SSL=false
```

The `AlletraProvider` (`app/providers/alletra.py`) logs in to get a session
token, then reads arrays, pools, volumes and replication partners and maps them
to the dashboard contract. If the array is unreachable, the service logs a
warning and returns simulated data so the UI never breaks.

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
      alletra.py            real HPE Alletra REST client
  requirements.txt
  .env.example
```
