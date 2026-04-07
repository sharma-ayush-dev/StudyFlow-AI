import os
import json
import datetime

from flask import Flask, request, jsonify, render_template, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

from text_extractor import organize_with_llm
from schedule_planner import generate_schedule, MODELS, DEFAULT_MAX_TOKENS


# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'change-me-before-deploy')
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///userdata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH']             = 20 * 1024 * 1024

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view    = 'landing'
login_manager.login_message = ''


# ─────────────────────────────────────────────
# DEFAULT RATE LIMIT VALUES
# Stored in AppSettings so the admin can change them at runtime.
# Keys map to DB keys; values are flask-limiter strings.
# ─────────────────────────────────────────────

RATE_LIMIT_DEFAULTS = {
    'rl_login':    '20 per hour',
    'rl_register': '10 per hour',
    'rl_upload':   '10 per hour',
    'rl_generate': '10 per hour',   # raised from 5 — was too restrictive for testing
}


def _get_setting(key: str, fallback=None) -> str | None:
    """Read a value from AppSettings. Returns fallback if not set."""
    try:
        row = AppSettings.query.get(key)
        return row.value if row else fallback
    except Exception:
        return fallback


def _set_setting(key: str, value: str):
    row = AppSettings.query.get(key)
    if row:
        row.value = value
    else:
        db.session.add(AppSettings(key=key, value=value))
    db.session.commit()


# ─────────────────────────────────────────────
# DYNAMIC RATE LIMIT FUNCTIONS
# flask-limiter accepts callables — called on every request.
# Admins get a fixed very-high limit so they are never blocked.
# ─────────────────────────────────────────────

def _rl(key: str):
    """Return a callable that flask-limiter will call per request."""
    def _limit():
        try:
            if current_user.is_authenticated and current_user.is_admin:
                return '10000 per hour'   # effectively unlimited for admins
        except Exception:
            pass
        return _get_setting(key, RATE_LIMIT_DEFAULTS[key])
    return _limit


# Limiter — storage_uri='memory://' is fine for single-process dev.
# Swap for 'redis://localhost:6379' in production multi-worker deployments.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri='memory://'
)


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model, UserMixin):
    id            = db.Column(db.Integer,    primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean,    default=False)
    created_at    = db.Column(db.DateTime,   default=datetime.datetime.utcnow)

    def set_password(self, p):
        self.password_hash = generate_password_hash(
            p, method='pbkdf2:sha256:600000', salt_length=16)

    def check_password(self, p):
        return check_password_hash(self.password_hash, p)


