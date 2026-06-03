__version__ = '0.7.2'

import base64
import hashlib
import importlib
import logging
import operator
import os
import re
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import timedelta
import datetime
import math
from collections import namedtuple, OrderedDict
from functools import reduce
from functools import wraps
from io import StringIO
from io import TextIOWrapper
from werkzeug.routing import BaseConverter
from werkzeug.utils import secure_filename, redirect
from werkzeug.security import check_password_hash

try:
    import datastore
except ImportError:
    datastore = None


try:
    from flask import (
        Flask, abort, flash, g, jsonify, make_response, redirect,
        render_template, request, session, url_for)
except ImportError:
    raise RuntimeError('Unable to import flask module. Install by running '
                       'pip install flask')
try:
    from markupsafe import Markup, escape
except ImportError:
    raise RuntimeError('Unable to import markupsafe module. Install by running'
                       ' pip install markupsafe')

try:
    from pygments import formatters, highlight, lexers
except ImportError:
    import warnings
    warnings.warn('pygments library not found.', ImportWarning)
    syntax_highlight = lambda data: '<pre>%s</pre>' % data
else:
    def syntax_highlight(data):
        if not data:
            return ''
        lexer = lexers.get_lexer_by_name('sql')
        formatter = formatters.HtmlFormatter(linenos=False)
        return highlight(data, lexer, formatter)

try:
    from peewee import __version__ as _pw_version
    peewee_version = tuple([int(p) for p in _pw_version.split('.')])
except ImportError:
    raise RuntimeError('Unable to import peewee module. Install by running '
                       'pip install peewee')
else:
    if peewee_version < (3, 0, 0):
        raise RuntimeError('Peewee >= 3.0.0 is required. Found version %s. '
                           'Please update by running pip install --update '
                           'peewee' % _pw_version)

from peewee import *
from peewee import IndexMetadata
from peewee import sqlite3
from playhouse.dataset import DataSet
from playhouse.migrate import migrate


CUR_DIR = os.path.realpath(os.path.dirname(__file__))
DEBUG = False

BLOB_AS_BASE64 = False  # Default is hex.
ROWS_PER_PAGE = 50
QUERY_ROWS_PER_PAGE = 1000
TRUNCATE_VALUES = True
SECRET_KEY = 'sqlite-database-browser-0.1.0'

app = Flask(
    __name__,
    static_folder=os.path.join(CUR_DIR, 'static'),
    template_folder=os.path.join(CUR_DIR, 'templates'))
app.config.from_object(__name__)
datasets = {}
dataset_config = {}

def die(message):
    sys.stderr.write('%s\n' % message)
    sys.exit(1)

# --- State Management ---
# The `datasets` dictionary holds the in-memory cache of active database
# connections (SqliteDataSet objects). The authoritative list of database
# paths is stored externally in Firestore via the `datastore` module.


#
# Database metadata objects.
#

TriggerMetadata = namedtuple('TriggerMetadata', ('name', 'sql'))

ViewMetadata = namedtuple('ViewMetadata', ('name', 'sql'))

#
# Database helpers.
#

class SqliteDataSet(DataSet):
    @property
    def filename(self):
        db_file = self._database.database
        if db_file.startswith('file:'):
            db_file = db_file[5:]
        return os.path.realpath(db_file.rsplit('?', 1)[0])

    @property
    def basename(self):
        return os.path.basename(self.filename)

    @property
    def is_readonly(self):
        db_file = self._database.database
        return db_file.endswith('?mode=ro')

    @property
    def created(self):
        stat = os.stat(self.filename)
        return datetime.datetime.fromtimestamp(stat.st_ctime)

    @property
    def modified(self):
        stat = os.stat(self.filename)
        return datetime.datetime.fromtimestamp(stat.st_mtime)

    @property
    def size_on_disk(self):
        stat = os.stat(self.filename)
        return stat.st_size

    def get_indexes(self, table):
        return self._database.get_indexes(table)

    def get_all_indexes(self):
        cursor = self.query(
            'SELECT name, sql FROM sqlite_master '
            'WHERE type = ? ORDER BY name',
            ('index',))
        return [IndexMetadata(row[0], row[1], None, None, None)
                for row in cursor.fetchall()]

    def get_columns(self, table):
        return self._database.get_columns(table)

    def get_foreign_keys(self, table):
        return self._database.get_foreign_keys(table)

    def get_triggers(self, table):
        cursor = self.query(
            'SELECT name, sql FROM sqlite_master '
            'WHERE type = ? AND tbl_name = ?',
            ('trigger', table))
        return [TriggerMetadata(*row) for row in cursor.fetchall()]

    def get_all_triggers(self):
        cursor = self.query(
            'SELECT name, sql FROM sqlite_master '
            'WHERE type = ? ORDER BY name',
            ('trigger',))
        return [TriggerMetadata(*row) for row in cursor.fetchall()]

    def get_table_sql(self, table):
        if not table:
            return

        cursor = self.query(
            'SELECT sql FROM sqlite_master '
            'WHERE tbl_name = ? AND type IN (?, ?)',
            [table, 'table', 'view'])
        res = cursor.fetchone()
        if res is not None:
            return res[0]

    def get_view(self, name):
        cursor = self.query(
            'SELECT name, sql FROM sqlite_master '
            'WHERE type = ? AND name = ?', ('view', name))
        res = cursor.fetchone()
        if res is not None:
            return ViewMetadata(*res)

    def get_all_views(self):
        cursor = self.query(
            'SELECT name, sql FROM sqlite_master '
            'WHERE type = ? ORDER BY name',
            ('view',))
        return [ViewMetadata(*row) for row in cursor.fetchall()]

    def get_virtual_tables(self):
        cursor = self.query(
            'SELECT name FROM sqlite_master '
            'WHERE type = ? AND sql LIKE ? '
            'ORDER BY name',
            ('table', 'CREATE VIRTUAL TABLE%'))
        return set([row[0] for row in cursor.fetchall()])

    def get_corollary_virtual_tables(self):
        virtual_tables = self.get_virtual_tables()
        suffixes = ['content', 'docsize', 'segdir', 'segments', 'stat']
        return set(
            '%s_%s' % (virtual_table, suffix) for suffix in suffixes
            for virtual_table in virtual_tables)

    def is_view(self, name):
        cursor = self.query(
            'SELECT name FROM sqlite_master '
            'WHERE type = ? AND name = ?', ('view', name))
        return cursor.fetchone() is not None

    def view_operations(self, name):
        cursor = self.query(
            'SELECT sql FROM sqlite_master WHERE type=? AND tbl_name=?',
            ('trigger', name))
        triggers = [t for t, in cursor.fetchall()]
        rgx = re.compile(r'CREATE\s+TRIGGER.+?\sINSTEAD\s+OF\s+'
                         r'(INSERT|UPDATE|DELETE)\s', re.I)
        operations = set()
        for trigger in triggers:
            operations.update([op.lower() for op in rgx.findall(trigger)])

        return operations

