import os
import re
import json
import datetime
import secrets

from flask import Flask, request, jsonify, render_template, redirect, url_for, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from text_extractor import organize_with_llm, VISION_MODELS
from schedule_planner import generate_schedule, MODELS as SCHED_MODELS, DEFAULT_MAX_TOKENS


# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'change-me-before-deploy')
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///userdata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH']             = 20 * 1024 * 1024
app.config['CACHE_TYPE']                     = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT']          = 120

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}

db           = Cache_obj = None   # assigned below after models
login_manager = LoginManager()
limiter       = Limiter(key_func=get_remote_address, default_limits=[], storage_uri='memory://')
cache         = Cache()

db = SQLAlchemy(app)
limiter.init_app(app)
cache.init_app(app)
login_manager.init_app(app)
login_manager.login_view    = 'landing'
login_manager.login_message = ''


# ─────────────────────────────────────────────
# RATE LIMIT DEFAULTS
# ─────────────────────────────────────────────

RATE_LIMIT_DEFAULTS = {
    'rl_login':    '20 per hour',
    'rl_register': '10 per hour',
    'rl_upload':   '10 per hour',
    'rl_generate': '10 per hour',
}

DEFAULT_WORD_LIMIT = 2000


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model, UserMixin):
    id               = db.Column(db.Integer,    primary_key=True)
    username         = db.Column(db.String(80),  unique=True, nullable=False)
    email            = db.Column(db.String(120), unique=True, nullable=False)
    password_hash    = db.Column(db.String(256), nullable=False)
    is_admin         = db.Column(db.Boolean,    default=False)
    created_at       = db.Column(db.DateTime,   default=datetime.datetime.utcnow)
    # Increment to force-logout all sessions for this user
    session_version  = db.Column(db.Integer,    default=0, nullable=False)

    def set_password(self, p: str):
        self.password_hash = generate_password_hash(
            p, method='pbkdf2:sha256:600000', salt_length=16)

    def check_password(self, p: str) -> bool:
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
    key   = db.Column(db.String(64),  primary_key=True)
    value = db.Column(db.String(512), nullable=True)


class ActivityLog(db.Model):
    """Semantic action log — records meaningful user actions."""
    id        = db.Column(db.Integer,  primary_key=True)
    userid    = db.Column(db.Integer,  db.ForeignKey('user.id'), nullable=True)
    action    = db.Column(db.String(50))   # upload|generate|edit|regenerate|login|register
    detail    = db.Column(db.Text,     nullable=True)   # optional JSON string
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class RequestLog(db.Model):
    id          = db.Column(db.Integer,  primary_key=True)
    userid      = db.Column(db.Integer,  db.ForeignKey('user.id'), nullable=True)
    method      = db.Column(db.String(10))
    path        = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    timestamp   = db.Column(db.DateTime, default=datetime.datetime.utcnow)


@login_manager.user_loader
def load_user(user_id: str):
    user = User.query.get(int(user_id))
    if user is None:
        return None
    # Force-logout check: session version must match
    if session.get('session_version', 0) != (user.session_version or 0):
        return None
    return user


# ─────────────────────────────────────────────
# SETTINGS HELPERS
# ─────────────────────────────────────────────

def _get_setting(key: str, fallback=None):
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


def get_today() -> str:
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


def get_sched_model_list() -> list:
    val = _get_setting('sched_model_list')
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception:
            pass
    return SCHED_MODELS


def get_extract_model_list() -> list:
    val = _get_setting('extract_model_list')
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception:
            pass
    return VISION_MODELS


def get_word_limit() -> int:
    val = _get_setting('text_word_limit')
    try:
        return int(val) if val else DEFAULT_WORD_LIMIT
    except (ValueError, TypeError):
        return DEFAULT_WORD_LIMIT


# ─────────────────────────────────────────────
# SANITIZATION
# ─────────────────────────────────────────────

def _sanitize(text: str, max_words: int = None) -> str:
    """
    Strips HTML/script tags, removes control characters, normalizes whitespace.
    Optionally truncates to max_words. Safe to pass to LLM.
    """
    if not isinstance(text, str):
        return ''
    text = text.replace('\x00', '')
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)  # control chars (keep \t\n)
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)   # strip remaining HTML tags
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    if max_words:
        words = text.split()
        if len(words) > max_words:
            text = ' '.join(words[:max_words])
    return text


