import os
import re
import json
import datetime
import urllib.parse
import secrets as _secrets

from flask import Flask, request, jsonify, render_template, redirect, url_for, abort, session, Response, stream_with_context
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
from teacher import (
    get_initial_message, get_reply, get_quiz,
    stream_reply, stream_quiz, TEACHER_MODELS
)


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

db      = SQLAlchemy(app)
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri='memory://')
cache   = Cache()
login_manager = LoginManager()

limiter.init_app(app)
cache.init_app(app)
login_manager.init_app(app)
login_manager.login_view    = 'landing'
login_manager.login_message = ''

RATE_LIMIT_DEFAULTS = {
    'rl_login':    '20 per hour',
    'rl_register': '10 per hour',
    'rl_upload':   '10 per hour',
    'rl_generate': '10 per hour',
    'rl_chat':     '60 per hour',
}

DEFAULT_WORD_LIMIT = 2000


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model, UserMixin):
    id              = db.Column(db.Integer,    primary_key=True)
    username        = db.Column(db.String(80),  unique=True, nullable=False)
    email           = db.Column(db.String(120), unique=True, nullable=False)
    password_hash   = db.Column(db.String(256), nullable=False)
    is_admin        = db.Column(db.Boolean,    default=False)
    created_at      = db.Column(db.DateTime,   default=datetime.datetime.utcnow)
    username_changed_at = db.Column(db.DateTime, nullable=True)
    session_version = db.Column(db.Integer,    default=0, nullable=False)
    course          = db.Column(db.String(50),  nullable=True)   # e.g. "B.Tech Computer Science"

    def set_password(self, p): self.password_hash = generate_password_hash(
        p, method='pbkdf2:sha256:600000', salt_length=16)
    def check_password(self, p): return check_password_hash(self.password_hash, p)


class StudyData(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    userid                = db.Column(db.Integer, db.ForeignKey('user.id'),
                                      unique=True, nullable=False)
    extracted_json        = db.Column(db.Text)
    topic_status          = db.Column(db.Text)
    schedule_json         = db.Column(db.Text)
    pending_schedule_json = db.Column(db.Text)


class Chat(db.Model):
    """One chat per user+subject+topic combination. Continues across sessions."""
    id         = db.Column(db.Integer,   primary_key=True)
    userid     = db.Column(db.Integer,   db.ForeignKey('user.id'), nullable=False)
    subject    = db.Column(db.String(200), nullable=False)
    topic      = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime,  default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime,  default=datetime.datetime.utcnow,
                            onupdate=datetime.datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('userid', 'subject', 'topic',
                                          name='uq_user_subject_topic'),)


class Message(db.Model):
    """Individual message in a chat. role = 'user' | 'assistant'."""
    id         = db.Column(db.Integer,  primary_key=True)
    chat_id    = db.Column(db.Integer,  db.ForeignKey('chat.id'), nullable=False)
    role       = db.Column(db.String(10), nullable=False)
    content    = db.Column(db.Text,     nullable=False)
    timestamp  = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class PasswordResetOTP(db.Model):
    """
    OTP for password reset / email verification.
    HARDCODED OTP: 1234 - replace send_otp_email() with real SMTP later.
    """
    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(120), nullable=False)
    otp_code   = db.Column(db.String(10), nullable=False, default='1234')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)


class ContactMessage(db.Model):
    """Stores contact form submissions until a real email system is wired up."""
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100))
    email      = db.Column(db.String(120))
    subject    = db.Column(db.String(200))
    message    = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class AppSettings(db.Model):
    key   = db.Column(db.String(64),  primary_key=True)
    value = db.Column(db.String(512), nullable=True)


class ActivityLog(db.Model):
    id        = db.Column(db.Integer,  primary_key=True)
    userid    = db.Column(db.Integer,  db.ForeignKey('user.id'), nullable=True)
    action    = db.Column(db.String(50))
    detail    = db.Column(db.Text,     nullable=True)
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
    if not user: return None
    if session.get('session_version', 0) != (user.session_version or 0): return None
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
    if row: row.value = value
    else:   db.session.add(AppSettings(key=key, value=value))
    db.session.commit()

def get_today() -> str:
    val = _get_setting('test_today')
    return val if val else datetime.date.today().strftime('%d-%m-%Y')

def parse_dmy(s: str) -> datetime.date:
    d, m, y = s.strip().split('-')
    return datetime.date(int(y), int(m), int(d))

def get_max_tokens() -> int:
    try: return int(_get_setting('max_tokens') or DEFAULT_MAX_TOKENS)
    except: return DEFAULT_MAX_TOKENS

def get_word_limit() -> int:
    try: return int(_get_setting('text_word_limit') or DEFAULT_WORD_LIMIT)
    except: return DEFAULT_WORD_LIMIT

def get_sched_model_list() -> list:
    return _parse_model_list('sched_model_list', SCHED_MODELS)

def get_extract_model_list() -> list:
    return _parse_model_list('extract_model_list', VISION_MODELS)

def get_teacher_model_list() -> list:
    return _parse_model_list('teacher_model_list', TEACHER_MODELS)

def _parse_model_list(key: str, default: list) -> list:
    val = _get_setting(key)
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed: return parsed
        except: pass
    return default

