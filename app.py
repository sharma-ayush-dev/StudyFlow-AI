import os
import json
import datetime

from flask import Flask, request, jsonify, render_template, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

from text_extractor import organize_with_llm
from schedule_planner import generate_schedule

# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)

# IMPORTANT: change this to a long random string before deploying.
# Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-before-deploy')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///userdata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Hard limit on upload size — rejects anything over 20 MB before it hits Python
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'landing'       # redirect to landing page if not logged in
login_manager.login_message = ''           # suppress default flash message (we handle UI)


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model, UserMixin):
    """Stores credentials and admin flag for each registered user."""

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def set_password(self, password: str):
        # pbkdf2:sha256 with 600 000 iterations + automatic random salt
        self.password_hash = generate_password_hash(
            password,
            method='pbkdf2:sha256:600000',
            salt_length=16
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class StudyData(db.Model):
    """One row per user — stores all three JSON blobs for the pipeline."""

    id             = db.Column(db.Integer, primary_key=True)
    userid         = db.Column(db.Integer, db.ForeignKey('user.id'),
                               unique=True, nullable=False)

    # { Exam_dates, Subjects, study_days }  — set after file upload
    extracted_json = db.Column(db.Text)

    # Same shape, topic values replaced with completion % strings
    topic_status   = db.Column(db.Text)

    # { DD-MM-YYYY: { Subject: { Topic: hours } } }
    schedule_json  = db.Column(db.Text)


class RequestLog(db.Model):
    """One row per HTTP request — used by the admin dashboard."""

    id          = db.Column(db.Integer, primary_key=True)
    userid      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    method      = db.Column(db.String(10))
    path        = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    timestamp   = db.Column(db.DateTime, default=datetime.datetime.utcnow)


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────────
# REQUEST LOGGING HOOK
# ─────────────────────────────────────────────

@app.after_request
def log_request(response):
    # Skip static files and the admin page itself to avoid log spam
    if request.path.startswith('/static') or request.path == '/admin':
        return response
    try:
        entry = RequestLog(
            userid      = current_user.id if current_user.is_authenticated else None,
            method      = request.method,
            path        = request.path,
            status_code = response.status_code
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()   # never let logging crash a real request
    return response


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _get_study_data_for_current_user() -> StudyData | None:
    return StudyData.query.filter_by(userid=current_user.id).first()


def _require_owner(userid: int):
    """
    IDOR guard: abort with 403 if the requested userid doesn't belong to the
    logged-in user (unless they're an admin).
    """
    if userid != current_user.id and not current_user.is_admin:
        abort(403)


def _delete_files(paths: list[str]):
    """Delete uploaded files from disk, ignoring any that don't exist."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f"[WARN] Could not delete {p}: {e}")


# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def landing():
    """Landing page — shows login/register modal if not authenticated."""
    if current_user.is_authenticated:
        return redirect(url_for('upload_page'))
    return render_template('Landing.html')


@app.route('/register', methods=['POST'])
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
def login():
    data       = request.get_json()
    identifier = (data.get('identifier') or '').strip()   # username OR email
    password   =  data.get('password')    or ''
    remember   =  data.get('remember',  False)

    if not identifier or not password:
        return jsonify({'error': 'Username/email and password are required'}), 400

    # Allow login with either username or email
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
# PAGE ROUTES  (all require login)
# ─────────────────────────────────────────────

@app.route('/upload_page')
@login_required
def upload_page():
    return render_template('Upload-page.html', username=current_user.username)


@app.route('/status')
@login_required
def status_page():
    user = _get_study_data_for_current_user()
    if not user:
        return redirect(url_for('upload_page'))

    data       = json.loads(user.extracted_json)
    exam_dates = data.get('Exam_dates', {})

    return render_template(
        'Status.html',
        data=data,
        exam_dates=exam_dates,
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
def upload_files():
    file_paths = []
    try:
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No files uploaded'}), 400

        for file in files:
            if not file.filename:
                continue
            # Whitelist allowed extensions
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in ('.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'):
                return jsonify({'error': f'Unsupported file type: {ext}'}), 400
            path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(path)
            file_paths.append(path)

        if not file_paths:
            return jsonify({'error': 'No valid files received'}), 400

        # Optional: plain-text syllabus typed directly in the browser
        # Upload-page.js can append a text field named "manual_text"
        manual_text = request.form.get('manual_text', '').strip()

        final_json = organize_with_llm(file_paths, manual_text=manual_text or None)

        existing = _get_study_data_for_current_user()
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
        # Always delete uploaded files from disk after processing
        _delete_files(file_paths)


# ─────────────────────────────────────────────
# SAVE EDITED EXTRACTED DATA  (page 2 edit feature)
# ─────────────────────────────────────────────

@app.route('/save_extracted/<int:userid>', methods=['POST'])
@login_required
def save_extracted(userid):
    _require_owner(userid)
    user = _get_study_data_for_current_user()
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
    user = _get_study_data_for_current_user()
    if not user:
        return jsonify({'error': 'User data not found'}), 404

    user.topic_status = json.dumps(request.json)
    db.session.commit()
    return jsonify({'message': 'saved'})


# ─────────────────────────────────────────────
# GENERATE SCHEDULE
# ─────────────────────────────────────────────

@app.route('/generate_schedule/<int:userid>', methods=['POST'])
@login_required
def generate(userid):
    _require_owner(userid)
    user = _get_study_data_for_current_user()
    if not user:
        return jsonify({'error': 'User data not found'}), 404
    if not user.topic_status:
        return jsonify({'error': 'Topic status not submitted yet'}), 400

    topic_data = json.loads(user.topic_status)
    schedule   = generate_schedule(topic_data)

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
    user = _get_study_data_for_current_user()
    if not user or not user.schedule_json:
        return jsonify({'error': 'Schedule not found'}), 404
    return jsonify(json.loads(user.schedule_json))


# ─────────────────────────────────────────────
# CURRENT USER INFO  (used by JS to get userid)
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

    total_users    = User.query.count()
    total_requests = RequestLog.query.count()
    total_schedules = StudyData.query.filter(
        StudyData.schedule_json.isnot(None)
    ).count()

    # Requests per day for the last 14 days
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

    recent_users = (
        User.query
        .order_by(User.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        'Admin.html',
        total_users=total_users,
        total_requests=total_requests,
        total_schedules=total_schedules,
        daily_logs=daily_logs,
        recent_users=recent_users
    )


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return jsonify({'error': 'Forbidden'}), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum upload size is 20 MB.'}), 413


# ─────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)