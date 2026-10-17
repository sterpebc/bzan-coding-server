# Load-testing the Coding Server for a 240-student final exam

## Bonus finding, unrelated to load

While validating this script against the real `sqlite_web.py` code,
I found a pre-existing bug, nothing to do with performance: visiting
`/db/<dataset>/<table>/query/` (the *per-table* query page) 500s,
because its template file (`table_query.html`) doesn't exist anywhere
in the repo. It's harmless in practice today -- no page links to it
anywhere, so nobody stumbles into it by clicking around -- but
`CODING_SERVER_HOWTO.md` documents that exact URL pattern, so a student
or colleague who tries it directly (or you, demoing it) will hit a 500.
This load test deliberately avoids that route (it uses the dataset-level
`/db/query/` page instead, which is the one actually reachable from the
UI and works fine), so it won't skew your results. Worth a quick fix
separately -- just say the word and I'll patch it.

## Why run the load generator from a separate machine

Running the load generator on your own laptop, or on the app server
itself, both contaminate the results: your laptop's home internet
connection can't cleanly open hundreds of truly concurrent connections,
and running it *on* the server means the load generator and the app are
fighting each other for the same CPU, which makes the app look slower
than it would be under real traffic from 240 separate machines. A small,
short-lived VM in the **same Vultr region** as your existing server gets
you proper datacenter bandwidth/concurrency without taking any capacity
away from the app itself. It costs a few cents and takes about 5 minutes
to create and destroy.

## 1. Find your existing server's region