def _sanitize_field(s: str, max_len: int = 200) -> str:
    """Sanitize a short string field (username, topic name, subject name, etc.)."""
    if not isinstance(s, str):
        return ''
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return s[:max_len].strip()


def _sanitize_email(s: str) -> str:
    s = _sanitize_field(s, 120).lower()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', s):
        return ''
    return s


def _sanitize_topics_payload(payload: dict) -> dict:
    """Recursively sanitize all string values in the status/edit JSON payloads."""
    if not isinstance(payload, dict):
        return {}
    clean = {}
    for k, v in payload.items():
        clean_k = _sanitize_field(str(k))
        if isinstance(v, dict):
            clean[clean_k] = _sanitize_topics_payload(v)
        elif isinstance(v, str):
            clean[clean_k] = _sanitize_field(v)
        elif isinstance(v, (int, float)):
            clean[clean_k] = v
        else:
            clean[clean_k] = _sanitize_field(str(v))
    return clean


# ─────────────────────────────────────────────
# DYNAMIC RATE LIMITS
# ─────────────────────────────────────────────

def _rl(key: str):
    def _limit():
        try:
            if current_user.is_authenticated and current_user.is_admin:
                return '10000 per hour'
        except Exception:
            pass
        return _get_setting(key, RATE_LIMIT_DEFAULTS[key])
    return _limit


# ─────────────────────────────────────────────
# MISC HELPERS
# ─────────────────────────────────────────────

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


