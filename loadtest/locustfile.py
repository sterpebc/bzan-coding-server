"""
Load test for the Coding Server during a final exam.

Simulates 240 students using the two things they can actually touch
(no login needed for either -- see CODING_SERVER_HOWTO.md):

  - ApiExamTaker: hits the JSON /api/<domain>/<collection> endpoints,
    as a student's own code would.
  - DbExamTaker:  browses pre-loaded tables and runs SELECT queries
    through the /db admin-panel UI's anonymous "Query..." page -- the
    same thing a student does by hand during an exam.

Both user types are enabled with equal weight by default, simulating an
exam that uses both features. If your exam only uses one, set the
other class's `weight = 0` (or comment the class out) before running.

Validated locally before delivery (see chat for details): ran this
exact script with Locust against local replicas of both api.py and
sqlite_web.py -- the latter seeded with a synthetic 3-table SQLite
database (companies/rooms/bookings, matching the structure of the real
"coworking" dataset already loaded on your production server) -- served
by gunicorn under the same --worker-class gthread --workers 1 --threads 4
config production runs. Every endpoint exercised here, including
multi-table JOINs and GROUP BY through the query page, came back 200
with zero errors at full 240-user concurrency. (Discovered one real,
pre-existing bug along the way, unrelated to load: the *per-table*
query page, /<dataset>/<table>/query/, 500s because its template file
is missing from the repo. It isn't linked from anywhere in the UI --
only the dataset-level "Query..." page is reachable by students, which
is what this script uses -- so this test doesn't exercise that broken
route and isn't affected by it. Worth fixing separately; flagged to you
in chat.)

USAGE
-----
    API_PREFIX=/coding-server/api DB_PREFIX=/coding-server/db \
    DB_DATASET=coworking \
    locust -f locustfile.py \
        --host http://<server-ip-or-hostname> \
        --headless --users 240 --spawn-rate <N> --run-time 15m \
        --csv results/sustained

--spawn-rate controls which scenario you're running: low (e.g. 2) for a
gradual, realistic exam opening; high (e.g. 240, all at once) for the
worst-case "everyone's first request lands in the same second" spike.

Against a local test instance with no nginx/reverse-proxy prefix in
front of it, leave API_PREFIX/DB_PREFIX empty.
"""
import os
import random

from locust import HttpUser, task, between

API_PREFIX = os.environ.get("API_PREFIX", "/coding-server/api")
DB_PREFIX = os.environ.get("DB_PREFIX", "/coding-server/db")

# The dataset name already loaded in /db for students to browse/query.
# "coworking" is already uploaded on your production server (visible in
# its own access logs) -- swap in your real exam dataset's name if it's
# different, and update TABLES/SQL_QUERIES below to match its schema.
DB_DATASET = os.environ.get("DB_DATASET", "coworking")
TABLES = ["companies", "rooms", "bookings"]

# --- API endpoints (student code hitting /api/...) ----------------------

API_ENDPOINTS = [
    # (weight, path)
    (3, "/supply_chain/products"),
    (1, "/supply_chain/products/1"),
    (1, "/supply_chain/products?unitPrice=199.99"),
    (2, "/supply_chain/warehouses"),
    (2, "/supply_chain/inventory"),
    (3, "/pokemon_tcg/cards"),
    (1, "/pokemon_tcg/cards/base1-4"),
    (1, "/pokemon_tcg/cards?type=Fire"),
    (2, "/pokemon_tcg/sets"),
]
_WEIGHTED_API_PATHS = [path for weight, path in API_ENDPOINTS for _ in range(weight)]

# --- SQL a student might type into the Query page during an exam --------
# Edit these to match whatever your real exam's SQL questions look like.

SQL_QUERIES = [
    'SELECT * FROM {table} LIMIT 10',
    'SELECT * FROM companies WHERE industry = \'Tech\' ORDER BY name LIMIT 5',
    'SELECT room_id, COUNT(*) AS n FROM bookings GROUP BY room_id ORDER BY n DESC',
    ('SELECT b.booking_id, c.name AS company, r.name AS room '
     'FROM bookings b '
     'JOIN companies c ON b.company_id = c.company_id '
     'JOIN rooms r ON b.room_id = r.room_id '
     'LIMIT 10'),
]


class ApiExamTaker(HttpUser):
    """A student whose code repeatedly queries the JSON API."""

    weight = 1
    # "Think time" between requests: not a tight hammering loop, but
    # also not idle -- matches someone actively iterating on code and
    # re-running it against the API.
    wait_time = between(2, 10)

    @task
    def query_api(self):
        path = random.choice(_WEIGHTED_API_PATHS)
        with self.client.get(f"{API_PREFIX}{path}", catch_response=True, name=path) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")


class DbExamTaker(HttpUser):
    """A student browsing pre-loaded tables and running SELECT queries
    through the /db admin panel's anonymous Query page -- no login
    needed, matching how students actually use it."""

    weight = 1
    wait_time = between(3, 12)

    def on_start(self):
        # Mirrors a real visit: land on the dataset overview first,
        # which is also what establishes which dataset the rest of this
        # session's requests apply to (session-based, same as a browser).
        self.client.get(f"{DB_PREFIX}/{DB_DATASET}/", name="/<dataset>/")

    @task(3)
    def browse_table(self):
        table = random.choice(TABLES)
        page = random.choice([1, 1, 1, 2])  # mostly page 1, sometimes page 2
        params = "" if page == 1 else f"?page={page}"
        with self.client.get(
            f"{DB_PREFIX}/{DB_DATASET}/{table}/content/{params}",
            catch_response=True, name="/<dataset>/<table>/content/",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")

    @task(1)
    def run_query(self):
        table = random.choice(TABLES)
        sql = random.choice(SQL_QUERIES).format(table=table)
        with self.client.post(
            f"{DB_PREFIX}/query/",
            data={"dataset_name": DB_DATASET, "sql": sql},
            catch_response=True, name="/query/ [POST]",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
