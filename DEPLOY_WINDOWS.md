# Windows Server Deployment

## 1. Copy project

Copy the project folder to the server, for example:

```powershell
C:\apps\booking_cars
```

## 2. Create virtual environment

```powershell
cd C:\apps\booking_cars
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Configure environment variables

Copy `.env.example` and set system or service environment variables.

Minimum required:

```powershell
$env:BOOKING_CARS_SECRET_KEY="replace-with-a-long-random-secret"
$env:BOOKING_CARS_ADMIN_PASSWORD="replace-admin-password"
$env:BOOKING_CARS_VIEWER_PASSWORD="replace-viewer-password"
$env:BOOKING_CARS_HOST="127.0.0.1"
$env:BOOKING_CARS_PORT="8000"
```

Notes:

- `BOOKING_CARS_ADMIN_PASSWORD` and `BOOKING_CARS_VIEWER_PASSWORD` affect only the first user seed when the `users` table is empty.
- If the app is behind IIS, nginx or another reverse proxy, set `BOOKING_CARS_PROXY_FIX=true`.
- If HTTPS is terminated before the app and cookies must be secure, set `BOOKING_CARS_SESSION_SECURE=true`.

## 4. Run application

```powershell
cd C:\apps\booking_cars
.venv\Scripts\python.exe serve_waitress.py
```

Default endpoint:

```text
http://127.0.0.1:8000
```

## 5. Reverse proxy options

Recommended production layout on Windows Server:

- `Waitress` on `127.0.0.1:8000`
- `IIS + ARR` or `nginx` in front
- HTTPS termination in the reverse proxy

## 6. Run as a service

Practical Windows options:

1. `NSSM`
2. Windows Task Scheduler with `At startup`
3. IIS reverse proxy + background process manager

Example with NSSM:

```powershell
nssm install booking_cars
```

Use these values:

- Application: `C:\apps\booking_cars\.venv\Scripts\python.exe`
- Arguments: `serve_waitress.py`
- Startup directory: `C:\apps\booking_cars`

Then set environment variables in NSSM or at machine level.

## 7. First login

Default seeded users:

- `admin`
- `viewer`

Passwords come from:

- `BOOKING_CARS_ADMIN_PASSWORD`
- `BOOKING_CARS_VIEWER_PASSWORD`

If users were already created earlier, change passwords manually in the existing database.

## 8. Backups

Back up these files:

- `database.db`
- `cars_seed.json`

## 9. Update process

```powershell
cd C:\apps\booking_cars
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m py_compile app.py database.py serve_waitress.py
```

Restart the service after update.