class Base64Converter(BaseConverter):
    def to_python(self, value):
        value = base64.urlsafe_b64decode(value)
        try:
            return value.decode('utf8')
        except UnicodeDecodeError:
            return value

    def to_url(self, value):
        if not isinstance(value, (bytes, str)):
            value = str(value).encode('utf8')
        elif isinstance(value, str):
            value = value.encode('utf8')
        return base64.urlsafe_b64encode(value).decode()

app.url_map.converters['b64'] = Base64Converter

def get_dataset():
    # This function is now a wrapper around g.dataset. The dataset should
    # be set on `g` by the view functions.
    if not hasattr(g, 'dataset'):
        # Fallback for routes that don't have a dataset_name in the URL.
        if datasets:
            dataset_key = session.get('dataset')
            if dataset_key is None or dataset_key not in datasets:
                dataset_key = list(datasets)[0]
                session['dataset'] = dataset_key
            g.dataset = datasets[dataset_key]
    if not hasattr(g, 'dataset'):
        return None  # No datasets loaded.
    return g.dataset

def admin_required(fn):
    """
    Decorator that redirects anonymous users to the login page.
    """
    @wraps(fn)
    def _decorated_view(*args, **kwargs):
        if 'user_id' not in session:
            flash('You must be logged in to access this page.', 'danger')
            # Store the path they were trying to access.
            session['next_url'] = request.url
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return _decorated_view

def require_dataset(fn):
    """
    Decorator to ensure a valid dataset is specified in the URL and loaded
    into the request context `g`.
    """
    @wraps(fn)
    def inner(dataset_name, *args, **kwargs):
        if dataset_name not in datasets:
            abort(404)
        g.dataset = datasets[dataset_name]
        session['dataset'] = dataset_name
        return fn(dataset_name, *args, **kwargs)
    return inner

#
# Flask views.
#

@app.route('/<dataset_name>/')
@app.route('/<dataset_name>/<table>/')
@require_dataset
def dataset_dispatcher(dataset_name, table=None):
    """
    Main dispatcher for dataset-prefixed URLs.
    
    This view handles URLs like /<dataset_name>/ and /<dataset_name>/<table>/.
    It validates the dataset, sets it for the current request context, and then
    either displays a dataset overview or dispatches to a table-specific view.
    """
    if table is None:
        # /<dataset_name>/ -> Show dataset overview.
        return render_template('dataset_overview.html', dataset_name=dataset_name)
    else:
        # /<dataset_name>/<table>/ -> Show table structure.
        if table not in g.dataset.tables:
            abort(404)

        ds_table = g.dataset[table]
        model_class = ds_table.model_class
        return render_template('table_structure.html', columns=g.dataset.get_columns(table), ds_table=ds_table, foreign_keys=g.dataset.get_foreign_keys(table), indexes=g.dataset.get_indexes(table), model_class=model_class, table=table, table_sql=g.dataset.get_table_sql(table), triggers=g.dataset.get_triggers(table))

@app.route('/')
@admin_required
def index():
    # If no datasets are loaded at all, redirect to the upload page.
    if not datasets:
        flash('No databases are loaded. Please upload a database to begin.', 'info')
        return redirect(url_for('load'))

    # If there is only one dataset, redirect to it for convenience.
    if len(datasets) == 1:
        dataset_name = list(datasets.keys())[0]
        return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name))

    # If there are multiple datasets, show a selection page.
    return render_template('dataset_index.html')