class StudyData(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    userid                = db.Column(db.Integer, db.ForeignKey('user.id'),
                                      unique=True, nullable=False)
    extracted_json        = db.Column(db.Text)
    topic_status          = db.Column(db.Text)
    schedule_json         = db.Column(db.Text)
    pending_schedule_json = db.Column(db.Text)


class AppSettings(db.Model):
    """Key-value store for admin-configurable runtime settings."""
    key   = db.Column(db.String(64),  primary_key=True)
    value = db.Column(db.String(512), nullable=True)


class RequestLog(db.Model):
    id          = db.Column(db.Integer,  primary_key=True)
    userid      = db.Column(db.Integer,  db.ForeignKey('user.id'), nullable=True)
    method      = db.Column(db.String(10))
    path        = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    timestamp   = db.Column(db.DateTime, default=datetime.datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_today() -> str:
    """DD-MM-YYYY — respects admin test-date override."""
    val = _get_setting('test_today')
    return val if val else datetime.date.today().strftime('%d-%m-%Y')


def parse_dmy(s: str) -> datetime.date:
    d, m, y = s.strip().split('-')
    return datetime.date(int(y), int(m), int(d))


def get_max_tokens() -> int:
    val = _get_setting('max_tokens')
    try:
        return int(val) if val else DEFAULT_MAX_TOKENS
    except (ValueError, TypeError):
        return DEFAULT_MAX_TOKENS


def get_model_list() -> list:
    """Returns the active model list. Admin can override order/list via AppSettings."""
    val = _get_setting('model_list')
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception:
            pass
    return MODELS


def _get_study_data() -> StudyData | None:
    return StudyData.query.filter_by(userid=current_user.id).first()


def _require_owner(userid: int):
    if userid != current_user.id and not current_user.is_admin:
        abort(403)


def _delete_files(paths: list):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f'[WARN] Could not delete {p}: {e}')


def _render_error(code, title, message):
    return render_template('error.html', code=code, title=title, message=message), code


def _extract_meta(schedule: dict) -> tuple[dict, dict]:
    """
    Pops the '_meta' key from a schedule dict before it is saved to the DB.
    Returns (clean_schedule, meta).
    """
    meta  = schedule.pop('_meta', {})
    return schedule, meta


def _build_llm_notice(meta: dict) -> dict | None:
    """
    Builds a notice dict for the frontend if the primary model failed.
    Returns None if everything was normal.
    """
    if not meta.get('primary_failed'):
        return None
    return {
        'type':    'warning',
        'model':   meta.get('model_used', 'unknown'),
        'reasons': meta.get('failure_reasons', [])
    }


# ─────────────────────────────────────────────
# REQUEST LOGGING
# ─────────────────────────────────────────────

@app.after_request
def log_request(response):
    if request.path.startswith('/static') or request.path.startswith('/admin'):
        return response
    try:
        db.session.add(RequestLog(
            userid      = current_user.id if current_user.is_authenticated else None,
            method      = request.method,
            path        = request.path,
            status_code = response.status_code
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
    return response


# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def landing():
    return render_template('Landing.html')


@app.route('/register', methods=['POST'])
@limiter.limit(_rl('rl_register'))
def register():
    data     = request.get_json()
    username = (data.get('username') or '').strip()
    email    = (data.get('email')    or '').strip().lower()
    password =  data.get('password') or ''

    if not username or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user, remember=True)
    return jsonify({'redirect': url_for('upload_page')}), 201


@app.route('/login', methods=['POST'])
@limiter.limit(_rl('rl_login'))
def login():
    data       = request.get_json()
    identifier = (data.get('identifier') or '').strip()
    password   =  data.get('password')    or ''
    remember   =  data.get('remember',  False)

    if not identifier or not password:
        return jsonify({'error': 'Username/email and password are required'}), 400

    user = (User.query.filter_by(username=identifier).first() or
            User.query.filter_by(email=identifier.lower()).first())

    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401

    login_user(user, remember=remember, duration=datetime.timedelta(days=7))
    return jsonify({'redirect': url_for('upload_page')}), 200


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('landing'))


# ─────────────────────────────────────────────
# PAGE ROUTES
# ─────────────────────────────────────────────

@app.route('/upload_page')
@login_required
def upload_page():
    return render_template('Upload-page.html', username=current_user.username)


@app.route('/status')
@login_required
def status_page():
    user = _get_study_data()
    if not user:
        return redirect(url_for('upload_page'))
    data = json.loads(user.extracted_json)
    return render_template(
        'Status.html',
        data=data,
        exam_dates=data.get('Exam_dates', {}),
        username=current_user.username
    )


@app.route('/schedule_page')
@login_required
def schedule_page():
    return render_template('Schedule.html', username=current_user.username)


@app.route('/progress_page')
@login_required
def progress_page():
    user = _get_study_data()
    if not user or not user.schedule_json:
        return redirect(url_for('schedule_page'))

    today_str    = get_today()
    today_date   = parse_dmy(today_str)
    schedule     = json.loads(user.schedule_json)
    topic_status = json.loads(user.topic_status) if user.topic_status else {}

    past_schedule = {}
    for date_str, subjects in schedule.items():
        try:
            if parse_dmy(date_str) <= today_date:
                past_schedule[date_str] = subjects
        except ValueError:
            pass

    return render_template(
        'Progress.html',
        username      = current_user.username,
        today_str     = today_str,
        past_schedule = past_schedule,
        full_schedule = schedule,
        topic_status  = topic_status,
        userid        = current_user.id
    )


# ─────────────────────────────────────────────
# FILE UPLOAD
# ─────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_upload'))
def upload_files():
    file_paths = []
    try:
        files       = request.files.getlist('files')
        manual_text = request.form.get('manual_text', '').strip()

        if not files and not manual_text:
            return jsonify({'error': 'No files or text provided'}), 400

        for file in files:
            if not file.filename:
                continue
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in ('.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'):
                return jsonify({'error': f'Unsupported file type: {ext}'}), 400
            path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(path)
            file_paths.append(path)

        if not file_paths and not manual_text:
            return jsonify({'error': 'No valid files received'}), 400

        final_json = organize_with_llm(
            file_paths,
            manual_text=manual_text or None,
            today_str=get_today()
        )

        existing = _get_study_data()
        if existing:
            existing.extracted_json       = json.dumps(final_json)
            existing.topic_status         = None
            existing.schedule_json        = None
            existing.pending_schedule_json = None
        else:
            db.session.add(StudyData(
                userid=current_user.id,
                extracted_json=json.dumps(final_json)
            ))

        db.session.commit()
        return jsonify(final_json)

    except Exception as e:
        print('UPLOAD ERROR:', e)
        return jsonify({'error': str(e)}), 500
    finally:
        _delete_files(file_paths)


# ─────────────────────────────────────────────
# SAVE EXTRACTED / STATUS
# ─────────────────────────────────────────────

@app.route('/save_extracted/<int:userid>', methods=['POST'])
@login_required
def save_extracted(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404
    user.extracted_json = json.dumps(request.json)
    db.session.commit()
    return jsonify({'message': 'saved'})


@app.route('/submit_status/<int:userid>', methods=['POST'])
@login_required
def submit_status(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404
    user.topic_status = json.dumps(request.json)
    db.session.commit()
    return jsonify({'message': 'saved'})


# ─────────────────────────────────────────────
# GENERATE SCHEDULE  (initial, from Status page)
# ─────────────────────────────────────────────

@app.route('/generate_schedule/<int:userid>', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_generate'))
def generate(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user or not user.topic_status:
        return jsonify({'error': 'No topic status found'}), 400

    schedule = generate_schedule(
        json.loads(user.topic_status),
        today_str  = get_today(),
        max_tokens = get_max_tokens(),
        model_list = get_model_list()
    )

    schedule, meta = _extract_meta(schedule)
    user.schedule_json = json.dumps(schedule)
    db.session.commit()

    response_body = {'schedule': schedule}
    notice = _build_llm_notice(meta)
    if notice:
        response_body['notice'] = notice

    return jsonify(response_body)


# ─────────────────────────────────────────────
# GET SCHEDULE
# ─────────────────────────────────────────────

@app.route('/schedule/<int:userid>')
@login_required
def schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user or not user.schedule_json:
        return jsonify({'error': 'Schedule not found'}), 404
    return jsonify(json.loads(user.schedule_json))


# ─────────────────────────────────────────────
# PROGRESS ROUTES
# ─────────────────────────────────────────────

@app.route('/update_progress/<int:userid>', methods=['POST'])
@login_required
def update_progress(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404

    updated_subjects = (request.json or {}).get('Subjects', {})

    if user.topic_status:
        status = json.loads(user.topic_status)
    else:
        extracted = json.loads(user.extracted_json)
        status = {
            'Exam_dates': extracted.get('Exam_dates', {}),
            'Subjects':   {s: {t: '0' for t in topics}
                           for s, topics in extracted.get('Subjects', {}).items()},
            'study_days': extracted.get('study_days', {})
        }

    for subj, topics in updated_subjects.items():
        if subj in status['Subjects']:
            for topic, pct in topics.items():
                if topic in status['Subjects'][subj]:
                    status['Subjects'][subj][topic] = str(pct)

    user.topic_status = json.dumps(status)
    db.session.commit()
    return jsonify({'message': 'progress saved'})


@app.route('/regenerate_schedule/<int:userid>', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_generate'))
def regenerate_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user or not user.topic_status:
        return jsonify({'error': 'No topic status found'}), 400

    try:
        new_schedule = generate_schedule(
            json.loads(user.topic_status),
            today_str  = get_today(),
            max_tokens = get_max_tokens(),
            model_list = get_model_list()
        )
    except RuntimeError as e:
        # All models failed — return a structured error the frontend can display
        return jsonify({
            'error':   'All AI models failed to generate a schedule.',
            'details': str(e)   # full failure log, shown to admin via Progress.js
        }), 500

    new_schedule, meta = _extract_meta(new_schedule)
    user.pending_schedule_json = json.dumps(new_schedule)
    db.session.commit()

    response_body = {
        'old_schedule': json.loads(user.schedule_json),
        'new_schedule': new_schedule
    }
    notice = _build_llm_notice(meta)
    if notice:
        response_body['notice'] = notice

    return jsonify(response_body)


@app.route('/keep_schedule/<int:userid>', methods=['POST'])
@login_required
def keep_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404

    choice = (request.json or {}).get('choice', 'old')
    if choice == 'new' and user.pending_schedule_json:
        user.schedule_json = user.pending_schedule_json

    user.pending_schedule_json = None
    db.session.commit()
    return jsonify({'message': 'saved', 'choice': choice})


# ─────────────────────────────────────────────
# CURRENT USER
# ─────────────────────────────────────────────

@app.route('/me')
@login_required
def me():
    return jsonify({
        'id':       current_user.id,
        'username': current_user.username,
        'is_admin': current_user.is_admin
    })


# ─────────────────────────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        abort(403)

    fourteen_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=14)
    daily_logs = (
        db.session.query(
            db.func.date(RequestLog.timestamp).label('day'),
            db.func.count().label('count')
        )
        .filter(RequestLog.timestamp >= fourteen_days_ago)
        .group_by('day').order_by('day').all()
    )

    override_row = AppSettings.query.get('test_today')

    # Current rate limits (show actual DB value or default)
    current_limits = {
        k: (_get_setting(k) or v)
        for k, v in RATE_LIMIT_DEFAULTS.items()
    }

    return render_template(
        'Admin.html',
        total_users     = User.query.count(),
        total_requests  = RequestLog.query.count(),
        total_schedules = StudyData.query.filter(
                              StudyData.schedule_json.isnot(None)).count(),
        daily_logs      = daily_logs,
        recent_users    = User.query.order_by(User.created_at.desc()).limit(10).all(),
        test_today      = override_row.value if override_row else None,
        real_today      = datetime.date.today().strftime('%d-%m-%Y'),
        current_limits  = current_limits,
        default_limits  = RATE_LIMIT_DEFAULTS,
        max_tokens      = get_max_tokens(),
        model_list      = get_model_list(),
        default_models  = MODELS
    )


# ─────────────────────────────────────────────
# ADMIN: DATE OVERRIDE
# ─────────────────────────────────────────────

@app.route('/admin/set_date', methods=['POST'])
@login_required
def admin_set_date():
    if not current_user.is_admin:
        abort(403)
    date_val = (request.json or {}).get('date', '').strip()
    if not date_val:
        return jsonify({'error': 'No date provided'}), 400
    try:
        parse_dmy(date_val)
    except Exception:
        return jsonify({'error': 'Invalid date. Use DD-MM-YYYY'}), 400
    _set_setting('test_today', date_val)
    return jsonify({'message': f'Test date set to {date_val}'})


@app.route('/admin/reset_date', methods=['POST'])
@login_required
def admin_reset_date():
    if not current_user.is_admin:
        abort(403)
    row = AppSettings.query.get('test_today')
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({'message': 'Reset to real today'})


# ─────────────────────────────────────────────
# ADMIN: RATE LIMITS
# ─────────────────────────────────────────────

@app.route('/admin/set_rate_limits', methods=['POST'])
@login_required
def admin_set_rate_limits():
    if not current_user.is_admin:
        abort(403)

    data    = request.json or {}
    updated = {}

    for key in RATE_LIMIT_DEFAULTS:
        val = data.get(key, '').strip()
        if val:
            _set_setting(key, val)
            updated[key] = val

    if not updated:
        return jsonify({'error': 'No valid keys provided'}), 400

    return jsonify({'message': 'Rate limits updated', 'updated': updated})


@app.route('/admin/reset_rate_limits', methods=['POST'])
@login_required
def admin_reset_rate_limits():
    if not current_user.is_admin:
        abort(403)
    for key, val in RATE_LIMIT_DEFAULTS.items():
        _set_setting(key, val)
    return jsonify({'message': 'All rate limits reset to defaults'})


# ─────────────────────────────────────────────
# ADMIN: MAX TOKENS
# ─────────────────────────────────────────────

@app.route('/admin/set_max_tokens', methods=['POST'])
@login_required
def admin_set_max_tokens():
    if not current_user.is_admin:
        abort(403)
    val = (request.json or {}).get('max_tokens')
    try:
        tokens = int(val)
        if not (500 <= tokens <= 32000):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'max_tokens must be an integer between 500 and 32000'}), 400
    _set_setting('max_tokens', str(tokens))
    return jsonify({'message': f'max_tokens set to {tokens}'})


# ─────────────────────────────────────────────
# ADMIN: MODEL LIST
# ─────────────────────────────────────────────

@app.route('/admin/set_model_list', methods=['POST'])
@login_required
def admin_set_model_list():
    if not current_user.is_admin:
        abort(403)
    models = (request.json or {}).get('models')
    if not isinstance(models, list) or not models:
        return jsonify({'error': 'models must be a non-empty list'}), 400
    _set_setting('model_list', json.dumps(models))
    return jsonify({'message': 'Model list updated', 'models': models})


@app.route('/admin/reset_model_list', methods=['POST'])
@login_required
def admin_reset_model_list():
    if not current_user.is_admin:
        abort(403)
    row = AppSettings.query.get('model_list')
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({'message': 'Model list reset to defaults', 'models': MODELS})


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return _render_error(403, 'Access Denied',
                         "You don't have permission to view this page.")

@app.errorhandler(404)
def not_found(e):
    return _render_error(404, 'Page Not Found',
                         "The page you're looking for doesn't exist or has been moved.")

@app.errorhandler(413)
def too_large(e):
    return _render_error(413, 'File Too Large',
                         'Upload exceeds the 20 MB limit. Please compress and try again.')

@app.errorhandler(429)
def rate_limited(e):
    return _render_error(429, 'Slow Down',
                         "You've made too many requests. Please wait a moment and try again.")

@app.errorhandler(Exception)
def handle_exception(e):
    from flask_limiter.errors import RateLimitExceeded
    if isinstance(e, RateLimitExceeded):
        return _render_error(429, 'Slow Down',
                             "You've hit the rate limit. Please wait a moment.")
    raise e


# ─────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)