# Coding Server — HOWTO / Quick Reference

A cheat sheet for using the Coding Server (the "coding exercises" app — home page, SQLite database browser, and a JSON API that serves sample data to students). Written for infrequent use: if you've forgotten how any of this works, start here before digging into the repo.

**Base URL:** http://155.138.216.76/coding-server/

It runs on the same Vultr box as the Game Server, reachable through nginx at the /coding-server/ path (no port number needed). All URLs below are relative to that base.

-----

## 1. Is it even running? (30-second smoke test)

    curl -s http://155.138.216.76/coding-server/

If it's up, you'll get back an HTML page whose body includes:

    The application is running successfully!

That's the fastest possible check — no login, no auth, just confirms gunicorn/nginx are alive. If this fails, see DEPLOYMENT.md in the repo for how to restart the service (systemd, user-systemd, or the scripts/restart_local.sh fallback, depending on which option is set up on the box).

A slightly more useful check, since it also proves the API layer and the datastore are working:

    curl -s http://155.138.216.76/coding-server/api/supply_chain/products

This should return a JSON array of sample products (see §3 below). An empty [] means the app is up but that domain hasn't been seeded; a 500 means the datastore itself has a problem.

-----

## 2. The Admin Panel (/db/) — for you and colleagues, not students

Despite the URL, /db/ isn't just a database browser — it's the whole login-protected admin panel: browsing/editing the .db files used in exercises (a customized fork of [sqlite-web](https://github.com/coleifer/sqlite-web), which is where the /db prefix comes from historically), managing your own and colleagues' accounts, *and* seeding the student-facing API data (see "Seeding API data" below) — that last one has nothing to do with SQLite datasets at all, it just happens to live under the same login-gated app, because /db and /api are two genuinely separate Flask apps stitched together in app.py (DispatcherMiddleware), and only the /db one has a login system. Students don't get accounts here.

**Home:** http://155.138.216.76/coding-server/db/

- If no database is loaded yet, you'll be redirected to the **Load** page.
- If exactly one database is loaded, you're taken straight into it.
- If more than one is loaded, you get a picker page.

### Logging in

http://155.138.216.76/coding-server/db/login/ — username + password. There's no self-service signup; accounts are created either from the **Users** page (below, once you're logged in) or from the command line with create_admin.py (§4) if you don't have any account yet.

### Browsing a database

Once a dataset is loaded and selected:

- /db/\<dataset_name\>/ — overview of the tables in that database.
- /db/\<dataset_name\>/\<table\>/ — table structure (columns, indexes, foreign keys, triggers, the CREATE TABLE SQL).
- /db/\<dataset_name\>/\<table\>/content/ — the actual rows, paginated (50/page by default), with inline add/edit/delete for admins.
- /db/query/ — run an arbitrary SQL query against the whole database, not scoped to one table (reachable via the "Query..." link in the page header).
- /db/\<dataset_name\>/\<table\>/export/ — export the table (CSV/JSON, per the on-page options).

### Loading a new .db file

http://155.138.216.76/coding-server/db/load/ — upload a new SQLite file from your machine (or, if filesystem loading is enabled, point at a path on the server). Uploaded files persist under the DB_UPLOAD_DIR configured in .env (currently /root/data/uploads), so they survive restarts and redeploys.

### Seeding API data (no shell access needed)

http://155.138.216.76/coding-server/db/seed-api-data/ — upload a JSON file to populate the student-facing /api/\<domain\>/\<collection\> endpoints (§3), without needing to SSH in and run seed_api_data.py (§4) by hand. Pick or type a domain name (existing ones autocomplete), upload the JSON file, and submit. The page also lists everything currently seeded — domain, collection, document count, and the resulting /api/... URL — which is now the fastest way to answer "what's actually loaded right now" (no more need for the raw sqlite3 query in §3).

By default it skips any collection in the file that already has data (same safety behavior as the CLI script), but there's an **Overwrite** checkbox if you actually want to replace one — unlike the CLI script, checking it does a real replace (deletes the collection's existing rows first), not just an upsert on top of what's there.

(Yes, this page really is about /api/ data, not /db/ datasets — it's here because the login system lives with the admin panel, not the student API. See the note at the top of this section if that seems backwards.)

### Managing users

http://155.138.216.76/coding-server/db/users/ — list existing accounts, add a new one, or delete one. http://.../db/change-password/ lets the logged-in user change their own password. This is the easiest way to give a colleague their own login without touching the server directly.

-----

## 3. The Student JSON API (/api/)

A simple, read-only (GET-only) API that serves whatever sample data has been seeded, organized as domain/collection/document.

### What "domain" actually means

It's just a namespace, not a recognized subject or a fixed set of choices — there's no registry of valid domains anywhere in the app. Under the hood, every seeded document lives in one SQLite table, api_documents, with columns (domain, collection_name, doc_id, data_json), and (domain, collection_name, doc_id) together are the primary key. "Domain" is literally whatever string you type as the first argument to seed_api_data.py \<domain_name\> \<json_file\> (§4) — it becomes the first path segment in the URL, /api/\<domain\>/\<collection\>, and its only real job is to keep same-named collections from different exercises apart. For example, both supply_chain and a hypothetical second exercise could each have their own products collection without colliding, because they'd sit under different domains (supply_chain/products vs. some_other_domain/products).

So when seeding a new exercise's data, supply_chain isn't a magic value the app expects — it's just the domain name whoever set that exercise up happened to choose, matching the JSON file's subject matter for readability. You're free to pick any string for a new domain; just be consistent between the seed_api_data.py invocation and whatever URL you hand out to students (and keep it filesystem/URL-safe — no slashes).

### Seeing what's currently seeded

The **Seed API Data** page (§2, /db/seed-api-data/) lists every domain, collection, and document count currently loaded — check there first.

If you ever need it without logging into the browser UI (e.g. scripting, or the app itself is misbehaving), you can still query the app's own state database directly on the server:

    sqlite3 /root/data/app_state.db \
      "SELECT DISTINCT domain, collection_name FROM api_documents;"

(Adjust the path if STATE_DB_PATH in .env has been changed — see §4.) This is the app's *own* internal state DB, separate from the .db files you browse/upload through /db/ in §2, so it won't show up there.

Nothing about the route is hardcoded to a particular subject — any domain/collection you seed (§2 or §4) becomes queryable automatically.

**List everything in a collection:**

    GET /coding-server/api/<domain>/<collection>

**Get one document by ID:**

    GET /coding-server/api/<domain>/<collection>/<doc_id>

**Filter a list by field value** — append any field as a query parameter; numeric-looking values are matched as numbers, not strings:

    GET /coding-server/api/<domain>/<collection>?<field>=<value>

No authentication is required for /api/ — it's meant to be hit directly by student code.

### Currently seeded sample domains

Based on the fixtures in samples/ and the seeding commands in DEPLOYMENT.md, these two domains are the ones normally seeded for class use (confirm what's actually loaded with a quick curl, since seeding is a manual, one-time step — see §4):

**supply_chain** — collections products, warehouses, inventory.

    curl -s http://155.138.216.76/coding-server/api/supply_chain/products
    curl -s http://155.138.216.76/coding-server/api/supply_chain/products/1
    curl -s "http://155.138.216.76/coding-server/api/supply_chain/products?unitPrice=199.99"
    curl -s http://155.138.216.76/coding-server/api/supply_chain/warehouses
    curl -s http://155.138.216.76/coding-server/api/supply_chain/inventory

**pokemon_tcg** — collections sets, cards, market_values.

    curl -s http://155.138.216.76/coding-server/api/pokemon_tcg/cards
    curl -s http://155.138.216.76/coding-server/api/pokemon_tcg/cards/base1-4
    curl -s "http://155.138.216.76/coding-server/api/pokemon_tcg/cards?type=Fire"
    curl -s http://155.138.216.76/coding-server/api/pokemon_tcg/sets

A 404 on a single-document request means that ID doesn't exist in that collection; an empty list [] on a collection request means the domain/collection hasn't been seeded (or the filter matched nothing).

-----

## 4. What's not exposed over HTTP (needs shell access)

A couple of one-off admin tasks only exist as command-line scripts run on the server itself — there's no web page for them:

**Create your first admin login** (or a new one, though the /db/users/ page is easier once you have one account):

    cd /path/to/CodingServer
    set -a; . ./.env; set +a
    ./.venv/bin/python create_admin.py <username>
    # prompts for a password, then a confirmation

**Reset a password when you're locked out** — the /db/change-password/ page (§2) only works if you can already log in, which doesn't help if you've forgotten your only admin account's password. Same script, with --reset:

    cd /path/to/CodingServer
    set -a; . ./.env; set +a
    ./.venv/bin/python create_admin.py <username> --reset
    # prompts for a new password, then a confirmation

It's one script serving two purposes: without --reset it creates a new user and refuses if the username already exists; with --reset it does the opposite, changing an existing user's password and refuses if the username doesn't exist yet. Either way the password itself is only ever typed at a prompt, never as a command-line argument, so it doesn't end up sitting in your shell history.

**Seed (or re-seed) an API domain** from a JSON file — this is how the supply_chain/pokemon_tcg sample data in §3 gets loaded, and how you'd add a new domain for a different exercise. For a one-off upload, the **Seed API Data** page (§2) is easier — no shell access needed. This script is still the way to go for scripting/automating a seed as part of a larger setup step. Note it *skips* seeding if the collection already has documents, so re-running it is safe but won't overwrite existing data — remove the domain's rows from the datastore first if you actually want to replace it (the web UI's Overwrite checkbox does this for you):

    cd /path/to/CodingServer
    set -a; . ./.env; set +a
    ./.venv/bin/python seed_api_data.py <domain_name> <path_to_json_file>.json

**Don't skip sourcing .env here.** datastore.py reads STATE_DB_PATH from the environment and, if it's unset, silently falls back to a data/app_state.db *inside the repo checkout* — a different file from the /root/data/app_state.db the running app actually uses (systemd's EnvironmentFile= sources .env for the live service, but an ad hoc shell invocation of this script does not, unless you source it yourself). Skip this step and you'll seed a file nobody reads — the seeding will report success, but /api/\<domain\>/\<collection\> will still come back empty, and a second run will claim the collection is "not empty" while the real database stays untouched. If that's already happened to you, check for a stray data/app_state.db in the repo directory and re-seed after sourcing .env properly.

The JSON file's structure is: one top-level key per collection, each mapping to a list of documents (see samples/supply_chain_data.json / samples/pokemon_tcg_data.json in the repo for real examples). Each document's id field becomes its document ID; the one exception is a collection literally named inventory, which gets an auto-generated ID instead (since inventory rows don't have a natural single ID). The web UI (§2) does this more generally rather than hardcoding inventory by name: it uses each document's own id field only when *every* document in that collection has one, and auto-generates IDs otherwise — so a new collection without natural IDs works via the UI without needing a code change, unlike this script.

-----

## 5. Redeploying after a code change

    cd /path/to/CodingServer
    ./deploy.sh          # git pull + pip install
    # then restart the service (systemd / user-systemd / restart_local.sh —
    # whichever is set up; see DEPLOYMENT.md)

Full deployment details (nginx config, systemd unit files, Docker option, one-time server setup) live in DEPLOYMENT.md in the repo — this doc is just the day-to-day "how do I use/check the thing" reference.
