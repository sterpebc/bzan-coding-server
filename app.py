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
    service = os.environ.get('K_SERVICE', 'Unknown service')
    revision = os.environ.get('K_REVISION', 'Unknown revision')
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
# on the first run and saved to Firestore for subsequent runs.
default_sqlite_web_config = {
    'ENABLE_LOAD': True,
    'ENABLE_FILESYSTEM': False,
    'READ_ONLY': True,
    'DB_UPLOAD_DIR': None,  # Defaults to a system temp directory
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
