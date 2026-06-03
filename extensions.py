import threading
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from flask_login import LoginManager

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per minute"], storage_uri='memory://')
cache = Cache()
login_manager = LoginManager()

# Constants that were previously at top-level of app.py
RATE_LIMIT_DEFAULTS = {
    'rl_login':    '20 per hour',
    'rl_register': '10 per hour',
    'rl_upload':   '10 per hour',
    'rl_generate': '10 per hour',
    'rl_chat':     '60 per hour',
}

DEFAULT_WORD_LIMIT = 2000
DEFAULT_SCHED_PREF_LIMIT = 200

_settings_cache_lock = threading.Lock()

def init_app_extensions(app):
    db.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'landing'
    login_manager.login_message = ''
