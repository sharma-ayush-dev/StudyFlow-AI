import os
import re
import json
import time
import hashlib
import threading
import datetime
import urllib.parse
import secrets as _secrets

from flask import Flask, request, jsonify, render_template, redirect, url_for, abort, session, Response, stream_with_context
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename

from config import Config
from extensions import init_app_extensions, cache, limiter, db, RATE_LIMIT_DEFAULTS, DEFAULT_WORD_LIMIT, DEFAULT_SCHED_PREF_LIMIT
from models import (
    User, StudyData, Chat, Message, PasswordResetOTP, ContactMessage,
    AppSettings, ActivityLog, RequestLog
)
from helpers import *

from text_extractor import organize_with_llm, VISION_MODELS
from schedule_planner import (
    generate_schedule, invalidate_schedule_cache,
    MODELS as SCHED_MODELS, HARD_CAP as SCHED_HARD_CAP,
    MAX_SUBJECTS, MAX_TOPICS_PER_SUBJECT, MAX_TOTAL_TOPICS
)
from teacher import (
    get_initial_message, get_reply, get_quiz,
    stream_reply, stream_quiz, TEACHER_MODELS
)


# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)
app.config.from_object(Config)

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}

init_app_extensions(app)

# Model list helpers (wrappers use the schedule/vision/teacher defaults imported above)
def get_sched_model_list() -> list:
    return _parse_model_list('sched_model_list', SCHED_MODELS)

def get_extract_model_list() -> list:
    return _parse_model_list('extract_model_list', VISION_MODELS)

def get_teacher_model_list() -> list:
    return _parse_model_list('teacher_model_list', TEACHER_MODELS)

# ── UPLOAD / EXTRACTION VALIDATION LIMITS ────────────────────────────────────
# These mirror limits in text_extractor.py but are enforced at the route level
# so we can return a clean JSON error before touching any LLM.
MAX_UPLOAD_FILES      = 5
MAX_MANUAL_TEXT_WORDS = 3000


# settings and job stores moved to helpers


# models, helpers and extensions were moved to separate modules


# attach helpers' request logger
app.after_request(log_request)

# Routes moved to Blueprints: auth_bp, pages_bp
from auth_bp import auth_bp
from pages_bp import pages_bp

app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)


def _register_global_aliases_for_blueprint(bp):
    """Expose shorthand endpoint names for templates and existing code."""
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith(bp.name + '.'): 
            continue
        view_func = app.view_functions[rule.endpoint]
        endpoint_name = rule.endpoint.split('.', 1)[1]
        alias_names = {endpoint_name, getattr(view_func, '__name__', endpoint_name)}
        for alias in alias_names:
            if not alias or alias in app.view_functions:
                continue
            app.add_url_rule(
                rule.rule,
                endpoint=alias,
                view_func=view_func,
                methods=rule.methods,
                defaults=rule.defaults,
            )


_register_global_aliases_for_blueprint(auth_bp)
_register_global_aliases_for_blueprint(pages_bp)


# Page routes moved to pages_bp


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


# Routes moved to Blueprints: chat_bp, api_bp, admin_bp
from chat_bp import chat_bp
from api_bp import api_bp
from admin_bp import admin_bp

app.register_blueprint(chat_bp)
app.register_blueprint(api_bp)
app.register_blueprint(admin_bp)

_register_global_aliases_for_blueprint(chat_bp)
_register_global_aliases_for_blueprint(api_bp)
_register_global_aliases_for_blueprint(admin_bp)


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
    with app.app_context():
        db.create_all()
        _ensure_runtime_schema()
    app.run(debug=True)
