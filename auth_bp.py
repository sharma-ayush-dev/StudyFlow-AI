from flask import Blueprint, request, jsonify, redirect, url_for, session
from flask_login import login_user, logout_user, current_user
import datetime
import re
import os
import urllib.parse
import secrets as _secrets

from extensions import db, limiter
from models import User
from helpers import _sanitize_field, _sanitize_email, _log_activity, _rl, _create_login_otp, _verify_login_otp, _send_welcome_email, _render_error, get_default_cost_limit

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', endpoint='landing')
def landing():
    from flask import render_template
    return render_template('Landing.html')


@auth_bp.route('/register', methods=['POST'], endpoint='register')
@limiter.limit(_rl('rl_register'))
def register():
    data      = request.get_json() or {}
    full_name = data.get('full_name', '').strip()
    username  = _sanitize_field(data.get('username', ''), 80)
    email     = _sanitize_email(data.get('email', ''))
    password  = data.get('password', '')
    course    = _sanitize_field(data.get('course', ''), 50)

    if not full_name or not username or not email or not password or not course:
        return jsonify({'error': 'All fields are required'}), 400
    if len(full_name) > 50:
        return jsonify({'error': 'Full name must be 50 characters or fewer'}), 400
    if not re.match(r'^[A-Za-z\s]+$', full_name):
        return jsonify({'error': 'Full name must contain only letters and spaces'}), 400
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

    user = User(
        full_name=full_name,
        username=username,
        email=email,
        course=course,
        session_version=0,
        cost_limit=get_default_cost_limit()
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session['session_version'] = 0
    login_user(user, remember=True)
    _log_activity('register', {'username': username})
    return jsonify({'redirect': url_for('landing')}), 201


@auth_bp.route('/login', methods=['POST'], endpoint='login')
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


@auth_bp.route('/logout', endpoint='logout')
def logout():
    logout_user()
    session.pop('session_version', None)
    session.pop('incomplete_profile', None)
    return redirect(url_for('landing'))


@auth_bp.route('/otp/send', methods=['POST'], endpoint='otp_send')
@limiter.limit(_rl('rl_login'))
def otp_send():
    data = request.get_json() or {}
    email = _sanitize_email(data.get('email', ''))
    if not email:
        return jsonify({'error': 'Invalid email address'}), 400

    _create_login_otp(email)
    return jsonify({'message': 'OTP sent successfully. Please check your console/terminal.'}), 200


@auth_bp.route('/otp/verify', methods=['POST'], endpoint='otp_verify')
@limiter.limit(_rl('rl_login'))
def otp_verify():
    data = request.get_json() or {}
    email = _sanitize_email(data.get('email', ''))
    code = _sanitize_field(data.get('otp', ''), 10)

    if not email or not code:
        return jsonify({'error': 'Email and OTP code are required'}), 400

    if not _verify_login_otp(email, code):
        return jsonify({'error': 'Invalid or expired OTP'}), 401

    user = User.query.filter_by(email=email).first()
    if user:
        session['session_version'] = user.session_version or 0
        login_user(user, remember=True)
        _log_activity('login_otp', {'email': email})

        if not user.username or user.username.startswith('email_temp_') or user.username.startswith('google_temp_'):
            session['incomplete_profile'] = True
            return jsonify({'redirect': url_for('auth.complete_profile')}), 200

        return jsonify({'redirect': url_for('landing')}), 200
    else:
        import uuid
        temp_username = f"email_temp_{uuid.uuid4().hex[:8]}"

        user = User(username=temp_username, email=email, course=None, session_version=0, cost_limit=get_default_cost_limit())
        import secrets
        user.set_password(secrets.token_urlsafe(16))
        db.session.add(user)
        db.session.commit()

        session['session_version'] = 0
        login_user(user, remember=True)
        session['incomplete_profile'] = True
        _log_activity('register_otp_init', {'email': email})
        return jsonify({'redirect': url_for('auth.complete_profile')}), 201


@auth_bp.route('/google', endpoint='google_login')
def google_login():
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')

    if not client_id or not client_secret:
        return _render_error(501, "Google Sign-In Unconfigured", "Google Sign-In is not configured by the administrator.")

    redirect_uri = url_for('auth.google_callback', _external=True)
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': _secrets.token_urlsafe(16),
        'access_type': 'offline',
        'prompt': 'select_account'
    }
    session['oauth_state'] = params['state']
    google_auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(google_auth_url)


