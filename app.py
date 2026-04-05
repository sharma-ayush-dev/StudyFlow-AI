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
from schedule_planner import generate_schedule


# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)

# Generate a real key with: python -c "import secrets; print(secrets.token_hex(32))"
# Then set it as an env variable:  export SECRET_KEY=<value>
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-before-deploy')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///userdata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Reject uploads over 20 MB before they reach Python
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'landing'
login_manager.login_message = ''   # no default flash; we handle UI ourselves

# Rate limiter — uses the client IP as the key
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # no blanket limit; we set per-route limits below
    storage_uri='memory://'     # swap for 'redis://localhost:6379' in production
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

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(
            password,
            method='pbkdf2:sha256:600000',
            salt_length=16
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class StudyData(db.Model):

    id             = db.Column(db.Integer, primary_key=True)
    userid         = db.Column(db.Integer, db.ForeignKey('user.id'),
                               unique=True, nullable=False)
    extracted_json = db.Column(db.Text)   # { Exam_dates, Subjects, study_days }
    topic_status   = db.Column(db.Text)   # same shape with % values filled in
    schedule_json  = db.Column(db.Text)   # { DD-MM-YYYY: { Subject: { Topic: hrs } } }


class RequestLog(db.Model):

    id          = db.Column(db.Integer,  primary_key=True)
    userid      = db.Column(db.Integer,  db.ForeignKey('user.id'), nullable=True)
    method      = db.Column(db.String(10))
    path        = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    timestamp   = db.Column(db.DateTime, default=datetime.datetime.utcnow)


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────────
# REQUEST LOGGING
# ─────────────────────────────────────────────

@app.after_request
def log_request(response):
    if request.path.startswith('/static') or request.path == '/admin':
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
# HELPERS
# ─────────────────────────────────────────────

def _get_study_data() -> StudyData | None:
    return StudyData.query.filter_by(userid=current_user.id).first()


def _require_owner(userid: int):
    """IDOR guard — 403 if the URL userid doesn't match the session."""
    if userid != current_user.id and not current_user.is_admin:
        abort(403)


def _delete_files(paths: list[str]):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f'[WARN] Could not delete {p}: {e}')


def _render_error(code: int, title: str, message: str):
    return render_template('error.html', code=code, title=title, message=message), code


# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def landing():
    # No redirect — logged-in users see the landing page with extra nav buttons
    return render_template('Landing.html')


@app.route('/register', methods=['POST'])
@limiter.limit('10 per hour')
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
@limiter.limit('20 per hour')
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

    login_user(user, remember=remember,
               duration=datetime.timedelta(days=7))
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


# ─────────────────────────────────────────────
# FILE UPLOAD
# ─────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
@login_required
@limiter.limit('10 per hour')
def upload_files():
    file_paths = []
    try:
        files = request.files.getlist('files')
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

        final_json = organize_with_llm(file_paths, manual_text=manual_text or None)

        existing = _get_study_data()
        if existing:
            existing.extracted_json = json.dumps(final_json)
            existing.topic_status   = None
            existing.schedule_json  = None
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
        _delete_files(file_paths)   # always runs, even if LLM throws


# ─────────────────────────────────────────────
# SAVE EDITED EXTRACTED DATA
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


# ─────────────────────────────────────────────
# SAVE TOPIC STATUS
# ─────────────────────────────────────────────

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
# GENERATE SCHEDULE
# ─────────────────────────────────────────────

@app.route('/generate_schedule/<int:userid>', methods=['POST'])
@login_required
@limiter.limit('5 per hour')     # real server-side guard — not just UI
def generate(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user:
        return jsonify({'error': 'No data found'}), 404
    if not user.topic_status:
        return jsonify({'error': 'Topic status not submitted yet'}), 400

    schedule = generate_schedule(json.loads(user.topic_status))
    user.schedule_json = json.dumps(schedule)
    db.session.commit()
    return jsonify(schedule)


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
# CURRENT USER INFO  (JS uses this to get userid)
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
        .group_by('day')
        .order_by('day')
        .all()
    )

    return render_template(
        'Admin.html',
        total_users     = User.query.count(),
        total_requests  = RequestLog.query.count(),
        total_schedules = StudyData.query.filter(
                              StudyData.schedule_json.isnot(None)).count(),
        daily_logs      = daily_logs,
        recent_users    = User.query.order_by(
                              User.created_at.desc()).limit(10).all()
    )


# ─────────────────────────────────────────────
# ERROR HANDLERS  — proper HTML pages, not JSON
# ─────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return _render_error(
        403,
        "Access Denied",
        "You don't have permission to view this page."
    )

@app.errorhandler(404)
def not_found(e):
    return _render_error(
        404,
        "Page Not Found",
        "The page you're looking for doesn't exist or has been moved."
    )

@app.errorhandler(413)
def too_large(e):
    return _render_error(
        413,
        "File Too Large",
        "Your upload exceeds the 20 MB limit. Please compress your files and try again."
    )

@app.errorhandler(429)
def rate_limited(e):
    return _render_error(
        429,
        "Slow Down",
        "You've made too many requests in a short time. Please wait a moment and try again."
    )

# flask-limiter raises 429 as an exception too — handle it as HTML
@app.errorhandler(Exception)
def handle_limiter_error(e):
    from flask_limiter.errors import RateLimitExceeded
    if isinstance(e, RateLimitExceeded):
        return _render_error(
            429,
            "Slow Down",
            "You've hit the rate limit for this action. Please wait a moment and try again."
        )
    raise e   # re-raise anything else so Flask handles it normally


# ─────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)