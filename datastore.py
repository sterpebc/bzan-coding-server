"""
Manages shared application state using a local SQLite database.

This module replaces the app's original Google Firestore / Cloud Storage
backend as part of porting it off GCP. It intentionally preserves the same
public interface (a module-level `datastore` singleton with the same method
names/signatures, and a module-level `cloud_storage` name) so that the rest
of the application -- app.py, api.py, sqlite_web.py, create_admin.py, and
seed_api_data.py -- did not need to be rewritten, only lightly touched.

Cloud Storage support has been removed entirely rather than replaced with a
local-disk shim: sqlite_web.py already has a built-in fallback to local disk
for uploaded database files whenever `cloud_storage` is falsy (see the
DB_UPLOAD_DIR setting in app.py), so `cloud_storage` is simply left as None
here and that existing fallback path takes over.
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

# Cloud Storage support has been removed as part of porting this app away
# from GCP. sqlite_web.py falls back to storing uploaded database files on
# local disk (via the DB_UPLOAD_DIR config value) whenever this is falsy.
cloud_storage = None


def _coerce_numeric(value):
    """Coerces a string query-filter value to int or float when possible,
    otherwise returns it unchanged. Tries int before float so '1' stays an
    int (matching a doc's integer id) rather than becoming 1.0."""
    for caster in (int, float):
        try:
            return caster(value)
        except (TypeError, ValueError):
            continue
    return value

# Where the app's own state (config, users, the dataset registry, and the
# generic "API domains" documents served by api.py) lives. A single SQLite
# file, separate from any of the student-facing .db files the app browses.
DEFAULT_STATE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'app_state.db'
)


