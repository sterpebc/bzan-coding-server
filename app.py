"""
A sample Hello World server with an integrated SQLite explorer.
"""
import os
import sys
from flask import Flask, render_template
from werkzeug.wrappers import Request
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# --- Main Flask App ---
# This is your original application
main_app = Flask(__name__)


@main_app.route('/')
def hello():
    """Return a friendly HTTP greeting."""
    message = "The application is running successfully!"
    # K_SERVICE/K_REVISION were set automatically by Cloud Run. On a plain
    # server they won't exist, so fall back to something locally meaningful.
    service = os.environ.get('K_SERVICE', os.environ.get('APP_NAME', 'coding-server'))
    revision = os.environ.get('K_REVISION', f"pid-{os.getpid()}")
    return render_template('index.html',
                           message=message,
                           Service=service,
                           Revision=revision)


# --- sqlite-web App ---
# Import and configure the sqlite-web application

# Add the sqlite_web subdirectory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sqlite_web'))

import sqlite_web as sqlite_web_mod
sqlite_web_app = sqlite_web_mod.app if sqlite_web_mod else None

# Define the default configuration for sqlite-web. This will be used
# on the first run and saved to the local datastore (see datastore.py)
# for subsequent runs.
#
# DB_UPLOAD_DIR controls where uploaded .db files are stored persistently.
# Set the DB_UPLOAD_DIR environment variable in production (e.g. to a path
# under /data on the dev server) so uploads survive a restart; if unset,
# it falls back to a local ./data/uploads directory next to this file,
# which is fine for local development but not for a shared server.
default_sqlite_web_config = {
    'ENABLE_LOAD': True,
    'ENABLE_FILESYSTEM': False,
    'READ_ONLY': True,
    'DB_UPLOAD_DIR': os.environ.get(
        'DB_UPLOAD_DIR',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'uploads')
    ),
    'ROWS_PER_PAGE': 50,
    'QUERY_ROWS_PER_PAGE': 1000,
}

sqlite_web_mod.initialize_app(default_config=default_sqlite_web_config)

# --- API App ---
# Import and configure the API application
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))
try:
    from api import app as api_app
except (ImportError, ModuleNotFoundError) as e:
    main_app.logger.error(f"Could not import the API app: {e}")
    api_app = None

# --- Combined App ---
# Use DispatcherMiddleware to combine the two apps.
# Your main app will handle the root URL ('/').
# The sqlite_web_app will handle everything under '/db'.
application = DispatcherMiddleware(main_app, {
    '/db': sqlite_web_app if sqlite_web_app else main_app,
    '/api': api_app if api_app else main_app
})


class ReverseProxied:
    """
    WSGI middleware that makes this app aware it's being served under a
    path prefix by a reverse proxy (e.g. nginx proxying
    http://host/coding-server/ through to this app's root).

    Without this, Flask has no idea the prefix exists: it only ever sees
    PATH_INFO with the prefix already stripped by nginx, so anything the
    app generates itself -- redirect() targets, url_for() links -- comes
    out WITHOUT the prefix. That's fine for direct requests, but a
    post-login redirect (for example) would then send the browser to
    "/" instead of "/coding-server/", missing the app entirely.

    The fix is the standard WSGI pattern for this (see Flask's own docs
    under "Proxy Setups"): the reverse proxy tells us what prefix it
    stripped via a request header, and we set WSGI's SCRIPT_NAME
    accordingly. Werkzeug/Flask's URL generation already knows to
    respect SCRIPT_NAME automatically once it's set -- no other code
    needs to change.

    Expects nginx to set `proxy_set_header X-Script-Name /coding-server;`
    (no trailing slash) on the location block that proxies to this app.
    Requests made directly (no reverse proxy, e.g. local dev) are
    unaffected since the header simply won't be present.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ.get('PATH_INFO', '')
            # Defensive: if the prefix is somehow still present in
            # PATH_INFO (nginx configured without prefix-stripping), strip
            # it here too rather than double-counting it.
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        # Respect X-Forwarded-Proto too, so generated URLs are https:// if
        # the proxy terminates TLS even though this app only ever speaks
        # plain HTTP internally.
        forwarded_proto = environ.get('HTTP_X_FORWARDED_PROTO', '')
        if forwarded_proto:
            environ['wsgi.url_scheme'] = forwarded_proto

        return self.wsgi_app(environ, start_response)


application = ReverseProxied(application)

# --- Proxy App for `flask run` ---
# The `flask run` command expects a Flask app instance. This proxy app
# will wrap the DispatcherMiddleware and allow `flask run` to work correctly.
class WsgiProxyApp(Flask):
    def __init__(self, wsgi_app):
        super().__init__(__name__)
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

# The `flask run` command will now target this `proxy_app`
proxy_app = WsgiProxyApp(application)
proxy_app.config["TEMPLATES_AUTO_RELOAD"] = True


if __name__ == '__main__':
    # Note: We run the 'application' object now, not 'main_app'.
    # The Flask development server can run any WSGI-compliant app.
    server_port = int(os.environ.get('PORT', '8080'))
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', server_port, application, use_reloader=False, use_debugger=False)
