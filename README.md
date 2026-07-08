# Coding Server

A small Flask app used for coding exercises: it serves a sample "hello
world" page, an embedded [sqlite-web](https://github.com/coleifer/sqlite-web)
database browser (mounted at `/db`) with login-protected admin features,
and a simple JSON API (mounted at `/api`) that serves seeded sample data
to students.

Originally built for Google Cloud Platform (Firestore + Cloud Storage +
Cloud Run). It has since been ported to run on plain Python + gunicorn
with a local SQLite backend, with no GCP dependency at all.

## Running it

See `DEPLOYMENT.md` for how to set this up and run it on the department
dev server (with or without Docker), including the one-time steps for
creating an admin user and seeding sample API data.

For local development, after creating a virtualenv and installing
`requirements.txt`:

```bash
set -a; . ./.env; set +a
./.venv/bin/python create_admin.py <your-username>
./.venv/bin/flask --app app --debug run --host 0.0.0.0 --port 8080
```

Then visit `http://localhost:8080/` for the home page, `/db/` for the
database browser, and `/api/<domain>/<collection>` for seeded API data.

## Project layout

- `app.py` -- combines the three sub-apps (home page, sqlite-web, API) into one WSGI app.
- `datastore.py` -- the app's shared state (users, dataset registry, config, API data), backed by a local SQLite database.
- `api.py` -- the student-facing JSON API.
- `sqlite_web/` -- a vendored, customized copy of sqlite-web with login and dataset-registry integration.
- `create_admin.py` / `seed_api_data.py` -- one-off setup scripts.