def _log_activity(action: str, detail: dict = None):
    try:
        db.session.add(ActivityLog(
            userid = current_user.id if current_user.is_authenticated else None,
            action = action,
            detail = json.dumps(detail) if detail else None
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _extract_meta(schedule: dict) -> tuple:
    meta = schedule.pop('_meta', {})
    return schedule, meta


def _build_llm_notice(meta: dict) -> dict | None:
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
    data     = request.get_json() or {}
    username = _sanitize_field(data.get('username', ''), 80)
    email    = _sanitize_email(data.get('email', ''))
    password = data.get('password', '')

    if not username or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if not re.match(r'^[A-Za-z0-9_\-]+$', username):
        return jsonify({'error': 'Username may only contain letters, numbers, _ and -'}), 400
    if not email:
        return jsonify({'error': 'Invalid email address'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(username=username, email=email, session_version=0)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session['session_version'] = 0
    login_user(user, remember=True)
    _log_activity('register', {'username': username})

    # Stay on landing page — do NOT redirect to upload_page
    return jsonify({'redirect': url_for('landing')}), 201


@app.route('/login', methods=['POST'])
@limiter.limit(_rl('rl_login'))
def login():
    data       = request.get_json() or {}
    identifier = _sanitize_field(data.get('identifier', ''), 120)
    password   = data.get('password', '')
    remember   = bool(data.get('remember', False))

    if not identifier or not password:
        return jsonify({'error': 'Username/email and password are required'}), 400

    user = (User.query.filter_by(username=identifier).first() or
            User.query.filter_by(email=identifier.lower()).first())

    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401

    session['session_version'] = user.session_version or 0
    login_user(user, remember=remember, duration=datetime.timedelta(days=7))
    _log_activity('login')

    # Stay on landing page
    return jsonify({'redirect': url_for('landing')}), 200


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('session_version', None)
    return redirect(url_for('landing'))


# ─────────────────────────────────────────────
# PAGE ROUTES
# ─────────────────────────────────────────────

@app.route('/upload_page')
@login_required
def upload_page():
    word_limit = get_word_limit()
    return render_template('Upload-page.html', word_limit=word_limit)


@app.route('/status')
@login_required
def status_page():
    user = _get_study_data()
    if not user:
        return redirect(url_for('upload_page'))
    data = json.loads(user.extracted_json)
    return render_template('Status.html', data=data)


@app.route('/schedule_page')
@login_required
def schedule_page():
    return render_template('Schedule.html')


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
        today_str     = today_str,
        past_schedule = past_schedule,
        full_schedule = schedule,
        topic_status  = topic_status,
        userid        = current_user.id,
        is_admin      = current_user.is_admin
    )


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    return render_template('terms.html')


@app.route('/contact')
def contact():
    return render_template('contact.html')


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
        manual_text = _sanitize(
            request.form.get('manual_text', ''),
            max_words=get_word_limit()
        )

        if not files and not manual_text:
            return jsonify({'error': 'Please upload files or paste text'}), 400

        for file in files:
            if not file.filename:
                continue
            filename = secure_filename(file.filename)   # prevents path traversal
            if not filename:
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                return jsonify({'error': f'Unsupported file type: {ext}'}), 400
            path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(path)
            file_paths.append(path)

        if not file_paths and not manual_text:
            return jsonify({'error': 'No valid files received'}), 400

        final_json = organize_with_llm(
            file_paths,
            manual_text  = manual_text or None,
            today_str    = get_today(),
            model_list   = get_extract_model_list()
        )

        existing = _get_study_data()
        if existing:
            existing.extracted_json        = json.dumps(final_json)
            existing.topic_status          = None
            existing.schedule_json         = None
            existing.pending_schedule_json = None
        else:
            db.session.add(StudyData(
                userid=current_user.id,
                extracted_json=json.dumps(final_json)
            ))

        db.session.commit()
        cache.delete(f'schedule_{current_user.id}')
        _log_activity('upload', {'files': len(file_paths), 'manual': bool(manual_text)})
        return jsonify(final_json)

    except Exception as e:
        print('UPLOAD ERROR:', e)
        return jsonify({'error': str(e)}), 500
    finally:
        _delete_files(file_paths)


# ─────────────────────────────────────────────
# SAVE EXTRACTED DATA  (page 2 edit)
# ─────────────────────────────────────────────

@app.route('/save_extracted/<int:userid>', methods=['POST'])
@login_required
def save_extracted(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404

    payload = _sanitize_topics_payload(request.json or {})
    user.extracted_json = json.dumps(payload)
    db.session.commit()
    _log_activity('edit')
    return jsonify({'message': 'saved'})


# ─────────────────────────────────────────────
# TOPIC STATUS
# ─────────────────────────────────────────────

@app.route('/submit_status/<int:userid>', methods=['POST'])
@login_required
def submit_status(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404
    payload = _sanitize_topics_payload(request.json or {})
    user.topic_status = json.dumps(payload)
    db.session.commit()
    return jsonify({'message': 'saved'})


# ─────────────────────────────────────────────
# GENERATE SCHEDULE
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
        model_list = get_sched_model_list()
    )

    schedule, meta = _extract_meta(schedule)
    user.schedule_json = json.dumps(schedule)
    db.session.commit()
    cache.delete(f'schedule_{current_user.id}')
    _log_activity('generate')

    resp = {'schedule': schedule}
    notice = _build_llm_notice(meta)
    if notice:
        resp['notice'] = notice
    return jsonify(resp)


# ─────────────────────────────────────────────
# GET SCHEDULE  (cached)
# ─────────────────────────────────────────────

@app.route('/schedule/<int:userid>')
@login_required
def schedule(userid):
    _require_owner(userid)
    cache_key = f'schedule_{userid}'
    cached    = cache.get(cache_key)
    if cached:
        return jsonify(cached)
    user = _get_study_data()
    if not user or not user.schedule_json:
        return jsonify({'error': 'Schedule not found'}), 404
    data = json.loads(user.schedule_json)
    cache.set(cache_key, data, timeout=120)
    return jsonify(data)


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

    updated_subjects = _sanitize_topics_payload(
        (request.json or {}).get('Subjects', {})
    )

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
                    # Only allow numeric string 0-100
                    try:
                        val = max(0, min(100, int(pct)))
                        status['Subjects'][subj][topic] = str(val)
                    except (ValueError, TypeError):
                        pass

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
            model_list = get_sched_model_list()
        )
    except RuntimeError as e:
        return jsonify({'error': 'All AI models failed.', 'details': str(e)}), 500

    new_schedule, meta = _extract_meta(new_schedule)
    user.pending_schedule_json = json.dumps(new_schedule)
    db.session.commit()
    _log_activity('regenerate')

    resp = {
        'old_schedule': json.loads(user.schedule_json),
        'new_schedule': new_schedule
    }
    notice = _build_llm_notice(meta)
    if notice:
        resp['notice'] = notice
    return jsonify(resp)


@app.route('/keep_schedule/<int:userid>', methods=['POST'])
@login_required
def keep_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404

    choice = _sanitize_field((request.json or {}).get('choice', 'old'), 10)
    if choice == 'new' and user.pending_schedule_json:
        user.schedule_json = user.pending_schedule_json

    user.pending_schedule_json = None
    db.session.commit()
    cache.delete(f'schedule_{current_user.id}')
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
# ADMIN: DASHBOARD
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

    recent_activity = (
        db.session.query(ActivityLog, User.username)
        .outerjoin(User, User.id == ActivityLog.userid)
        .order_by(ActivityLog.timestamp.desc())
        .limit(50).all()
    )

    return render_template(
        'Admin.html',
        total_users      = User.query.count(),
        total_requests   = RequestLog.query.count(),
        total_schedules  = StudyData.query.filter(StudyData.schedule_json.isnot(None)).count(),
        daily_logs       = daily_logs,
        recent_activity  = recent_activity,
        test_today       = _get_setting('test_today'),
        real_today       = datetime.date.today().strftime('%d-%m-%Y'),
        current_limits   = {k: (_get_setting(k) or v) for k, v in RATE_LIMIT_DEFAULTS.items()},
        default_limits   = RATE_LIMIT_DEFAULTS,
        max_tokens       = get_max_tokens(),
        sched_models     = get_sched_model_list(),
        extract_models   = get_extract_model_list(),
        default_sched_models   = SCHED_MODELS,
        default_extract_models = VISION_MODELS,
        word_limit       = get_word_limit(),
        default_word_limit = DEFAULT_WORD_LIMIT
    )


# ─────────────────────────────────────────────
# ADMIN: USER MANAGEMENT API
# ─────────────────────────────────────────────

@app.route('/admin/api/users')
@login_required
def admin_list_users():
    if not current_user.is_admin:
        abort(403)
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify([{
        'id':         u.id,
        'username':   u.username,
        'email':      u.email,
        'is_admin':   u.is_admin,
        'created_at': u.created_at.strftime('%d %b %Y %H:%M'),
        'password_hash': u.password_hash
    } for u in users])


@app.route('/admin/api/users/<int:uid>/role', methods=['POST'])
@login_required
def admin_toggle_role(uid: int):
    if not current_user.is_admin:
        abort(403)
    if uid == current_user.id:
        return jsonify({'error': 'Cannot change your own admin status'}), 400
    user = User.query.get_or_404(uid)
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify({'is_admin': user.is_admin})


@app.route('/admin/api/users/<int:uid>/update', methods=['POST'])
@login_required
def admin_update_user(uid: int):
    if not current_user.is_admin:
        abort(403)
    user = User.query.get_or_404(uid)
    data = request.get_json() or {}

    if 'username' in data:
        new_username = _sanitize_field(data['username'], 80)
        if not new_username or len(new_username) < 3:
            return jsonify({'error': 'Username too short'}), 400
        if not re.match(r'^[A-Za-z0-9_\-]+$', new_username):
            return jsonify({'error': 'Username contains invalid characters'}), 400
        existing = User.query.filter_by(username=new_username).first()
        if existing and existing.id != uid:
            return jsonify({'error': 'Username already taken'}), 409
        user.username = new_username

    if 'email' in data:
        new_email = _sanitize_email(data['email'])
        if not new_email:
            return jsonify({'error': 'Invalid email'}), 400
        existing = User.query.filter_by(email=new_email).first()
        if existing and existing.id != uid:
            return jsonify({'error': 'Email already registered'}), 409
        user.email = new_email

    if 'password' in data:
        pw = data['password']
        if len(pw) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        user.set_password(pw)
        # Invalidate all existing sessions for this user
        user.session_version = (user.session_version or 0) + 1

    db.session.commit()
    return jsonify({'message': 'User updated'})


@app.route('/admin/api/users/<int:uid>/force_logout', methods=['POST'])
@login_required
def admin_force_logout(uid: int):
    if not current_user.is_admin:
        abort(403)
    if uid == current_user.id:
        return jsonify({'error': 'Cannot force-logout yourself'}), 400
    user = User.query.get_or_404(uid)
    user.session_version = (user.session_version or 0) + 1
    db.session.commit()
    return jsonify({'message': f'{user.username} will be logged out on next request'})


@app.route('/admin/api/users/<int:uid>', methods=['DELETE'])
@login_required
def admin_delete_user(uid: int):
    if not current_user.is_admin:
        abort(403)
    if uid == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    user = User.query.get_or_404(uid)
    # Clean up related data
    StudyData.query.filter_by(userid=uid).delete()
    ActivityLog.query.filter_by(userid=uid).delete()
    RequestLog.query.filter_by(userid=uid).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'message': f'User {uid} deleted'})


# ─────────────────────────────────────────────
# ADMIN: SETTINGS API
# ─────────────────────────────────────────────

@app.route('/admin/set_date', methods=['POST'])
@login_required
def admin_set_date():
    if not current_user.is_admin: abort(403)
    date_val = _sanitize_field((request.json or {}).get('date', ''), 20)
    try: parse_dmy(date_val)
    except Exception: return jsonify({'error': 'Invalid date. Use DD-MM-YYYY'}), 400
    _set_setting('test_today', date_val)
    return jsonify({'message': f'Test date set to {date_val}'})


@app.route('/admin/reset_date', methods=['POST'])
@login_required
def admin_reset_date():
    if not current_user.is_admin: abort(403)
    row = AppSettings.query.get('test_today')
    if row: db.session.delete(row); db.session.commit()
    return jsonify({'message': 'Reset to real today'})


@app.route('/admin/set_rate_limits', methods=['POST'])
@login_required
def admin_set_rate_limits():
    if not current_user.is_admin: abort(403)
    data, updated = request.json or {}, {}
    for key in RATE_LIMIT_DEFAULTS:
        val = _sanitize_field(data.get(key, ''), 50)
        if val: _set_setting(key, val); updated[key] = val
    if not updated: return jsonify({'error': 'No valid keys'}), 400
    return jsonify({'message': 'Updated', 'updated': updated})


@app.route('/admin/reset_rate_limits', methods=['POST'])
@login_required
def admin_reset_rate_limits():
    if not current_user.is_admin: abort(403)
    for k, v in RATE_LIMIT_DEFAULTS.items(): _set_setting(k, v)
    return jsonify({'message': 'Reset to defaults'})


@app.route('/admin/set_max_tokens', methods=['POST'])
@login_required
def admin_set_max_tokens():
    if not current_user.is_admin: abort(403)
    try:
        tokens = int((request.json or {}).get('max_tokens', 0))
        if not (500 <= tokens <= 32000): raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'Must be integer 500–32000'}), 400
    _set_setting('max_tokens', str(tokens))
    return jsonify({'message': f'max_tokens set to {tokens}'})