def get_use_chinese() -> bool:
    return _get_setting('use_chinese_prompts', 'false').lower() == 'true'


# ─────────────────────────────────────────────
# SANITIZATION
# ─────────────────────────────────────────────

def _sanitize(text: str, max_words: int = None) -> str:
    if not isinstance(text, str): return ''
    text = text.replace('\x00', '')
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if max_words:
        words = text.split()
        if len(words) > max_words: text = ' '.join(words[:max_words])
    return text

def _sanitize_field(s: str, max_len: int = 200) -> str:
    if not isinstance(s, str): return ''
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return s[:max_len].strip()

def _sanitize_email(s: str) -> str:
    s = _sanitize_field(s, 120).lower()
    return s if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', s) else ''

def _sanitize_topics_payload(payload: dict) -> dict:
    """Recursively sanitize all string keys/values in topics JSON."""
    if not isinstance(payload, dict): return {}
    clean = {}
    for k, v in payload.items():
        ck = _sanitize_field(str(k))
        if isinstance(v, dict):
            clean[ck] = _sanitize_topics_payload(v)
        elif isinstance(v, list):
            clean[ck] = [_sanitize_field(str(i)) for i in v if str(i).strip()]
        elif isinstance(v, str):
            clean[ck] = _sanitize_field(v)
        elif isinstance(v, (int, float)):
            clean[ck] = v
        else:
            clean[ck] = _sanitize_field(str(v))
    return clean


# ─────────────────────────────────────────────
# DYNAMIC RATE LIMITS
# ─────────────────────────────────────────────

def _rl(key: str):
    def _limit():
        try:
            if current_user.is_authenticated and current_user.is_admin:
                return '10000 per hour'
        except: pass
        return _get_setting(key, RATE_LIMIT_DEFAULTS[key])
    return _limit


# ─────────────────────────────────────────────
# MISC HELPERS
# ─────────────────────────────────────────────

def _get_study_data() -> StudyData | None:
    return StudyData.query.filter_by(userid=current_user.id).first()

def _require_owner(userid: int):
    if userid != current_user.id and not current_user.is_admin: abort(403)

def _delete_files(paths: list):
    for p in paths:
        try:
            if os.path.exists(p): os.remove(p)
        except Exception as e: print(f'[WARN] {p}: {e}')

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
    except: db.session.rollback()

def _extract_meta(schedule: dict) -> tuple:
    meta = schedule.pop('_meta', {})
    return schedule, meta

def _build_llm_notice(meta: dict) -> dict | None:
    if not meta.get('primary_failed'): return None
    return {'type': 'warning', 'model': meta.get('model_used', 'unknown'),
            'reasons': meta.get('failure_reasons', [])}

def _get_today_slot(schedule: dict, subject: str, topic: str, today_str: str) -> dict:
    """Return today's schedule slot for a given subject+topic, or empty dict."""
    day = schedule.get(today_str, {})
    return day.get(subject, {}).get(topic, {})


HARDCODED_OTP = '1234'


def _send_otp_email(email: str, otp: str):
    """
    Placeholder for real email delivery.
    Replace this later with SMTP / provider integration.
    """
    print(f'[OTP] Would send OTP {otp} to {email} - email not configured yet')


def _create_otp(email: str) -> PasswordResetOTP:
    PasswordResetOTP.query.filter_by(email=email, used=False).update({'used': True})
    db.session.commit()

    otp = PasswordResetOTP(
        email=email,
        otp_code=HARDCODED_OTP,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
    )
    db.session.add(otp)
    db.session.commit()
    _send_otp_email(email, HARDCODED_OTP)
    return otp


def _verify_otp(email: str, code: str) -> bool:
    now = datetime.datetime.utcnow()
    rec = (PasswordResetOTP.query
           .filter_by(email=email, otp_code=code, used=False)
           .filter(PasswordResetOTP.expires_at > now)
           .first())
    if not rec:
        return False
    rec.used = True
    db.session.commit()
    return True


# ─────────────────────────────────────────────
# REQUEST LOGGING
# ─────────────────────────────────────────────