In the Vultr customer portal, click into your existing CodingServer/
GameServer instance and note its **Location** (e.g. "New Jersey" or
whatever region it's actually in) -- you'll pick the same one below.

## 2. Spin up a temporary instance

In the Vultr portal: **Deploy New Server** -> Cloud Compute -> same
region as step 1 -> Ubuntu 24.04 (or whatever default LTS is offered) ->
the cheapest plan (1 vCPU / 1GB RAM is plenty for running Locust -- it's
the thing *generating* load, not serving it). Deploy it, note its IP
address once it's ready.

## 3. SSH in and set up Locust

```bash
ssh root@<temp-instance-ip>

apt update && apt install -y python3-pip
pip install --break-system-packages locust
```

(The `--break-system-packages` flag is needed because recent Ubuntu/Debian mark
the system Python as "externally managed" (PEP 668) and refuse a plain `pip
install` by default -- it's there to stop you from accidentally clobbering an
apt-managed Python package. It's a safe override here, since this instance is
disposable and gets destroyed right after the test anyway.)

**Alternative: a virtual environment**, if you'd rather not override that
protection at all -- e.g. you end up reusing this instance for something
longer-lived, where it's worth keeping the habit:

```bash
ssh root@<temp-instance-ip>

apt update && apt install -y python3-venv
python3 -m venv ~/locust-env
source ~/locust-env/bin/activate
pip install locust
```

If you go this route, remember the venv doesn't stay activated across SSH
reconnects -- run `source ~/locust-env/bin/activate` again before `locust` in
the steps below, in any new session.

## 4. Get the test script onto the instance

Easiest: paste the contents of `locustfile.py` (delivered alongside this
file) into a new file on the instance:

```bash
nano locustfile.py
# paste, save, exit
```

Or, if you'd rather not hand-copy it, `scp` it from your Mac:

```bash
scp locustfile.py root@<temp-instance-ip>:~/
```

## 5. Decide which part(s) of the app your exam actually uses

The script models two independent student behaviors, as separate Locust
user classes, each with `weight = 1` (an equal 50/50 mix) by default:

- `ApiExamTaker` -- queries the JSON `/api/<domain>/<collection>`
  endpoints, as a student's own code would.
- `DbExamTaker` -- browses pre-loaded tables and runs SELECT queries
  through the `/db` admin panel's anonymous "Query..." page, exactly as
  a student does by hand (no login needed for either -- see
  `CODING_SERVER_HOWTO.md`).

If this particular exam only uses one or the other, open `locustfile.py`
and set the unused class's `weight = 0` before running (e.g. `weight = 0`
on `ApiExamTaker` for an exam that's pure SQL questions). Leave both at
`1` for an exam that uses both.

`DbExamTaker` defaults to the `coworking` dataset already loaded on your
server (visible in its own access logs) with tables `companies`,
`rooms`, `bookings`. If the real exam uses a different dataset/schema,
update `DB_DATASET` and the `TABLES`/`SQL_QUERIES` list near the top of
the file to match -- the SQL strings there are just examples standing in
for "whatever your students will actually type."

## 6. Run the sustained-load scenario

Models the bulk of a real exam: students trickle in and interact with
the app intermittently (every few seconds, as if actively writing/
running their own code or composing a query) over the whole exam
window. This run ramps up over 2 minutes, then holds 240 simulated
students for 15 minutes:

```bash
API_PREFIX=/coding-server/api DB_PREFIX=/coding-server/db DB_DATASET=coworking \
locust -f locustfile.py \
    --host http://155.138.216.76 \
    --headless --users 240 --spawn-rate 2 --run-time 15m \
    --csv results-sustained
```

(`--spawn-rate 2` means 2 new simulated students start per second, so
all 240 are ramped up after 2 minutes -- adjust to taste.)

## 7. Run the exam-start-spike scenario

Models the worst case: everyone's first request lands in the same
few seconds (e.g. the exam just opened and everyone immediately runs
their starter code or submits their first query). Same script, just a
much higher spawn rate and a shorter hold:

```bash
API_PREFIX=/coding-server/api DB_PREFIX=/coding-server/db DB_DATASET=coworking \
locust -f locustfile.py \
    --host http://155.138.216.76 \
    --headless --users 240 --spawn-rate 240 --run-time 2m \
    --csv results-spike
```

## 8. Read the results

Locust prints a summary table at the end, broken down by endpoint (also
written to `results-*_stats.csv`), including the `/db` routes
(`/<dataset>/`, `/<dataset>/<table>/content/`, `/query/ [POST]`)
alongside the `/api/...` ones if both classes are enabled. The numbers
that actually matter:

- **Failure count / failures/s** -- should be 0, or very close to it.
  Any real failures mean requests are erroring out or timing out, not
  just running slow.
- **95th/99th percentile response time** -- the "95% of students saw
  this or better" number. For simple SQLite reads/queries like these,
  anything under ~500ms-1s at full load is healthy; seconds-long tails
  mean requests are queuing up waiting for a free gthread worker thread.
  Expect `/db` routes to run a bit heavier than `/api` ones at the same
  load -- rendering an HTML table and executing arbitrary SQL is more
  work than a tiny JSON read -- so compare each route to its own
  baseline rather than expecting identical numbers across the board.
- **Requests/s** -- sanity-check this against what 240 students
  realistically generate: at roughly one request every few seconds per
  student (this script's `wait_time`), aggregate load is on the order
  of tens of requests per second, split across however many of the two
  behaviors your exam actually uses. If the server handles that
  comfortably (it almost certainly will), you're in good shape.

## 9. Before and after: a couple of practical notes

- **Run this off-hours**, not during a class or when colleagues might
  be using the server -- a sustained 240-"student" test will use real
  CPU on the production box and could make it sluggish for anyone else
  on it at the time.
- **fail2ban won't interfere.** The `nginx-404-probe` jail we set up
  only matches requests for paths that don't exist at all (scanner
  traffic); real `/api/...` and `/db/...` requests, even thousands of
  them, never touch that filter.
- **Destroy the temporary Vultr instance when you're done** (Vultr
  portal -> the instance -> Destroy) so it stops costing anything.

## 10. If the results show trouble

If failure rates climb or response times balloon under load, the first
thing to try is more `gthread` threads
(`--worker-class gthread --workers 1 --threads 8` or higher) in
`deploy/coding-server.service` / `coding-server-user.service` and
`scripts/restart_local.sh`.

Beyond that, it's also safe to add more **worker processes**
(`--workers 2` etc.) specifically for exam days, even though we kept
`--workers 1` for everyday use -- *as long as you don't add or remove
a dataset in `/db` (Load/Unload Database) while the exam is running.*
The reason `--workers 1` was chosen originally is that `sqlite_web.py`
keeps an in-memory `datasets` dict per process, populated once at
startup; multiple worker processes would each load their own copy, and
an admin action that changes the dataset list (upload/unload) would
only update the process that handled that one request, leaving the
others stale. Student-facing read-only traffic -- browsing tables,
running SELECT queries, hitting `/api/...` -- never changes that list,
so it's not affected: every worker process loaded the same dataset list
at startup and nothing is mutating it mid-exam. So the practical rule
is just: finish any dataset loading/unloading before the exam starts,
then scaling worker processes up for that window is safe.
