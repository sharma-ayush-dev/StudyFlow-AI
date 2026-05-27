from flask import Blueprint, request, jsonify, redirect, url_for, session
from flask_login import login_user, logout_user
import datetime
import re

from extensions import db, limiter
from models import User
from helpers import _sanitize_field, _sanitize_email, _log_activity, _rl

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', endpoint='landing')
def landing():
    from flask import render_template
    return render_template('Landing.html')


@auth_bp.route('/register', methods=['POST'], endpoint='register')
@limiter.limit(_rl('rl_register'))
def register():
    data     = request.get_json() or {}
    username = _sanitize_field(data.get('username', ''), 80)
    email    = _sanitize_email(data.get('email', ''))
    password = data.get('password', '')
    course   = _sanitize_field(data.get('course', ''), 50)

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
    return redirect(url_for('landing'))