@app.after_request
def log_request(response):
    if request.path.startswith('/static') or request.path.startswith('/admin'): return response
    try:
        db.session.add(RequestLog(
            userid=current_user.id if current_user.is_authenticated else None,
            method=request.method, path=request.path, status_code=response.status_code
        ))
        db.session.commit()
    except: db.session.rollback()
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
    course   = _sanitize_field(data.get('course', ''), 50)   # new field

    if not username or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if not re.match(r'^[A-Za-z0-9_\-]+$', username):
        return jsonify({'error': 'Username: letters, numbers, _ and - only'}), 400
    if not email:
        return jsonify({'error': 'Invalid email address'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(username=username, email=email, course=course or None, session_version=0)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session['session_version'] = 0
    login_user(user, remember=True)
    _log_activity('register', {'username': username})
    return jsonify({'redirect': url_for('landing')}), 201


@app.route('/login', methods=['POST'])
@limiter.limit(_rl('rl_login'))
def login():
    data       = request.get_json() or {}
    identifier = _sanitize_field(data.get('identifier', ''), 120)
    password   = data.get('password', '')
    remember   = bool(data.get('remember', False))
    if not identifier or not password:
        return jsonify({'error': 'Username/email and password required'}), 400
    user = (User.query.filter_by(username=identifier).first() or
            User.query.filter_by(email=identifier.lower()).first())
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    session['session_version'] = user.session_version or 0
    login_user(user, remember=remember, duration=datetime.timedelta(days=7))
    _log_activity('login')
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
    return render_template('Upload-page.html', word_limit=get_word_limit())


@app.route('/status')
@login_required
def status_page():
    user = _get_study_data()
    if not user: return redirect(url_for('upload_page'))
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
    if not user or not user.schedule_json: return redirect(url_for('schedule_page'))
    today_str    = get_today()
    today_date   = parse_dmy(today_str)
    schedule     = json.loads(user.schedule_json)
    topic_status = json.loads(user.topic_status) if user.topic_status else {}
    past_schedule = {d: s for d, s in schedule.items()
                     if _safe_parse_dmy(d) and _safe_parse_dmy(d) <= today_date}
    return render_template('Progress.html',
        today_str=today_str, past_schedule=past_schedule,
        full_schedule=schedule, topic_status=topic_status,
        userid=current_user.id, is_admin=current_user.is_admin)


def _safe_parse_dmy(s):
    try: return parse_dmy(s)
    except: return None


@app.route('/study/<subject>/<topic>')
@login_required
def study_page(subject: str, topic: str):
    subject = _sanitize_field(urllib.parse.unquote(subject), 200)
    topic   = _sanitize_field(urllib.parse.unquote(topic),   200)

    user_data = _get_study_data()
    if not user_data: return redirect(url_for('upload_page'))

    schedule  = json.loads(user_data.schedule_json) if user_data.schedule_json else {}
    today_str = get_today()
    slot      = _get_today_slot(schedule, subject, topic, today_str)
    hours     = slot.get('hours')
    subtopics = slot.get('subtopics', [])

    # If not in today's schedule, fall back to the topic's stored subtopics
    if not subtopics and user_data.extracted_json:
        extracted = json.loads(user_data.extracted_json)
        topic_data = (extracted.get('Subjects', {})
                               .get(subject, {})
                               .get(topic, {}))
        if isinstance(topic_data, dict):
            subtopics = topic_data.get('subtopics', [])

    # Get or create the chat for this user+subject+topic
    chat = Chat.query.filter_by(
        userid=current_user.id, subject=subject, topic=topic).first()
    if not chat:
        chat = Chat(userid=current_user.id, subject=subject, topic=topic)
        db.session.add(chat)
        db.session.commit()

    return render_template('Study.html',
        subject    = subject,
        topic      = topic,
        subtopics  = subtopics,
        hours      = hours,
        chat_id    = chat.id,
        userid     = current_user.id,
        today_str  = today_str
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


@app.route('/settings')
@login_required
def settings_page():
    can_change_username = True
    days_until_change = 0

    if current_user.username_changed_at:
        delta = datetime.datetime.utcnow() - current_user.username_changed_at
        if delta.days < 14:
            can_change_username = False
            days_until_change = 14 - delta.days

    return render_template(
        'Settings.html',
        user=current_user,
        can_change_username=can_change_username,
        days_until_change=days_until_change
    )


@app.route('/settings/update', methods=['POST'])
@login_required
@limiter.limit('10 per hour')
def settings_update():
    data = request.get_json() or {}
    action = _sanitize_field(data.get('action', ''), 30)

    if action == 'update_course':
        course = _sanitize_field(data.get('course', ''), 50)
        current_user.course = course or None
        db.session.commit()
        return jsonify({'message': 'Course updated'})

    if action == 'update_username':
        new_username = _sanitize_field(data.get('username', ''), 80)
        if len(new_username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters'}), 400
        if not re.match(r'^[A-Za-z0-9_\-]+$', new_username):
            return jsonify({'error': 'Username: letters, numbers, _ and - only'}), 400
        existing_user = User.query.filter_by(username=new_username).first()
        if existing_user and existing_user.id != current_user.id:
            return jsonify({'error': 'Username already taken'}), 409

        if current_user.username_changed_at:
            delta = datetime.datetime.utcnow() - current_user.username_changed_at
            if delta.days < 14:
                return jsonify({
                    'error': f'You can change your username again in {14 - delta.days} days'
                }), 429

        current_user.username = new_username
        current_user.username_changed_at = datetime.datetime.utcnow()
        db.session.commit()
        return jsonify({'message': 'Username updated', 'new_username': new_username})

    if action == 'change_password':
        old_pw = data.get('old_password', '')
        new_pw = data.get('new_password', '')
        if not current_user.check_password(old_pw):
            return jsonify({'error': 'Current password is incorrect'}), 401
        if len(new_pw) < 8:
            return jsonify({'error': 'New password must be at least 8 characters'}), 400
        current_user.set_password(new_pw)
        current_user.session_version = (current_user.session_version or 0) + 1
        db.session.commit()
        session['session_version'] = current_user.session_version
        return jsonify({'message': 'Password changed successfully'})

    return jsonify({'error': 'Unknown action'}), 400


@app.route('/forgot_password', methods=['POST'])
@limiter.limit('5 per hour')
def forgot_password():
    email = _sanitize_email((request.get_json() or {}).get('email', ''))
    if not email:
        return jsonify({'error': 'Invalid email'}), 400

    user = User.query.filter_by(email=email).first()
    if user:
        _create_otp(email)

    return jsonify({'message': 'If that email is registered, an OTP has been sent.'})


@app.route('/verify_reset_otp', methods=['POST'])
@limiter.limit('10 per hour')
def verify_reset_otp():
    data = request.get_json() or {}
    email = _sanitize_email(data.get('email', ''))
    code = _sanitize_field(data.get('otp', ''), 10)

    if not _verify_otp(email, code):
        return jsonify({'error': 'Invalid or expired OTP'}), 401

    token = _secrets.token_urlsafe(32)
    session[f'reset_token_{email}'] = token
    return jsonify({'token': token, 'email': email})


@app.route('/reset_password', methods=['POST'])
@limiter.limit('5 per hour')
def reset_password():
    data = request.get_json() or {}
    email = _sanitize_email(data.get('email', ''))
    token = data.get('token', '')
    new_pw = data.get('password', '')

    expected = session.get(f'reset_token_{email}')
    if not expected or expected != token:
        return jsonify({'error': 'Invalid reset token'}), 401

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if len(new_pw) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    user.set_password(new_pw)
    user.session_version = (user.session_version or 0) + 1
    db.session.commit()

    session.pop(f'reset_token_{email}', None)
    return jsonify({'message': 'Password reset successfully'})


@app.route('/contact/submit', methods=['POST'])
@limiter.limit('3 per hour')
def contact_submit():
    data = request.get_json() or {}
    msg = ContactMessage(
        name=_sanitize_field(data.get('name', ''), 100),
        email=_sanitize_email(data.get('email', '')),
        subject=_sanitize_field(data.get('subject', ''), 200),
        message=_sanitize(data.get('message', ''), max_words=500)
    )
    if not msg.email:
        return jsonify({'error': 'Invalid email'}), 400
    if not msg.message:
        return jsonify({'error': 'Message is required'}), 400

    db.session.add(msg)
    db.session.commit()
    return jsonify({'message': "Thanks! We'll get back to you within 24 hours."})


# ─────────────────────────────────────────────
# STUDY / CHAT API
# ─────────────────────────────────────────────

def _get_chat_or_403(chat_id: int) -> Chat:
    chat = Chat.query.get_or_404(chat_id)
    if chat.userid != current_user.id and not current_user.is_admin:
        abort(403)
    return chat

def _get_chat_history(chat_id: int) -> list:
    """Return list of {role, content} dicts in chronological order."""
    msgs = (Message.query
            .filter_by(chat_id=chat_id)
            .order_by(Message.timestamp)
            .all())
    return [{'role': m.role, 'content': m.content} for m in msgs]

def _save_message(chat_id: int, role: str, content: str) -> Message:
    msg = Message(chat_id=chat_id, role=role, content=content)
    db.session.add(msg)
    # Update chat.updated_at
    chat = Chat.query.get(chat_id)
    if chat: chat.updated_at = datetime.datetime.utcnow()
    db.session.commit()
    return msg

def _get_topic_context(chat: Chat) -> tuple:
    """Get subtopics, hours, course for a chat from stored data."""
    user      = User.query.get(chat.userid)
    course    = user.course if user else None
    user_data = StudyData.query.filter_by(userid=chat.userid).first()
    if not user_data: return [], None, course

    schedule  = json.loads(user_data.schedule_json) if user_data.schedule_json else {}
    today_str = get_today()
    slot      = _get_today_slot(schedule, chat.subject, chat.topic, today_str)
    hours     = slot.get('hours')
    subtopics = slot.get('subtopics', [])

    if not subtopics and user_data.extracted_json:
        extracted = json.loads(user_data.extracted_json)
        tdata = (extracted.get('Subjects', {})
                          .get(chat.subject, {})
                          .get(chat.topic, {}))
        if isinstance(tdata, dict):
            subtopics = tdata.get('subtopics', [])

    return subtopics, hours, course


@app.route('/api/chat/<int:chat_id>/history')
@login_required
def chat_history(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    msgs = (Message.query
            .filter_by(chat_id=chat_id)
            .order_by(Message.timestamp)
            .all())
    return jsonify([{
        'id':        m.id,
        'role':      m.role,
        'content':   m.content,
        'timestamp': m.timestamp.isoformat()
    } for m in msgs])


@app.route('/api/chat/<int:chat_id>/start', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_start(chat_id: int):
    """
    Called by JS when a chat has no messages yet.
    Generates the first proactive teaching message from the AI.
    """
    chat = _get_chat_or_403(chat_id)

    # If already has messages, just return the existing history
    existing_count = Message.query.filter_by(chat_id=chat_id).count()
    if existing_count > 0:
        return jsonify({'already_started': True})

    subtopics, hours, course = _get_topic_context(chat)

    try:
        content, model_used, failures = get_initial_message(
            course     = course or '',
            subject    = chat.subject,
            topic      = chat.topic,
            subtopics  = subtopics,
            hours      = hours,
            model_list = get_teacher_model_list(),
            use_chinese= get_use_chinese()
        )
        _save_message(chat_id, 'assistant', content)
        _log_activity('study_start', {'subject': chat.subject, 'topic': chat.topic})

        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'model': model_used, 'reasons': failures}
        return jsonify(resp)

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat/<int:chat_id>/send', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_send(chat_id: int):
    chat = _get_chat_or_403(chat_id)

    user_message = _sanitize(
        (request.json or {}).get('message', ''), max_words=500)
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    # Save user message
    _save_message(chat_id, 'user', user_message)

    # Get full history for sliding window
    history    = _get_chat_history(chat_id)
    # Remove the message we just saved from history (it'll be added by get_reply)
    history    = history[:-1]

    subtopics, hours, course = _get_topic_context(chat)

    try:
        content, model_used, failures = get_reply(
            history     = history,
            new_message = user_message,
            course      = course or '',
            subject     = chat.subject,
            topic       = chat.topic,
            subtopics   = subtopics,
            hours       = hours,
            model_list  = get_teacher_model_list(),
            use_chinese = get_use_chinese()
        )
        _save_message(chat_id, 'assistant', content)

        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'model': model_used, 'reasons': failures}
        return jsonify(resp)

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat/<int:chat_id>/quiz', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_quiz(chat_id: int):
    chat    = _get_chat_or_403(chat_id)
    history = _get_chat_history(chat_id)

    if len(history) < 2:
        return jsonify({'error': 'Study a bit first before taking a quiz!'}), 400

    subtopics, hours, course = _get_topic_context(chat)

    try:
        content, model_used, failures = get_quiz(
            history     = history,
            course      = course or '',
            subject     = chat.subject,
            topic       = chat.topic,
            subtopics   = subtopics,
            hours       = hours,
            model_list  = get_teacher_model_list(),
            use_chinese = get_use_chinese()
        )
        _save_message(chat_id, 'assistant', content)

        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'model': model_used, 'reasons': failures}
        return jsonify(resp)

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# FILE UPLOAD
# ─────────────────────────────────────────────

@app.route('/api/chat/<int:chat_id>/message/<int:msg_id>', methods=['PATCH'])
@login_required
def edit_message(chat_id: int, msg_id: int):
    """Edit a user message without regenerating the next AI response."""
    _get_chat_or_403(chat_id)
    msg = Message.query.get_or_404(msg_id)
    if msg.chat_id != chat_id:
        abort(403)
    if msg.role != 'user':
        return jsonify({'error': 'Only user messages can be edited'}), 400

    new_content = _sanitize((request.json or {}).get('content', ''), max_words=500)
    if not new_content:
        return jsonify({'error': 'Empty message'}), 400

    msg.content = new_content
    db.session.commit()
    return jsonify({'message': 'edited', 'content': new_content})


@app.route('/api/chat/<int:chat_id>/message/<int:msg_id>', methods=['DELETE'])
@login_required
def delete_message(chat_id: int, msg_id: int):
    """Delete a single message and the immediate AI reply if it follows."""
    _get_chat_or_403(chat_id)
    msg = Message.query.get_or_404(msg_id)
    if msg.chat_id != chat_id:
        abort(403)

    if msg.role == 'user':
        next_msg = (Message.query
                    .filter_by(chat_id=chat_id)
                    .filter(Message.id > msg_id)
                    .order_by(Message.id)
                    .first())
        if next_msg and next_msg.role == 'assistant':
            db.session.delete(next_msg)

    db.session.delete(msg)
    db.session.commit()
    return jsonify({'message': 'deleted'})


@app.route('/api/chat/<int:chat_id>/regenerate_last', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def regenerate_last(chat_id: int):
    """Delete the last AI response and regenerate it."""
    chat = _get_chat_or_403(chat_id)

    last_assistant = (Message.query
                      .filter_by(chat_id=chat_id, role='assistant')
                      .order_by(Message.id.desc())
                      .first())
    if last_assistant:
        db.session.delete(last_assistant)
        db.session.commit()

    history = _get_chat_history(chat_id)
    subtopics, hours, course = _get_topic_context(chat)

    try:
        content, model_used, failures = get_reply(
            history=history[:-1] if history and history[-1]['role'] == 'user' else history,
            new_message=history[-1]['content'] if history and history[-1]['role'] == 'user' else 'Please continue.',
            course=course or '',
            subject=chat.subject,
            topic=chat.topic,
            subtopics=subtopics,
            hours=hours,
            model_list=get_teacher_model_list(),
            use_chinese=get_use_chinese()
        )
        new_msg = _save_message(chat_id, 'assistant', content)

        resp = {'content': content, 'role': 'assistant', 'id': new_msg.id}
        if failures:
            resp['notice'] = {'model': model_used, 'reasons': failures}
        return jsonify(resp)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat/<int:chat_id>/send/stream', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_send_stream(chat_id: int):
    chat = _get_chat_or_403(chat_id)

    user_message = _sanitize((request.json or {}).get('message', ''), max_words=500)
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    _save_message(chat_id, 'user', user_message)

    history = _get_chat_history(chat_id)[:-1]
    subtopics, hours, course = _get_topic_context(chat)
    model_list = get_teacher_model_list()
    use_zh = get_use_chinese()

    def generate():
        full_text = ''
        try:
            for chunk in stream_reply(
                history=history,
                new_message=user_message,
                course=course or '',
                subject=chat.subject,
                topic=chat.topic,
                subtopics=subtopics,
                hours=hours,
                model_list=model_list,
                use_chinese=use_zh
            ):
                full_text += chunk
                yield f"data: {json.dumps(chunk)}\n\n"

            _save_message(chat_id, 'assistant', full_text)
            yield 'data: [DONE]\n\n'
        except RuntimeError as e:
            yield f'data: [ERROR] {str(e)}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/api/chat/<int:chat_id>/quiz/stream', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_quiz_stream(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    history = _get_chat_history(chat_id)

    if len(history) < 2:
        return jsonify({'error': 'Study a bit first before taking a quiz!'}), 400

    subtopics, hours, course = _get_topic_context(chat)
    model_list = get_teacher_model_list()
    use_zh = get_use_chinese()

    def generate():
        full_text = ''
        try:
            for chunk in stream_quiz(
                history=history,
                course=course or '',
                subject=chat.subject,
                topic=chat.topic,
                subtopics=subtopics,
                hours=hours,
                model_list=model_list,
                use_chinese=use_zh
            ):
                full_text += chunk
                yield f"data: {json.dumps(chunk)}\n\n"

            _save_message(chat_id, 'assistant', full_text)
            yield 'data: [DONE_QUIZ]\n\n'
        except RuntimeError as e:
            yield f'data: [ERROR] {str(e)}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/upload', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_upload'))
def upload_files():
    file_paths = []
    try:
        files       = request.files.getlist('files')
        manual_text = _sanitize(request.form.get('manual_text', ''),
                                max_words=get_word_limit())
        if not files and not manual_text:
            return jsonify({'error': 'Please upload files or paste text'}), 400

        for file in files:
            if not file.filename: continue
            filename = secure_filename(file.filename)
            if not filename: continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                return jsonify({'error': f'Unsupported file type: {ext}'}), 400
            path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(path)
            file_paths.append(path)

        if not file_paths and not manual_text:
            return jsonify({'error': 'No valid files received'}), 400

        final_json = organize_with_llm(
            file_paths, manual_text=manual_text or None,
            today_str=get_today(), model_list=get_extract_model_list())

        existing = _get_study_data()
        if existing:
            existing.extracted_json        = json.dumps(final_json)
            existing.topic_status          = None
            existing.schedule_json         = None
            existing.pending_schedule_json = None
        else:
            db.session.add(StudyData(userid=current_user.id,
                                     extracted_json=json.dumps(final_json)))
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
# SAVE EXTRACTED / STATUS
# ─────────────────────────────────────────────

@app.route('/save_extracted/<int:userid>', methods=['POST'])
@login_required
def save_extracted(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
    payload = _sanitize_topics_payload(request.json or {})
    user.extracted_json = json.dumps(payload)
    db.session.commit()
    _log_activity('edit')
    return jsonify({'message': 'saved'})


@app.route('/submit_status/<int:userid>', methods=['POST'])
@login_required
def submit_status(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
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

    schedule = generate_schedule(json.loads(user.topic_status),
                                 today_str=get_today(),
                                 max_tokens=get_max_tokens(),
                                 model_list=get_sched_model_list())
    schedule, meta = _extract_meta(schedule)
    user.schedule_json = json.dumps(schedule)
    db.session.commit()
    cache.delete(f'schedule_{current_user.id}')
    _log_activity('generate')

    resp = {'schedule': schedule}
    notice = _build_llm_notice(meta)
    if notice: resp['notice'] = notice
    return jsonify(resp)


@app.route('/schedule/<int:userid>')
@login_required
def schedule(userid):
    _require_owner(userid)
    ck     = f'schedule_{userid}'
    cached = cache.get(ck)
    if cached: return jsonify(cached)
    user = _get_study_data()
    if not user or not user.schedule_json:
        return jsonify({'error': 'Schedule not found'}), 404
    data = json.loads(user.schedule_json)
    cache.set(ck, data, timeout=120)
    return jsonify(data)


# ─────────────────────────────────────────────
# PROGRESS ROUTES
# ─────────────────────────────────────────────

@app.route('/update_progress/<int:userid>', methods=['POST'])
@login_required
def update_progress(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404

    updated_subjects = _sanitize_topics_payload(
        (request.json or {}).get('Subjects', {}))

    if user.topic_status:
        status = json.loads(user.topic_status)
    else:
        extracted = json.loads(user.extracted_json)
        status = {
            'Exam_dates': extracted.get('Exam_dates', {}),
            'Subjects':   {
                s: {t: {'status': '0', 'subtopics': tdata.get('subtopics', [])
                         if isinstance(tdata, dict) else []}
                    for t, tdata in topics.items()}
                for s, topics in extracted.get('Subjects', {}).items()
            },
            'study_days': extracted.get('study_days', {})
        }

    for subj, topics in updated_subjects.items():
        if subj not in status['Subjects']: continue
        for topic, val in topics.items():
            if topic not in status['Subjects'][subj]: continue
            existing_topic = status['Subjects'][subj][topic]
            if isinstance(existing_topic, dict):
                # New schema — only update status, preserve subtopics
                try:
                    pct = max(0, min(100, int(val if not isinstance(val, dict) else val.get('status', 0))))
                    existing_topic['status'] = str(pct)
                except (ValueError, TypeError): pass
            else:
                # Old flat schema fallback
                try:
                    pct = max(0, min(100, int(val)))
                    status['Subjects'][subj][topic] = str(pct)
                except (ValueError, TypeError): pass

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
        new_schedule = generate_schedule(json.loads(user.topic_status),
                                         today_str=get_today(),
                                         max_tokens=get_max_tokens(),
                                         model_list=get_sched_model_list())
    except RuntimeError as e:
        return jsonify({'error': 'All AI models failed.', 'details': str(e)}), 500

    new_schedule, meta = _extract_meta(new_schedule)
    user.pending_schedule_json = json.dumps(new_schedule)
    db.session.commit()
    _log_activity('regenerate')

    resp = {'old_schedule': json.loads(user.schedule_json), 'new_schedule': new_schedule}
    notice = _build_llm_notice(meta)
    if notice: resp['notice'] = notice
    return jsonify(resp)


@app.route('/keep_schedule/<int:userid>', methods=['POST'])
@login_required
def keep_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
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
    return jsonify({'id': current_user.id, 'username': current_user.username,
                    'is_admin': current_user.is_admin})


# ─────────────────────────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin: abort(403)
    fourteen_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=14)
    daily_logs = (db.session.query(
        db.func.date(RequestLog.timestamp).label('day'),
        db.func.count().label('count'))
        .filter(RequestLog.timestamp >= fourteen_days_ago)
        .group_by('day').order_by('day').all())
    recent_activity = (db.session.query(ActivityLog, User.username)
        .outerjoin(User, User.id == ActivityLog.userid)
        .order_by(ActivityLog.timestamp.desc()).limit(50).all())
    return render_template('Admin.html',
        total_users=User.query.count(),
        total_requests=RequestLog.query.count(),
        total_schedules=StudyData.query.filter(StudyData.schedule_json.isnot(None)).count(),
        total_chats=Chat.query.count(),
        daily_logs=daily_logs, recent_activity=recent_activity,
        test_today=_get_setting('test_today'),
        real_today=datetime.date.today().strftime('%d-%m-%Y'),
        current_limits={k: (_get_setting(k) or v) for k, v in RATE_LIMIT_DEFAULTS.items()},
        default_limits=RATE_LIMIT_DEFAULTS,
        max_tokens=get_max_tokens(),
        sched_models=get_sched_model_list(),
        extract_models=get_extract_model_list(),
        teacher_models=get_teacher_model_list(),
        default_sched_models=SCHED_MODELS,
        default_extract_models=VISION_MODELS,
        default_teacher_models=TEACHER_MODELS,
        word_limit=get_word_limit(), default_word_limit=DEFAULT_WORD_LIMIT,
        use_chinese=get_use_chinese())


# ─────────────────────────────────────────────
# ADMIN USER API
# ─────────────────────────────────────────────

@app.route('/admin/api/users')
@login_required
def admin_list_users():
    if not current_user.is_admin: abort(403)
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify([{
        'id': u.id, 'username': u.username, 'email': u.email,
        'course': u.course or '', 'is_admin': u.is_admin,
        'created_at': u.created_at.strftime('%d %b %Y %H:%M'),
        'password_hash': u.password_hash
    } for u in users])


@app.route('/admin/api/users/<int:uid>/role', methods=['POST'])
@login_required
def admin_toggle_role(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot change own role'}), 400
    user = User.query.get_or_404(uid)
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify({'is_admin': user.is_admin})


@app.route('/admin/api/users/<int:uid>/update', methods=['POST'])
@login_required
def admin_update_user(uid):
    if not current_user.is_admin: abort(403)
    user = User.query.get_or_404(uid)
    data = request.get_json() or {}
    if 'username' in data:
        nu = _sanitize_field(data['username'], 80)
        if len(nu) < 3: return jsonify({'error': 'Username too short'}), 400
        if not re.match(r'^[A-Za-z0-9_\-]+$', nu): return jsonify({'error': 'Invalid characters'}), 400
        ex = User.query.filter_by(username=nu).first()
        if ex and ex.id != uid: return jsonify({'error': 'Username taken'}), 409
        user.username = nu
    if 'email' in data:
        ne = _sanitize_email(data['email'])
        if not ne: return jsonify({'error': 'Invalid email'}), 400
        ex = User.query.filter_by(email=ne).first()
        if ex and ex.id != uid: return jsonify({'error': 'Email registered'}), 409
        user.email = ne
    if 'course' in data:
        user.course = _sanitize_field(data['course'], 50) or None
    if 'password' in data:
        pw = data['password']
        if len(pw) < 8: return jsonify({'error': 'Password too short'}), 400
        user.set_password(pw)
        user.session_version = (user.session_version or 0) + 1
    db.session.commit()
    return jsonify({'message': 'User updated'})


@app.route('/admin/api/users/<int:uid>/force_logout', methods=['POST'])
@login_required
def admin_force_logout(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot logout yourself'}), 400
    user = User.query.get_or_404(uid)
    user.session_version = (user.session_version or 0) + 1
    db.session.commit()
    return jsonify({'message': f'{user.username} logged out on next request'})


@app.route('/admin/api/users/<int:uid>', methods=['DELETE'])
@login_required
def admin_delete_user(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot delete yourself'}), 400
    user = User.query.get_or_404(uid)
    # Clean up all related data including chats
    for chat in Chat.query.filter_by(userid=uid).all():
        Message.query.filter_by(chat_id=chat.id).delete()
    Chat.query.filter_by(userid=uid).delete()
    StudyData.query.filter_by(userid=uid).delete()
    ActivityLog.query.filter_by(userid=uid).delete()
    RequestLog.query.filter_by(userid=uid).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'message': f'User {uid} deleted'})


# ─────────────────────────────────────────────
# ADMIN SETTINGS API
# ─────────────────────────────────────────────

@app.route('/admin/set_date',          methods=['POST'])
@app.route('/admin/reset_date',        methods=['POST'])
@app.route('/admin/set_rate_limits',   methods=['POST'])
@app.route('/admin/reset_rate_limits', methods=['POST'])
@app.route('/admin/set_max_tokens',    methods=['POST'])
@app.route('/admin/set_word_limit',    methods=['POST'])
@app.route('/admin/set_model_list',    methods=['POST'])
@app.route('/admin/reset_model_list',  methods=['POST'])
@app.route('/admin/set_chinese',       methods=['POST'])
def admin_settings():
    if not current_user.is_authenticated or not current_user.is_admin: abort(403)
    ep   = request.path.split('/')[-1]
    data = request.get_json() or {}

    if ep == 'set_date':
        dv = _sanitize_field(data.get('date',''), 20)
        try: parse_dmy(dv)
        except: return jsonify({'error':'Invalid date'}), 400
        _set_setting('test_today', dv)
        return jsonify({'message': f'Test date set to {dv}'})

    if ep == 'reset_date':
        row = AppSettings.query.get('test_today')
        if row: db.session.delete(row); db.session.commit()
        return jsonify({'message': 'Reset to real today'})

    if ep == 'set_rate_limits':
        updated = {}
        for key in RATE_LIMIT_DEFAULTS:
            val = _sanitize_field(data.get(key,''), 50)
            if val: _set_setting(key, val); updated[key] = val
        return jsonify({'message':'Updated','updated':updated}) if updated \
               else (jsonify({'error':'No valid keys'}), 400)

    if ep == 'reset_rate_limits':
        for k, v in RATE_LIMIT_DEFAULTS.items(): _set_setting(k, v)
        return jsonify({'message':'Reset to defaults'})

    if ep == 'set_max_tokens':
        try:
            t = int(data.get('max_tokens',0))
            if not (500 <= t <= 32000): raise ValueError
        except: return jsonify({'error':'Must be 500-32000'}), 400
        _set_setting('max_tokens', str(t))
        return jsonify({'message': f'max_tokens = {t}'})

    if ep == 'set_word_limit':
        try:
            wl = int(data.get('word_limit',0))
            if not (100 <= wl <= 10000): raise ValueError
        except: return jsonify({'error':'Must be 100-10000'}), 400
        _set_setting('text_word_limit', str(wl))
        return jsonify({'message': f'Word limit = {wl}'})

    if ep == 'set_model_list':
        lk = _sanitize_field(data.get('list_key',''), 30)
        if lk not in ('sched_model_list','extract_model_list','teacher_model_list'):
            return jsonify({'error':'Invalid list_key'}), 400
        models = [_sanitize_field(m, 100) for m in (data.get('models') or []) if m]
        if not models: return jsonify({'error':'Empty list'}), 400
        _set_setting(lk, json.dumps(models))
        return jsonify({'message':'Updated','models':models})

    if ep == 'reset_model_list':
        lk = _sanitize_field(data.get('list_key',''), 30)
        defaults = {'sched_model_list': SCHED_MODELS,
                    'extract_model_list': VISION_MODELS,
                    'teacher_model_list': TEACHER_MODELS}
        if lk not in defaults: return jsonify({'error':'Invalid list_key'}), 400
        row = AppSettings.query.get(lk)
        if row: db.session.delete(row); db.session.commit()
        return jsonify({'message':'Reset','models':defaults[lk]})

    if ep == 'set_chinese':
        val = bool(data.get('enabled', False))
        _set_setting('use_chinese_prompts', 'true' if val else 'false')
        return jsonify({'message': f'Chinese prompts {"enabled" if val else "disabled"}'})

    abort(404)


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e): return _render_error(403,'Access Denied',"You don't have permission.")
@app.errorhandler(404)
def not_found(e): return _render_error(404,'Page Not Found',"This page doesn't exist.")
@app.errorhandler(413)
def too_large(e): return _render_error(413,'File Too Large','20 MB limit exceeded.')
@app.errorhandler(429)
def rate_limited(e): return _render_error(429,'Slow Down','Too many requests.')
@app.errorhandler(Exception)
def handle_exception(e):
    from flask_limiter.errors import RateLimitExceeded
    if isinstance(e, RateLimitExceeded):
        return _render_error(429,'Slow Down','Rate limit hit. Wait a moment.')
    raise e


if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(debug=True)
