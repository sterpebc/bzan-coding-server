Deploying the Coding Server on the department dev server
==========================================================

This app no longer depends on GCP: it uses SQLite for its own state
(instead of Firestore) and local disk for uploaded database files
(instead of Cloud Storage). It runs with plain Python + gunicorn.
Docker is optional -- everything below works without it.

One-time setup
---------------

1. Clone the repo onto the dev server and `cd` into it.

2. Create the persistent data directory. Put it under `/data`, outside
   the app's own directory, so `git pull` and redeploys never touch it:

   ```bash
   mkdir -p /data/apidata/uploads
   ```

   If you don't have write access to `/data`, ask IT to create
   `/data/apidata` and give your account ownership of it -- this is a
   one-time favor, not an ongoing dependency.

3. Copy `.env` into place and fill it in (it already defaults
   `STATE_DB_PATH` and `DB_UPLOAD_DIR` to `/data/apidata/...` -- adjust
   if IT gave you a different path). Generate a real secret key:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   # paste the output into FLASK_SECRET_KEY="..." in .env
   ```

4. Create the virtualenv and install dependencies:

   ```bash
   python3 -m venv .venv
   ./.venv/bin/pip install --no-cache-dir "setuptools<81" -r requirements.txt
   ```

5. Create your first admin user (needed to log into the `/db` sqlite-web
   browser):

   ```bash
   set -a; . ./.env; set +a
   ./.venv/bin/python create_admin.py <your-username>
   ```

6. (Optional) Seed any "API domains" sample data students will query
   under `/api/...`:

   ```bash
   ./.venv/bin/python seed_api_data.py pokemon_tcg pokemon_tcg_data.json
   ./.venv/bin/python seed_api_data.py supply_chain supply_chain_data.json
   ```

Running it
-----------

Pick **one** of these, based on what access you have on the dev server.
All three run the same command in the end
(`gunicorn --bind 0.0.0.0:8080 --access-logfile - --error-logfile - app:application`);
they differ only in how the process survives logout/reboot.

Note the `--access-logfile`/`--error-logfile` flags: gunicorn's per-request
access log is OFF by default (unlike Flask's dev server, which logs every
request automatically), so these are required if you want to see request
activity in your logs at all. `-` means "write to stdout/stderr," which
each option below captures into a log file or the journal for you.

**Option 1 -- system-level systemd (best, needs sudo once)**

See `deploy/coding-server.service`. Ask IT to install it if you don't
have sudo yourself; after that, restarting after a deploy is a single
command and you get real logs via `journalctl`.

**Option 2 -- user-level systemd (no sudo, but needs "lingering" enabled)**

See `deploy/coding-server-user.service`. Works without sudo for the
service itself, but by default stops when you log out unless an admin
runs `loginctl enable-linger <you>` once. Ask IT for that if you want
this option to behave like a real background service.

**Option 3 -- no-sudo fallback (cron + nohup)**

If neither of the above is available, use `scripts/restart_local.sh` to
start the app as a background process, and add it to your user crontab
so it comes back after a reboot with zero admin involvement:

```bash
crontab -e
# add this line:
@reboot /path/to/CodingServer/scripts/restart_local.sh
```

Run `./scripts/restart_local.sh` by hand any time you redeploy.

Redeploying after code changes
--------------------------------

```bash
./deploy.sh
# then restart with whichever option you set up above
```

What changed from the original GCP version
----------------------------------------------

- Firestore -> a local SQLite database (`STATE_DB_PATH`, default
  `/data/apidata/app_state.db`), via the rewritten `datastore.py`.
- Cloud Storage -> local disk (`DB_UPLOAD_DIR`, default
  `/data/apidata/uploads`), using a fallback path that was already
  built into `sqlite_web.py`.
- The hardcoded sqlite-web session secret key was replaced with
  `FLASK_SECRET_KEY` (env var, or an auto-generated persisted key).
- `env.yaml`, the Google Cloud SDK install in the Dockerfile, and the
  `gcloud run deploy` command in `deploy.sh` were removed -- none of
  them apply outside Cloud Run.
- Nothing here needs `GOOGLE_APPLICATION_CREDENTIALS` or a service
  account key anymore; you can delete `.secrets/` once you've confirmed
  the app runs.

If you still want Docker
--------------------------

The trimmed `Dockerfile` and `entrypoint.sh` still work standalone:

```bash
docker build --target prod -t codingserver:prod .
docker run -p 8080:8080 --env-file .env -v /data/apidata:/data/apidata codingserver:prod
```

See `README_DOCKER.md` for more detail.