@app.route('/admin/set_word_limit', methods=['POST'])
@login_required
def admin_set_word_limit():
    if not current_user.is_admin: abort(403)
    try:
        limit = int((request.json or {}).get('word_limit', 0))
        if not (100 <= limit <= 10000): raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'Must be integer 100–10000'}), 400
    _set_setting('text_word_limit', str(limit))
    return jsonify({'message': f'Word limit set to {limit}'})


@app.route('/admin/set_model_list', methods=['POST'])
@login_required
def admin_set_model_list():
    if not current_user.is_admin: abort(403)
    data     = request.json or {}
    list_key = _sanitize_field(data.get('list_key', ''), 30)  # sched_model_list or extract_model_list
    models   = data.get('models')
    if list_key not in ('sched_model_list', 'extract_model_list'):
        return jsonify({'error': 'Invalid list_key'}), 400
    if not isinstance(models, list) or not models:
        return jsonify({'error': 'models must be a non-empty list'}), 400
    models = [_sanitize_field(m, 100) for m in models if m]
    _set_setting(list_key, json.dumps(models))
    return jsonify({'message': 'Model list updated', 'models': models})


@app.route('/admin/reset_model_list', methods=['POST'])
@login_required
def admin_reset_model_list():
    if not current_user.is_admin: abort(403)
    list_key = _sanitize_field((request.json or {}).get('list_key', ''), 30)
    if list_key not in ('sched_model_list', 'extract_model_list'):
        return jsonify({'error': 'Invalid list_key'}), 400
    row = AppSettings.query.get(list_key)
    if row: db.session.delete(row); db.session.commit()
    defaults = SCHED_MODELS if list_key == 'sched_model_list' else VISION_MODELS
    return jsonify({'message': 'Reset to defaults', 'models': defaults})


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return _render_error(403, 'Access Denied', "You don't have permission to view this page.")

@app.errorhandler(404)
def not_found(e):
    return _render_error(404, 'Page Not Found', "The page you're looking for doesn't exist.")

@app.errorhandler(413)
def too_large(e):
    return _render_error(413, 'File Too Large', 'Upload exceeds the 20 MB limit.')

@app.errorhandler(429)
def rate_limited(e):
    return _render_error(429, 'Slow Down', "You've made too many requests. Please wait.")

@app.errorhandler(Exception)
def handle_exception(e):
    from flask_limiter.errors import RateLimitExceeded
    if isinstance(e, RateLimitExceeded):
        return _render_error(429, 'Slow Down', "Rate limit hit. Please wait a moment.")
    raise e


# ─────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)