class LocalDatastore:
    """A state manager backed by a local SQLite database.

    Drop-in replacement for the old Firestore-backed `FirestoreDatastore`:
    every public method here matches the name, arguments, and return shape
    of its Firestore predecessor.
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or os.environ.get('STATE_DB_PATH', DEFAULT_STATE_DB_PATH)
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        # gunicorn workers are separate processes (each gets its own
        # LocalDatastore/connection), but a single worker thread pool could
        # still share this instance, so keep connections thread-local.
        self._local = threading.local()
        self._init_schema()

    @property
    def _conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA foreign_keys=ON')
            self._local.conn = conn
        return conn

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS datasets (
                    name TEXT PRIMARY KEY,
                    path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    config_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_by TEXT,
                    date_created TEXT
                );

                CREATE TABLE IF NOT EXISTS api_documents (
                    domain TEXT NOT NULL,
                    collection_name TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (domain, collection_name, doc_id)
                );

                CREATE INDEX IF NOT EXISTS idx_api_documents_lookup
                    ON api_documents (domain, collection_name);
            ''')
            conn.commit()
        finally:
            conn.close()

    # --- Dataset registry ---------------------------------------------

    def get_all_datasets(self):
        """Retrieves all dataset configurations."""
        cur = self._conn.execute('SELECT name, path FROM datasets')
        return {row['name']: row['path'] for row in cur.fetchall()}

    def add_dataset(self, name, path):
        """Adds or updates a dataset configuration."""
        with self._conn:
            self._conn.execute(
                'INSERT INTO datasets (name, path) VALUES (?, ?) '
                'ON CONFLICT(name) DO UPDATE SET path=excluded.path',
                (name, path)
            )

    def remove_dataset(self, name):
        """Removes a dataset configuration."""
        with self._conn:
            self._conn.execute('DELETE FROM datasets WHERE name = ?', (name,))

    # --- App configuration ----------------------------------------------

    def get_config(self):
        """Retrieves the application configuration document."""
        cur = self._conn.execute('SELECT config_json FROM app_config WHERE id = 1')
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row['config_json'])

    def save_config(self, config_dict):
        """Saves the application configuration."""
        with self._conn:
            self._conn.execute(
                'INSERT INTO app_config (id, config_json) VALUES (1, ?) '
                'ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json',
                (json.dumps(config_dict),)
            )

    # --- Users -------------------------------------------------------------

    def get_user(self, username):
        """Retrieves a user record."""
        cur = self._conn.execute(
            'SELECT username, password_hash, created_by, date_created '
            'FROM users WHERE username = ?', (username,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_all_users(self):
        """Retrieves all users."""
        cur = self._conn.execute(
            'SELECT username, password_hash, created_by, date_created FROM users '
            'ORDER BY username'
        )
        return [dict(row) for row in cur.fetchall()]

    def add_user(self, username, password_hash, created_by):
        """Adds a new user."""
        with self._conn:
            self._conn.execute(
                'INSERT INTO users (username, password_hash, created_by, date_created) '
                'VALUES (?, ?, ?, ?)',
                (username, password_hash, created_by, datetime.now(timezone.utc).isoformat())
            )

    def update_user_password(self, username, new_password_hash):
        """Updates an existing user's password hash."""
        with self._conn:
            self._conn.execute(
                'UPDATE users SET password_hash = ? WHERE username = ?',
                (new_password_hash, username)
            )

    def delete_user(self, username):
        """Deletes a user."""
        with self._conn:
            self._conn.execute('DELETE FROM users WHERE username = ?', (username,))

    # --- Generic "API domains" document store -------------------------

    def get_api_document(self, domain, collection_name, document_id):
        """Retrieves a single document from a generic API collection."""
        cur = self._conn.execute(
            'SELECT data_json FROM api_documents '
            'WHERE domain = ? AND collection_name = ? AND doc_id = ?',
            (domain, collection_name, document_id)
        )
        row = cur.fetchone()
        return json.loads(row['data_json']) if row else None

    def query_api_collection(self, domain, collection_name, **filters):
        """
        Queries a generic API collection with optional key-value filters.

        Filtering happens in Python after loading the matching rows, which
        mirrors the simple equality-filter semantics the old Firestore
        version supported: numeric-looking filter strings are coerced to
        int or float, e.g. so both `?id=1` (int field) and
        `?unitPrice=199.99` (float field, per api.py's own docstring
        example) match correctly.
        """
        cur = self._conn.execute(
            'SELECT data_json FROM api_documents WHERE domain = ? AND collection_name = ?',
            (domain, collection_name)
        )
        results = [json.loads(row['data_json']) for row in cur.fetchall()]

        if not filters:
            return results

        coerced_filters = {field: _coerce_numeric(value) for field, value in filters.items()}

        return [
            doc for doc in results
            if all(doc.get(field) == value for field, value in coerced_filters.items())
        ]

    # --- Seeding helpers (used by seed_api_data.py) ------------------------

    def collection_is_empty(self, domain, collection_name):
        """Returns True if the given API collection has no documents yet."""
        cur = self._conn.execute(
            'SELECT 1 FROM api_documents WHERE domain = ? AND collection_name = ? LIMIT 1',
            (domain, collection_name)
        )
        return cur.fetchone() is None

    def bulk_add_api_documents(self, domain, collection_name, docs, id_field='id'):
        """
        Bulk-inserts documents into a generic API collection.

        `docs` is a list of dicts. If `id_field` is set and present in a
        document, its (stringified) value is used as the document ID,
        matching the old Firestore-based seeding behavior. Otherwise a
        random ID is generated, mirroring Firestore's auto-ID documents
        (used for collections like 'inventory').
        """
        rows = []
        for item in docs:
            if id_field and id_field in item:
                doc_id = str(item[id_field])
            else:
                doc_id = uuid.uuid4().hex
            rows.append((domain, collection_name, doc_id, json.dumps(item)))

        with self._conn:
            self._conn.executemany(
                'INSERT OR REPLACE INTO api_documents '
                '(domain, collection_name, doc_id, data_json) VALUES (?, ?, ?, ?)',
                rows
            )
        return len(rows)


# Singleton instance used by the rest of the application, mirroring the
# shape of the old module (`datastore.datastore`, `datastore.cloud_storage`).
datastore = LocalDatastore()
