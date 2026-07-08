Docker build & run instructions (optional)
===========================================

Docker is optional for this app -- see `DEPLOYMENT.md` for the primary,
no-Docker deployment path on the dev server. This file is for anyone who
does have Docker available and prefers to run it containerized.

This project provides a single `Dockerfile` with `base`, `dev`, and `prod`
stages and an `entrypoint.sh` script that chooses the startup command based
on the `MODE` environment variable.

Build the images
-----------------

- Build a development image (optional):

```bash
docker build --target dev -t codingserver:dev .
```

- Build a production image:

```bash
docker build --target prod -t codingserver:prod .
```

Run locally
-----------

The app needs a persistent directory for its SQLite state and uploaded
database files (see `.env` / `STATE_DB_PATH` / `DB_UPLOAD_DIR`). Mount a
host directory in so that data survives container restarts:

- Run the production image (uses `gunicorn`):

```bash
docker run -p 8080:8080 --env-file .env -v /data/apidata:/data/apidata codingserver:prod
```

- Run the production image but use dev mode (hot start with the dev server):

```bash
docker run -e MODE=dev -p 8080:8080 --env-file .env -v /data/apidata:/data/apidata codingserver:prod
```

- Override the entrypoint to run a one-off command, e.g. creating an admin user:

```bash
docker run --entrypoint python --env-file .env -v /data/apidata:/data/apidata codingserver:prod create_admin.py <username>
```

Notes
-----
- The `entrypoint.sh` will prefer an explicit command if provided (e.g. `docker run image python app.py`).
- `MODE=dev` will run `python app.py` (Flask dev server). Default is production `gunicorn` server.
- For the current `gunicorn==20.1.0` workflow, the image is pinned to a setuptools version that provides `pkg_resources`, which is required by Gunicorn on startup.