@app.route('/users/', methods=['GET', 'POST'])
@admin_required
def users():
    if not datastore or not datastore.datastore:
        flash('User authentication is not configured.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_user':
            return redirect(url_for('add_user'))
        elif action == 'change_password':
            return redirect(url_for('change_password'))

    all_users = datastore.datastore.get_all_users()
    return render_template('users.html', users=all_users)

@app.route('/users/add/', methods=['GET', 'POST'])
@admin_required
def add_user():
    if not datastore or not datastore.datastore:
        flash('User authentication is not configured.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return render_template('add_user.html')

        if datastore.datastore.get_user(username):
            flash('User already exists.', 'danger')
            return render_template('add_user.html', username=username)

        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(password)
        created_by = session.get('user_id')
        datastore.datastore.add_user(username, password_hash, created_by)
        flash(f'User "{username}" created successfully.', 'success')
        return redirect(url_for('users'))

    return render_template('add_user.html')

@app.route('/users/delete/<username>/', methods=['POST'])
@admin_required
def delete_user(username):
    if not datastore or not datastore.datastore:
        flash('User authentication is not configured.', 'danger')
        return redirect(url_for('index'))

    if username == session.get('user_id'):
        flash('You cannot delete yourself.', 'danger')
        return redirect(url_for('users'))

    datastore.datastore.delete_user(username)
    flash(f'User "{username}" deleted successfully.', 'success')
    return redirect(url_for('users'))

@app.route('/change-password/', methods=['GET', 'POST'])
@admin_required
def change_password():
    if not datastore or not datastore.datastore:
        flash('User authentication is not configured.', 'danger')
        return redirect(url_for('index'))

    username = session.get('user_id')
    if not username:
        # This case should not be hit due to @admin_required, but it's good practice.
        flash('Could not identify current user.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not all([current_password, new_password, confirm_password]):
            flash('All password fields are required.', 'danger')
            return render_template('change_password.html')

        user_data = datastore.datastore.get_user(username)

        if not user_data or not check_password_hash(user_data.get('password_hash'), current_password):
            flash('Your current password is not correct.', 'danger')
            return render_template('change_password.html')

        if new_password != confirm_password:
            flash('The new passwords do not match.', 'danger')
            return render_template('change_password.html')

        from werkzeug.security import generate_password_hash
        new_password_hash = generate_password_hash(new_password)
        datastore.datastore.update_user_password(username, new_password_hash)
        flash('Your password has been changed successfully.', 'success')
        return redirect(url_for('users'))

    return render_template('change_password.html')

@app.route('/login/', methods=['GET', 'POST'])
def login():
    # If there's no datastore, we can't authenticate users.
    if not datastore or not datastore.datastore:
        flash('User authentication is not configured.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return render_template('login.html')

        user_data = datastore.datastore.get_user(username)

        if user_data and check_password_hash(user_data.get('password_hash'), password):
            # Login successful. Store user identifier in the session.
            # Make the session permanent so it will expire.
            session.permanent = True
            session['user_id'] = username
            flash('Login successful.', 'success')
            return redirect(session.pop('next_url', url_for('index')))
        else:
            flash('Invalid username or password.', 'danger')
            app.logger.debug('Failed login attempt for user "%s" from %s',
                             username, request.remote_addr)
    return render_template('login.html')

@app.route('/logout/', methods=['GET'])
def logout():
    session.pop('user_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/select-dataset/', methods=['GET'])
def select_dataset():
    dataset = request.args.get('name')
    if dataset and dataset in datasets:
        session['dataset'] = dataset
    else:
        flash('Unable to load selected database.', 'danger')
    return redirect(url_for('index'))

@app.route('/load/', methods=['GET', 'POST'])
@admin_required
def load():
    enable_load = app.config.get('ENABLE_LOAD')
    enable_filesystem = app.config.get('ENABLE_FILESYSTEM')
    if not (enable_load or enable_filesystem):
        flash('Loading databases at run-time is not supported.', 'warning')
        return redirect(url_for('index'))

    dataset = None
    dataset_name = None
    filename = None
    error = None
    if request.method == 'POST':
        filename = request.form.get('filename')
        dataset_name = request.form.get('name')
        try:
            dataset, error = _add_dataset(enable_load, enable_filesystem, dataset_name)
        except ValueError as exc:
            error = str(exc)

        if dataset and not error:
            flash('Successfully loaded database.', 'success')
            return redirect(url_for('index'))

    return render_template(
        'load.html',
        dataset_name=dataset_name,
        filename=filename,
        error=error)

def _add_dataset(enable_load, enable_filesystem, name=None):
    mode = request.form.get('mode')
    path = None
    if mode == 'upload':
        if not enable_load:
            return None, 'Uploading databases is not allowed.'

        database = request.files.get('database')
        if not database:
            return None, 'Database file is required.'

        if datastore and datastore.cloud_storage:
            filename = secure_filename(database.filename) or 'database.db'
            path = datastore.cloud_storage.upload_file(database.stream, filename)
        else:
            # Fallback to temp directory if GCS is not available.
            app.logger.warning('GCS not configured. Uploaded database will be stored '
                               'in a temporary, non-persistent directory.')
            upload_dir = app.config.get('DB_UPLOAD_DIR')
            if upload_dir:
                dirname = app.config['DB_UPLOAD_DIR']
                os.makedirs(dirname, exist_ok=True)
            else:
                dirname = tempfile.mkdtemp(prefix='sqlite-web')
            filename = secure_filename(database.filename) or 'database.db'
            path = os.path.join(dirname, filename)
            database.save(path)

    elif mode == 'filesystem':
        if not enable_filesystem:
            return None, 'Loading databases from the filesystem is not allowed.'
        path = request.form.get('filename')
        if not path:
            return None, 'Filename is required.'
        if not os.path.exists(path):
            return None, 'File "%s" not found.' % path
    else:
        return None, 'Error: unrecognized mode "%s".' % mode

    if name:
        url_name = secure_filename(name).strip()
        if not url_name:
            return None, 'Invalid dataset name specified.'
    else:
        basename = os.path.basename(path)
        url_name = os.path.splitext(secure_filename(basename))[0]

    if not url_name:
        return None, 'Could not generate a valid dataset name from filename.'
    if url_name in datasets:
        return None, 'A dataset with the name "%s" already exists.' % url_name

    try:
        dataset = initialize_dataset(path)
    except Exception as exc:
        return None, 'Unable to load database: %s' % exc
    else:
        datasets[url_name] = dataset
        session['dataset'] = url_name
        if datastore:
            datastore.datastore.add_dataset(url_name, path)

    return dataset, None

@app.route('/unload/', methods=['GET', 'POST'])
@admin_required
def unload():
    enable_load = app.config.get('ENABLE_LOAD')
    enable_filesystem = app.config.get('ENABLE_FILESYSTEM')
    if not (enable_load or enable_filesystem):
        flash('Unloading databases is not supported.', 'danger')
        return redirect(url_for('index'))
    if len(datasets) == 1:
        flash('Cannot unload dataset.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        dataset = request.form.get('dataset')
        if not dataset or dataset not in datasets:
            flash('Database not found.', 'warning')
            return redirect(url_for('unload'))

        ds = datasets.pop(dataset)
        ds.close()

        current = session.get('dataset')
        if current == dataset:
            session['dataset'] = list(datasets)[0]
        if datastore:
            datastore.datastore.remove_dataset(dataset)
        flash('Database "%s" unloaded successfully.' % dataset, 'success')
        return redirect(url_for('index'))
    else:
        dataset = request.args.get('dataset')

    return render_template('unload.html', selected=dataset)

def _query_view(template, table=None):
    dataset = get_dataset()
    data = []
    data_description = error = row_count = sql = None
    ordering = None
    pk_index = None

    sql = qsql = request.values.get('sql') or ''

    if 'export_json' in request.values:
        ordering = request.values.get('export_ordering')
        export_format = 'json'
    elif 'export_csv' in request.values:
        ordering = request.values.get('export_ordering')
        export_format = 'csv'
    else:
        ordering = request.values.get('ordering')
        export_format = None

    if ordering:
        import math
        ordering = int(ordering)
        direction = 'DESC' if ordering < 0 else 'ASC'
        qsql = ('SELECT * FROM (%s) AS _ ORDER BY %d %s' %
                (sql.rstrip(' ;'), abs(ordering), direction))
    else:
        ordering = None

    if table:
        default_sql = 'SELECT * FROM "%s"' % table
        model_class = dataset[table].model_class
        pk = model_class._meta.primary_key
        is_composite_pk = isinstance(pk, CompositeKey)
        allow_edit = not dataset.is_readonly and pk is not False
        allow_bulk = allow_edit and not is_composite_pk
    else:
        default_sql = ''
        model_class = dataset._base_model
        pk = None
        is_composite_pk = False
        allow_edit = False
        allow_bulk = False

    if request.method == 'POST':
        action = request.form.get('action')
        pks = request.form.getlist('pk')
        if action == 'bulk-delete':
            if not allow_bulk:
                flash('Cannot perform bulk operation on this table.', 'warning')
            elif not pks:
                flash('No rows were selected.', 'warning')
            else:
                n = (model_class.delete()
                     .where(model_class._meta.primary_key.in_(pks))
                     .execute())
                flash('Successfully deleted %s row(s)' % n, 'success')

    page = page_next = page_prev = page_start = page_end = total_pages = 1
    total = -1  # Number of rows in query result.
    if qsql:
        if export_format:
            query = model_class.raw(qsql).dicts()
            return export(query, export_format, table)

        try:
            total, = dataset.query('SELECT COUNT(*) FROM (%s) as _' %
                                   qsql.rstrip('; ')).fetchone()
        except Exception as exc:
            total = -1

        # Apply pagination.
        rpp = app.config['QUERY_ROWS_PER_PAGE']
        import math
        page = request.values.get('page')
        if page and page.isdigit():
            page = max(int(page), 1)
        else:
            page = 1
        offset = (page - 1) * rpp
        page_prev, page_next = max(page - 1, 1), page + 1

        # Figure out highest page.
        if total > 0:
            total_pages = max(1, int(math.ceil(total / float(rpp))))
            page = max(min(page, total_pages), 1)
            page_next = min(page + 1, total_pages)
            page_start = offset + 1
            page_end = min(total, page_start + rpp - 1)

        qsql = ('SELECT * FROM (%s) AS _ LIMIT %d OFFSET %d' %
                (qsql.rstrip(' ;'), rpp, offset))

        try:
            cursor = dataset.query(qsql)
        except Exception as exc:
            error = str(exc)
            app.logger.exception('Error in user-submitted query.')
        else:
            data = cursor.fetchmany(rpp)
            data_description = cursor.description
            row_count = cursor.rowcount

    if data_description is not None and table:
        col_names = [r[0] for r in data_description]
        if pk and not is_composite_pk and pk.column_name in col_names:
            pk_index = col_names.index(pk.column_name)

    return render_template(
        template,
        allow_bulk=allow_bulk,
        allow_edit=allow_edit,
        data=data,
        data_description=data_description,
        default_sql=default_sql,
        error=error,
        ordering=ordering,
        page=page,
        page_end=page_end,
        page_next=page_next,
        page_prev=page_prev,
        page_start=page_start,
        pk_index=pk_index,
        query_images=get_query_images(),
        row_count=row_count,
        sql=sql,
        table=table,
        table_sql=dataset.get_table_sql(table),
        total=total,
        total_pages=total_pages)

@app.route('/query/', methods=['GET', 'POST'])
def generic_query():
    return _query_view('query.html')

def require_table(fn):
    @wraps(fn)
    def inner(dataset_name, table, *args, **kwargs):
        if table not in get_dataset().tables:
            abort(404)
        return fn(dataset_name, table, *args, **kwargs)
    return inner

@app.route('/<dataset_name>/create-table/', methods=['POST'])
@admin_required
@require_dataset
def table_create(dataset_name):
    table = (request.form.get('table_name') or '').strip()
    if not table:
        flash('Table name is required.', 'danger')
        dest = request.form.get('redirect') or url_for('index')
        dest = '/' + dest.lstrip('/')  # idiot vulnerability "researchers".
        return redirect(dest)

    try:
        get_dataset()[table]
    except Exception as exc:
        flash('Error: %s' % str(exc), 'danger')
        app.logger.exception('Error attempting to create table.')
    return redirect(url_for('table_import', dataset_name=dataset_name, table=table))

def get_request_data():
    if request.method == 'POST':
        return request.form
    return request.args

@app.route('/<dataset_name>/<table>/add-column/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def add_column(dataset_name, table):
    dataset = get_dataset()

    class JsonField(TextField):
        field_type = 'JSON'
    column_mapping = OrderedDict((
        ('TEXT', TextField),
        ('INTEGER', IntegerField),
        ('REAL', FloatField),
        ('BLOB', BlobField),
        ('JSON', JsonField),
        ('BOOL', BooleanField),
        ('DATETIME', DateTimeField),
        ('DATE', DateField),
        ('DECIMAL', DecimalField),
        ('TIME', TimeField),
        ('VARCHAR', CharField)))

    request_data = get_request_data()
    col_type = request_data.get('type')
    name = request_data.get('name', '')

    if request.method == 'POST':
        name = re.sub(r'[^\w]+', '_', name.strip())
        if name and col_type in column_mapping:
            try:
                migrate(
                    dataset._migrator.add_column(
                        table,
                        name,
                        column_mapping[col_type](null=True)))
            except Exception as exc:
                flash('Error attempting to add column "%s": %s' % (name, exc),
                      'danger')
                app.logger.exception('Error attempting to add column.')
            else:
                flash('Column "%s" was added successfully!' % name, 'success')
                dataset.update_cache(table)
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('Name and column type are required.', 'danger')

    return render_template(
        'add_column.html',
        col_type=col_type,
        column_mapping=column_mapping,
        name=name,
        table=table,
        table_sql=dataset.get_table_sql(table))

@app.route('/<dataset_name>/<table>/drop-column/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def drop_column(dataset_name, table):
    dataset = get_dataset()
    request_data = get_request_data()
    name = request_data.get('name', '')
    columns = dataset.get_columns(table)
    column_names = [column.name for column in columns]

    if request.method == 'POST':
        if name in column_names:
            try:
                migrate(dataset._migrator.drop_column(table, name))
            except Exception as exc:
                flash('Error attempting to drop column "%s": %s' % (name, exc),
                      'danger')
                app.logger.exception('Error attempting to drop column.')
            else:
                flash('Column "%s" was dropped successfully!' % name, 'success')
                dataset.update_cache(table)
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('Name is required.', 'danger')

    return render_template(
        'drop_column.html',
        columns=columns,
        column_names=column_names,
        name=name,
        table=table)

@app.route('/<dataset_name>/<table>/rename-column/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def rename_column(dataset_name, table):
    dataset = get_dataset()
    request_data = get_request_data()
    rename = request_data.get('rename', '')
    rename_to = request_data.get('rename_to', '')

    columns = dataset.get_columns(table)
    column_names = [column.name for column in columns]

    if request.method == 'POST':
        rename_to = re.sub(r'[^\w]+', '_', rename_to.strip())
        if (rename in column_names) and (rename_to not in column_names):
            try:
                migrate(dataset._migrator.rename_column(table, rename, rename_to))
            except Exception as exc:
                flash('Error attempting to rename column "%s": %s' %
                      (rename, exc), 'danger')
                app.logger.exception('Error attempting to rename column.')
            else:
                flash('Column "%s" was renamed successfully!' % rename, 'success')
                dataset.update_cache(table)
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('Column name is required and cannot conflict with an '
                  'existing column\'s name.', 'danger')

    return render_template(
        'rename_column.html',
        columns=columns,
        column_names=column_names,
        rename=rename,
        rename_to=rename_to,
        table=table)

@app.route('/<dataset_name>/<table>/add-index/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def add_index(dataset_name, table):
    dataset = get_dataset()
    request_data = get_request_data()
    indexed_columns = request_data.getlist('indexed_columns')
    unique = bool(request_data.get('unique'))

    columns = dataset.get_columns(table)

    if request.method == 'POST':
        if indexed_columns:
            try:
                migrate(
                    dataset._migrator.add_index(
                        table,
                        indexed_columns,
                        unique))
            except Exception as exc:
                flash('Error attempting to create index: %s' % exc, 'danger')
                app.logger.exception('Error attempting to create index.')
            else:
                flash('Index created successfully.', 'success')
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('One or more columns must be selected.', 'danger')

    return render_template(
        'add_index.html',
        columns=columns,
        indexed_columns=indexed_columns,
        table=table,
        unique=unique)

@app.route('/<dataset_name>/<table>/drop-index/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def drop_index(dataset_name, table):
    dataset = get_dataset()
    request_data = get_request_data()
    name = request_data.get('name', '')
    indexes = dataset.get_indexes(table)
    index_names = [index.name for index in indexes]

    if request.method == 'POST':
        if name in index_names:
            try:
                migrate(dataset._migrator.drop_index(table, name))
            except Exception as exc:
                flash('Error attempting to drop index: %s' % exc, 'danger')
                app.logger.exception('Error attempting to drop index.')
            else:
                flash('Index "%s" was dropped successfully!' % name, 'success')
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('Index name is required.', 'danger')

    return render_template(
        'drop_index.html',
        indexes=indexes,
        index_names=index_names,
        name=name,
        table=table)

@app.route('/<dataset_name>/<table>/drop-trigger/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def drop_trigger(dataset_name, table):
    dataset = get_dataset()
    request_data = get_request_data()
    name = request_data.get('name', '')
    triggers = dataset.get_triggers(table)
    trigger_names = [trigger.name for trigger in triggers]

    if request.method == 'POST':
        if name in trigger_names:
            try:
                dataset.query('DROP TRIGGER "%s";' % name)
            except Exception as exc:
                flash('Error attempting to drop trigger: %s' % exc, 'danger')
                app.logger.exception('Error attempting to drop trigger.')
            else:
                flash('Trigger "%s" was dropped successfully!' % name, 'success')
                return redirect(url_for('dataset_dispatcher', dataset_name=dataset_name, table=table))
        else:
            flash('Trigger name is required.', 'danger')

    return render_template(
        'drop_trigger.html',
        triggers=triggers,
        trigger_names=trigger_names,
        name=name,
        table=table)


@app.route('/<dataset_name>/<table>/content/', methods=['GET', 'POST'])
@require_table
@require_dataset
def table_content(dataset_name, table):
    dataset = get_dataset()
    dataset.update_cache(table)
    ds_table = dataset[table]
    model = ds_table.model_class
    is_composite_pk = isinstance(model._meta.primary_key, CompositeKey)

    allow_edit = not dataset.is_readonly and model._meta.primary_key is not False
    allow_bulk = allow_edit and not is_composite_pk

    if request.method == 'POST':
        if not allow_bulk:
            flash('Cannot perform bulk operation on this table.', 'warning')
        else:
            action = request.form['action']
            pks = request.form.getlist('pk')
            if not pks:
                flash('No rows were selected.', 'warning')
            elif action == 'bulk-delete':
                n = (model.delete()
                     .where(model._meta.primary_key.in_(pks))
                     .execute())
                flash('Successfully deleted %s row(s)' % n, 'success')
            else:
                flash('Unrecognized action', 'warning')
        return redirect(request.full_path)

    page_number = request.args.get('page') or ''
    if page_number == 'last': page_number = '1000000'
    page_number = int(page_number) if page_number.isdigit() else 1

    total_rows = ds_table.all().count()
    rows_per_page = app.config['ROWS_PER_PAGE']
    total_pages = max(1, int(math.ceil(total_rows / float(rows_per_page))))
    # Restrict bounds.
    page_number = max(min(page_number, total_pages), 1)

    previous_page = page_number - 1 if page_number > 1 else None
    next_page = page_number + 1 if page_number < total_pages else None

    query = ds_table.all().paginate(page_number, rows_per_page)

    ordering = request.args.get('ordering')
    if ordering:
        field = model._meta.columns[ordering.lstrip('-')]
        if ordering.startswith('-'):
            field = field.desc()
        query = query.order_by(field)

    session['%s.last_viewed' % table] = (page_number, ordering)

    field_names = ds_table.columns
    columns = [f.column_name for f in ds_table.model_class._meta.sorted_fields]

    return render_template(
        'table_content.html',
        allow_bulk=allow_bulk,
        allow_edit=allow_edit,
        columns=columns,
        ds_table=ds_table,
        field_names=field_names,
        is_composite_pk=isinstance(model._meta.primary_key, CompositeKey),
        next_page=next_page,
        ordering=ordering,
        page=page_number,
        previous_page=previous_page,
        query=query,
        table=table,
        table_pk=model._meta.primary_key,
        table_sql=dataset.get_table_sql(table),
        total_pages=total_pages,
        total_rows=total_rows)

def minimal_validate_field(field, value):
    if value.lower().strip() == 'null':
        value = None
    if value is None and not field.null:
        return 'NULL', 'Column does not allow NULL values.'
    if value is None:
        return None, None
    if isinstance(field, IntegerField):
        try:
            _ = int(value)
        except Exception:
            return value, 'Value is not a number.'
    elif isinstance(field, FloatField):
        try:
            _ = float(value)
        except Exception:
            return value, 'Value is not a numeric/real.'
    elif isinstance(field, BooleanField):
        if value.lower() not in ('1', '0', 'true', 'false', 't', 'f'):
            return value, 'Value must be 1, 0, true, false, t or f.'
        value = True if value.lower() in ('1', 't', 'true') else False
    elif isinstance(field, BlobField):
        if app.config['BLOB_AS_BASE64']:
            try:
                value = base64.b64decode(value)
            except Exception as exc:
                return value, 'Value must be base64-encoded binary data.'
        else:
            try:
                value = bytes.fromhex(value)
            except Exception as exc:
                return value, 'Value must be valid hex representation.'
    try:
        field.db_value(value)
    except Exception as exc:
        return value, str(exc)

    return value, None

@app.route('/<dataset_name>/<table>/insert/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def table_insert(dataset_name, table):
    dataset = get_dataset()
    dataset.update_cache(table)
    model = dataset[table].model_class

    columns = []
    fields = []
    defaults = {}
    row = {}
    for column in dataset.get_columns(table):
        field = model._meta.columns[column.name]
        if isinstance(field, AutoField):
            continue
        if column.default:
            defaults[column.name] = column.default
        columns.append(column)
        fields.append(field)
        row[field.name] = ''

    edited = set()
    errors = {}
    if request.method == 'POST':
        insert = {}
        for key, value in request.form.items():
            if key not in model._meta.fields: continue
            field = model._meta.fields[key]
            edited.add(field.name)
            row[field.name] = value

            value, err = minimal_validate_field(field, value)
            if err:
                errors[key] = err
            else:
                insert[field] = value

        if errors:
            flash('One or more errors prevented the row being inserted.',
                  'danger')
        elif insert:
            try:
                with dataset.transaction() as txn:
                    n = model.insert(insert).execute()
            except Exception as exc:
                flash('Insert failed: %s' % exc, 'danger')
                app.logger.exception('Error attempting to insert row into %s.', table)
            else:
                flash('Successfully inserted record (%s).' % n, 'success')
                return redirect(url_for(
                    'table_content',
                    'table_content',
                    table=table,
                    page='last'))
        else:
            flash('No data was specified to be inserted.', 'warning')
    else:
        edited = set(model._meta.sorted_field_names) - set(defaults)  # Make all fields editable on load.

    columns_fields = zip(columns, fields)

    return render_template(
        'table_insert.html',
        columns_fields=columns_fields,
        defaults=defaults,
        edited=edited,
        errors=errors,
        model=model,
        row=row,
        table=table)

def redirect_to_previous(dataset_name, table):
    page_ordering = session.get('%s.last_viewed' % table)
    if not page_ordering:
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))
    page, ordering = page_ordering
    kw = {'dataset_name': dataset_name}
    if page and page != 1:
        kw['page'] = page
    if ordering:
        kw['ordering'] = ordering
    return redirect(url_for('table_content', table=table, **kw))

@app.route('/<dataset_name>/<table>/update/<b64:pk>/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def table_update(dataset_name, table, pk):
    dataset = get_dataset()
    dataset.update_cache(table)
    model = dataset[table].model_class
    table_pk = model._meta.primary_key
    if not table_pk:
        flash('Table must have a primary key to perform update.', 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))
    elif pk == '__uneditable__':
        flash('Could not encode primary key to perform update.', 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))

    expr = decode_pk(model, pk)
    try:
        obj = model.get(expr)
    except model.DoesNotExist:
        pk_repr = pk_display(table_pk, pk)
        flash('Could not fetch row with primary-key %s.' % str(pk_repr), 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))

    columns = []
    fields = []
    for column in dataset.get_columns(table):
        columns.append(column)
        fields.append(model._meta.columns[column.name])

    row = {}
    for field in fields:
        value = getattr(obj, field.name)
        if value is None:
            row[field.name] = None
        elif isinstance(field, BlobField):
            if app.config['BLOB_AS_BASE64']:
                row[field.name] = base64.b64encode(value).decode('utf8')
            else:
                row[field.name] = value.hex()
        else:
            row[field.name] = value

    edited = set()
    errors = {}
    if request.method == 'POST':
        update = {}
        for key, value in request.form.items():
            if key not in model._meta.fields: continue
            field = model._meta.fields[key]
            edited.add(field.name)
            row[field.name] = value

            value, err = minimal_validate_field(field, value)
            if err:
                errors[key] = err
            else:
                update[field] = value

        if errors:
            flash('One or more errors prevented the row being updated.',
                  'danger')
        elif update:
            try:
                with dataset.transaction() as txn:
                    n = model.update(update).where(expr).execute()
            except Exception as exc:
                flash('Update failed: %s' % exc, 'danger')
                app.logger.exception('Error attempting to update row from %s.', table)
            else:
                flash('Successfully updated %s record.' % n, 'success')
                return redirect_to_previous(dataset_name, table)
        else:
            flash('No data was specified to be updated.', 'warning')

    columns_fields = zip(columns, fields)

    return render_template(
        'table_update.html',
        columns_fields=columns_fields,
        edited=edited,
        errors=errors,
        fields=fields,
        model=model,
        pk=pk,
        row=row,
        table=table,
        table_pk=model._meta.primary_key)

@app.route('/<dataset_name>/<table>/delete/<b64:pk>/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def table_delete(dataset_name, table, pk):
    dataset = get_dataset()
    dataset.update_cache(table)
    model = dataset[table].model_class
    table_pk = model._meta.primary_key
    if not table_pk:
        flash('Table must have a primary key to perform delete.', 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))
    elif pk == '__uneditable__':
        flash('Could not encode primary key to perform delete.', 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))

    expr = decode_pk(model, pk)
    try:
        row = model.select().where(expr).dicts().get()
    except model.DoesNotExist:
        pk_repr = pk_display(table_pk, pk)
        flash('Could not fetch row with primary-key %s.' % str(pk_repr), 'danger')
        return redirect(url_for('table_content', dataset_name=dataset_name, table=table))

    if request.method == 'POST':
        try:
            with dataset.transaction() as txn:
                n = model.delete().where(expr).execute()
        except Exception as exc:
            flash('Delete failed: %s' % exc, 'danger')
            app.logger.exception('Error attempting to delete row from %s.', table)
        else:
            flash('Successfully deleted %s record.' % n, 'success')
            return redirect_to_previous(dataset_name, table)

    return render_template(
        'table_delete.html',
        model=model,
        pk=pk,
        row=row,
        table=table,
        table_pk=table_pk)

@app.route('/<dataset_name>/<table>/query/', methods=['GET', 'POST'])
@require_table
@require_dataset
def table_query(dataset_name, table):
    return _query_view('table_query.html', table)

def export(query, export_format, table=None):
    dataset = get_dataset()
    buf = StringIO()
    if export_format == 'json':
        kwargs = {'indent': 2}
        filename = 'export.json'
        mimetype = 'application/json'
    else:
        kwargs = {}
        filename = 'export.csv'
        mimetype = 'text/csv'

    if table:
        filename = '%s-%s' % (table, filename)

    # Avoid any special chars in export filename.
    filename = re.sub(r'[^\w\d\-\.]+', '', filename)

    if peewee_version >= (4, 0, 2):
        kwargs['base64_bytes'] = app.config['BLOB_AS_BASE64']

    dataset.freeze(query, export_format, file_obj=buf, **kwargs)

    response_data = buf.getvalue().encode('utf8')
    response = make_response(response_data)
    response.headers['Content-Length'] = len(response_data)
    response.headers['Content-Type'] = mimetype
    response.headers['Content-Disposition'] = 'attachment; filename="%s"' % (
        filename)
    response.headers['Expires'] = 0
    response.headers['Pragma'] = 'public'
    return response

@app.route('/<dataset_name>/<table>/export/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def table_export(dataset_name, table):
    dataset = get_dataset()
    columns = dataset.get_columns(table)
    if request.method == 'POST':
        export_format = request.form.get('export_format') or 'json'
        col_dict = {c.name: c for c in columns}
        selected = [c for c in (request.form.getlist('columns') or [])
                    if c in col_dict]
        if not selected:
            flash('Please select one or more columns to export.', 'danger')
        else:
            model = dataset[table].model_class
            fields = [model._meta.columns[c] for c in selected]
            query = model.select(*fields).dicts()
            try:
                return export(query, export_format, table)
            except Exception as exc:
                flash('Error generating export: %s' % exc, 'danger')
                app.logger.exception('Error generating export.')

    return render_template(
        'table_export.html',
        columns=columns,
        table=table)

@app.route('/<dataset_name>/<table>/import/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def table_import(dataset_name, table):
    dataset = g.dataset
    count = None
    request_data = get_request_data()
    strict = bool(request_data.get('strict'))

    if request.method == 'POST':
        file_obj = request.files.get('file')
        if not file_obj:
            flash('Please select an import file.', 'danger')
        elif not file_obj.filename.lower().endswith(('.csv', '.json')):
            flash('Unsupported file-type. Must be a .json or .csv file.',
                  'danger')
        else:
            if file_obj.filename.lower().endswith('.json'):
                format = 'json'
            else:
                format = 'csv'

            # Here we need to translate the file stream. Werkzeug uses a
            # spooled temporary file opened in wb+ mode, which is not
            # compatible with Python's CSV module. We'd need to reach pretty
            # far into Flask's internals to modify this behavior, so instead
            # we'll just translate the stream into utf8-decoded unicode.
            try:
                stream = TextIOWrapper(file_obj, encoding='utf8')
            except AttributeError:
                # The SpooledTemporaryFile used by werkzeug does not
                # implement an API that the TextIOWrapper expects, so we'll
                # just consume the whole damn thing and decode it.
                # Fixed in werkzeug 0.15.
                stream = StringIO(file_obj.read().decode('utf8'))

            kwargs = {}
            if peewee_version >= (4, 0, 2):
                kwargs['base64_bytes'] = app.config['BLOB_AS_BASE64']
            try:
                with dataset.transaction():
                    count = dataset.thaw(
                        table,
                        format=format,
                        file_obj=stream,
                        strict=strict,
                        **kwargs)
            except Exception as exc:
                flash('Error importing file: %s' % exc, 'danger')
                app.logger.exception('Error importing file.')
            else:
                flash(
                    'Successfully imported %s objects from %s.' % (
                        count, file_obj.filename),
                    'success')
                return redirect(url_for('table_content', dataset_name=dataset_name, table=table, _anchor=''))

    return render_template(
        'table_import.html',
        count=count,
        strict=strict,
        table=table)

@app.route('/<dataset_name>/<table>/drop/', methods=['GET', 'POST'])
@admin_required
@require_table
@require_dataset
def drop_table(dataset_name, table):
    dataset = get_dataset()
    is_view = any(v.name == table for v in dataset.get_all_views())
    label = 'view' if is_view else 'table'
    if request.method == 'POST':
        try:
            if is_view:
                dataset.query('DROP VIEW "%s";' % table)
            else:
                model_class = dataset[table].model_class
                model_class.drop_table()
        except Exception as exc:
            flash('Error attempting to drop %s "%s".' % (label, table), 'danger')
            app.logger.exception('Error attempting to drop %s "%s".', label, table)
        else:
            dataset.update_cache()  # Update all tables.
            flash('%s "%s" dropped successfully.' %
                  ('view' if is_view else 'table', table),
                  'success')
            return redirect(url_for('index'))

    return render_template('drop_table.html', is_view=is_view, table=table)

@app.template_filter('format_index')
def format_index(index_sql):
    split_regex = re.compile(r'\bon\b', re.I)
    if not split_regex.search(index_sql):
        return index_sql

    create, definition = split_regex.split(index_sql)
    return '\nON '.join((create.strip(), definition.strip()))

@app.template_filter('encode_pk')
def encode_pk(row, pk):
    if isinstance(pk, CompositeKey):
        try:
            return ':::'.join([str(row[k]) for k in pk.field_names])
        except Exception as exc:
            return '__uneditable__'
    return row[pk.column_name]

def decode_pk(model, pk_data):
    pk = model._meta.primary_key
    if isinstance(pk, CompositeKey):
        fields = [pk.model._meta.columns[f] for f in pk.field_names]
        values = pk_data.split(':::')
        expressions = [(f == v) for f, v in zip(fields, values)]
        return reduce(operator.and_, expressions)
    return (pk == pk_data)

@app.template_filter('pk_display')
def pk_display(table_pk, pk):
    if isinstance(table_pk, CompositeKey):
        return tuple(pk.split(':::'))
    elif isinstance(pk, bytes):
        pk = base64.b64encode(pk).decode()
    return pk

link_re = re.compile(r'(?:https?|mailto)://[^\s]+')

@app.template_filter('value_filter')
def value_filter(value, max_length=50):
    if isinstance(value, (int, float)):
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            value = value.decode('utf8')
        except UnicodeDecodeError:
            if app.config['BLOB_AS_BASE64']:
                value = base64.b64encode(value)[:1024].decode('utf8')
            else:
                value = value.hex()[:1024]
    if isinstance(value, str):
        if link_re.match(value):
            label = value
            if len(value) > max_length and app.config['TRUNCATE_VALUES']:
                label = value[:max_length]
            return '<a href="%s">%s</a>' % (escape(value), escape(label))
        value = escape(value)
        if len(value) > max_length and app.config['TRUNCATE_VALUES']:
            return ('<span class="truncated">%s</span> '
                    '<span class="full" style="display:none;">%s</span>'
                    '<a class="toggle-value" href="#">...</a>') % (
                        value[:max_length],
                        value)
    return value

column_re = re.compile(r'(.+?)\((.+)\)', re.S)
column_split_re = re.compile(r'(?:[^,(]|\([^)]*\))+')

def _format_create_table(sql):
    create_table, column_list = column_re.search(sql).groups()
    columns = ['  %s' % column.strip()
               for column in column_split_re.findall(column_list)
               if column.strip()]
    return '%s (\n%s\n)' % (
        create_table,
        ',\n'.join(columns))

@app.template_filter()
def format_create_table(sql):
    try:
        return _format_create_table(sql)
    except:
        return sql

@app.template_filter('highlight')
def highlight_filter(data):
    return Markup(syntax_highlight(data))

def get_query_images():
    accum = []
    image_dir = os.path.join(app.static_folder, 'img')
    if not os.path.exists(image_dir):
        return accum
    for filename in sorted(os.listdir(image_dir)):
        basename = os.path.splitext(os.path.basename(filename))[0]
        parts = basename.split('-')
        accum.append((parts, 'img/' + filename))
    return accum

#
# Flask application helpers.
#

@app.context_processor
def _general():
    dataset_name = None
    dataset = get_dataset()  # This ensures g.dataset is populated if needed.
    if hasattr(g, 'dataset') and g.dataset:
        # The session holds the logical name of the dataset, which is what we
        # need for generating correct URLs. g.dataset.basename is the actual
        # filename, which is not what we want for routing.
        dataset_name = session.get('dataset')
    return {
        'dataset': dataset,
        'dataset_name': dataset_name,
        'datasets': datasets,
        'enable_load': app.config.get('ENABLE_LOAD'),
        'enable_filesystem': app.config.get('ENABLE_FILESYSTEM'),
        'login_required': bool(app.config.get('PASSWORD')),
        'version': __version__,
    }

@app.context_processor
def _now():
    return {'now': datetime.datetime.now()}

@app.before_request
def _connect_db():
    # get_dataset() is now called in the dispatch view or for non-dataset routes.
    # We only connect if g.dataset is already set.
    if hasattr(g, 'dataset'):
        g.dataset.connect()
        if dataset_config.get('startup_hook'):
            dataset_config['startup_hook'](g.dataset._database)

@app.teardown_request
def _close_db(exc):
    dataset = get_dataset()
    if dataset and not dataset._database.is_closed():
        dataset.close()


class PrefixMiddleware(object):
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = '/%s' % prefix.strip('/')
        self.prefix_len = len(self.prefix)

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][self.prefix_len:]
            environ['SCRIPT_NAME'] = self.prefix
            return self.app(environ, start_response)
        else:
            start_response('404', [('Content-Type', 'text/plain')])
            return ['URL does not match application prefix.'.encode()]

#
# Script options.
#

def open_browser_tab(host, port):
    url = 'http://%s:%s/' % (host, port)

    def _open_tab(url):
        time.sleep(1.5)
        webbrowser.open_new_tab(url)

    thread = threading.Thread(target=_open_tab, args=(url,))
    thread.daemon = True
    thread.start()

def initialize_dataset(filename):
    dataset_kw = {}
    if peewee_version >= (3, 14, 9):
        dataset_kw['include_views'] = True

    if filename.startswith('gs://'):
        if not (datastore and datastore.cloud_storage):
            die("Google Cloud Storage library not found, but GCS path specified.")

        # Download from GCS to a temporary file
        fd, local_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)

        datastore.cloud_storage.download_file(filename, local_path)
        filename = local_path

    if dataset_config['read_only']:
        if peewee_version < (3, 5, 1):
            die('Peewee 3.5.1 or newer is required for read-only access.')
        db = SqliteDatabase('file:%s?mode=ro' % filename, uri=True)
        try:
            db.connect()
        except OperationalError:
            die('Unable to open database file in read-only mode. Ensure that '
                'the database exists in order to use read-only mode.')
        db.close()
    else:
        db = SqliteDatabase(filename)

    if dataset_config['foreign_keys']:
        db.pragma('foreign_keys', True, permanent=True)

    if dataset_config['extensions']:
        # Load extensions before performing introspection.
        for ext in dataset_config['extensions']:
            db.load_extension(ext)

    if dataset_config['startup_hook']:
        dataset_config['startup_hook'](db)

    dataset = SqliteDataSet(db, bare_fields=True, **dataset_kw)
    dataset.close()
    return dataset

def initialize_app(filenames=None, default_config=None):
    global datasets
    global dataset_config

    # New configuration strategy:
    # 1. Try to load config from Firestore.
    # 2. If it doesn't exist, use the provided defaults.
    # 3. Save the defaults to Firestore to seed the config for next time.
    # 4. Apply the final config to the Flask app.
    if datastore and datastore.datastore:
        app.logger.info("Attempting to load configuration from Firestore.")
        config = datastore.datastore.get_config()
        if config is None:
            app.logger.info("No config found in Firestore, using defaults and saving.")
            config = default_config or {}
            datastore.datastore.save_config(config)
    else:
        config = default_config or {}

    # Apply the loaded/default configuration to the app.
    app.config.update(config)

    # Set a default session lifetime (e.g., 8 hours)
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(hours=8))

    # Extract values needed for dataset initialization.
    read_only = app.config.get('READ_ONLY', False)
    url_prefix = app.config.get('URL_PREFIX')
    extensions = app.config.get('EXTENSIONS')
    foreign_keys = app.config.get('FOREIGN_KEYS')
    startup_hook = app.config.get('STARTUP_HOOK')
    # If using Firestore, fetch the initial list of databases from there.
    # Otherwise, use the filenames passed as an argument.
    if datastore and datastore.datastore:
        app.logger.info("Using Firestore for state management.")
        db_configs = datastore.datastore.get_all_datasets()
        # `db_configs` is a dict of {name: path}. We need the paths.
        filenames = db_configs.values()
    elif filenames is None:
        filenames = []

    dataset_config.update(
        read_only=read_only,
        extensions=extensions,
        foreign_keys=foreign_keys,
        startup_hook=startup_hook)

    if url_prefix:
        app.logger.warning("url_prefix is set but will be ignored when sqlite-web is embedded.")
        # app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=url_prefix)
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=url_prefix)

    for filename in filenames:
        # Use the filename without extension as the default key.
        # This matches the behavior when loading files.
        key = os.path.splitext(os.path.basename(filename))[0]
        datasets[key] = initialize_dataset(filename)