@auth_bp.route('/google/callback', endpoint='google_callback')
def google_callback():
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')

    if not client_id or not client_secret:
        return _render_error(501, "Google Sign-In Unconfigured", "Google Sign-In is not configured by the administrator.")

    code = request.args.get('code')
    state = request.args.get('state')
    expected_state = session.pop('oauth_state', None)
    if not code or (expected_state and state != expected_state):
        return "Google Authentication failed (State mismatch or no code)", 400

    import requests
    redirect_uri = url_for('auth.google_callback', _external=True)
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code'
    }
    res = requests.post(token_url, data=data)
    if not res.ok:
        return "Failed to exchange token with Google", 400

    tokens = res.json()
    access_token = tokens.get('access_token')
    userinfo_url = f"https://www.googleapis.com/oauth2/v3/userinfo?access_token={access_token}"
    info_res = requests.get(userinfo_url)
    if not info_res.ok:
        return "Failed to fetch user info from Google", 400
    user_info = info_res.json()
    email = user_info.get('email', '').lower()

    email = _sanitize_email(email)
    if not email:
        return "Invalid email address from Google/Mock callback", 400

    user = User.query.filter_by(email=email).first()
    if user:
        session['session_version'] = user.session_version or 0
        login_user(user, remember=True)
        _log_activity('login_google', {'email': email})

        if not user.username or user.username.startswith('google_temp_') or user.username.startswith('email_temp_'):
            session['incomplete_profile'] = True
            return redirect(url_for('auth.complete_profile'))

        return redirect(url_for('landing'))
    else:
        import uuid
        temp_username = f"google_temp_{uuid.uuid4().hex[:8]}"

        user = User(username=temp_username, email=email, course=None, session_version=0, cost_limit=get_default_cost_limit())
        import secrets
        user.set_password(secrets.token_urlsafe(16))
        db.session.add(user)
        db.session.commit()

        session['session_version'] = 0
        login_user(user, remember=True)
        session['incomplete_profile'] = True
        _log_activity('register_google_init', {'email': email})
        return redirect(url_for('auth.complete_profile'))


@auth_bp.route('/complete-profile', methods=['GET'], endpoint='complete_profile')
def complete_profile():
    if not current_user.is_authenticated:
        return redirect(url_for('landing'))

    from flask import render_template
    return render_template('CompleteProfile.html', email=current_user.email)


@auth_bp.route('/complete-profile/submit', methods=['POST'], endpoint='complete_profile_submit')
@limiter.limit('10 per hour')
def complete_profile_submit():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    full_name = data.get('full_name', '').strip()
    username = _sanitize_field(data.get('username', ''), 80)
    course = _sanitize_field(data.get('course', ''), 50)

    if not full_name:
        return jsonify({'error': 'Full name is required'}), 400
    if len(full_name) > 50:
        return jsonify({'error': 'Full name must be 50 characters or fewer'}), 400
    if not re.match(r'^[A-Za-z\s]+$', full_name):
        return jsonify({'error': 'Full name must contain only letters and spaces'}), 400
    if not username:
        return jsonify({'error': 'Username is required'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if not re.match(r'^[A-Za-z0-9_\-]+$', username):
        return jsonify({'error': 'Username: letters, numbers, _ and - only'}), 400
    if not course:
        return jsonify({'error': 'Course is required'}), 400

    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != current_user.id:
        return jsonify({'error': 'Username already taken'}), 409

    current_user.full_name = full_name
    current_user.username = username
    current_user.course = course
    current_user.username_changed_at = datetime.datetime.utcnow()
    db.session.commit()

    session.pop('incomplete_profile', None)

    _log_activity('complete_profile_success', {'username': username, 'course': course})
    
    # Send welcome email asynchronously
    _send_welcome_email(current_user.email, username)
    
    return jsonify({'redirect': url_for('landing')}), 200
