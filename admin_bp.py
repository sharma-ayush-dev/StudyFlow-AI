from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
import datetime
import json

from extensions import db, RATE_LIMIT_DEFAULTS, DEFAULT_WORD_LIMIT
from models import User, ActivityLog, RequestLog, StudyData, Chat
from helpers import *
from schedule_planner import MODELS as SCHED_MODELS
from text_extractor import VISION_MODELS
from teacher import TEACHER_MODELS
from flask import abort
import re

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/admin')
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
        sched_pref_limit=get_sched_pref_limit(), default_sched_pref_limit=DEFAULT_SCHED_PREF_LIMIT,
        use_chinese=get_use_chinese())


@admin_bp.route('/admin/api/users')
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


@admin_bp.route('/admin/api/users/<int:uid>/role', methods=['POST'])
@login_required
def admin_toggle_role(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot change own role'}), 400
    user = User.query.get_or_404(uid)
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify({'is_admin': user.is_admin})


@admin_bp.route('/admin/api/users/<int:uid>/update', methods=['POST'])
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


@admin_bp.route('/admin/api/users/<int:uid>/force_logout', methods=['POST'])
@login_required
def admin_force_logout(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot logout yourself'}), 400
    user = User.query.get_or_404(uid)
    user.session_version = (user.session_version or 0) + 1
    db.session.commit()
    return jsonify({'message': f'{user.username} logged out on next request'})


@admin_bp.route('/admin/api/users/<int:uid>', methods=['DELETE'])
@login_required
def admin_delete_user(uid):
    if not current_user.is_admin: abort(403)
    if uid == current_user.id: return jsonify({'error': 'Cannot delete yourself'}), 400
    user = User.query.get_or_404(uid)
    for chat in Chat.query.filter_by(userid=uid).all():
        Message.query.filter_by(chat_id=chat.id).delete()
    Chat.query.filter_by(userid=uid).delete()
    StudyData.query.filter_by(userid=uid).delete()
    ActivityLog.query.filter_by(userid=uid).delete()
    RequestLog.query.filter_by(userid=uid).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'message': f'User {uid} deleted'})


@admin_bp.route('/admin/<action>', methods=['POST'])
@login_required
def admin_settings(action):
    if not current_user.is_authenticated or not current_user.is_admin: abort(403)
    ep = action
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
        return jsonify({'message':'Updated','updated':updated}) if updated else (jsonify({'error':'No valid keys'}), 400)
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
    if ep == 'set_sched_pref_limit':
        try:
            lim = int(data.get('limit', 0))
            if not (10 <= lim <= 2000): raise ValueError
        except: return jsonify({'error':'Must be 10-2000'}), 400
        _set_setting('sched_pref_limit', str(lim))
        return jsonify({'message': f'Schedule preference limit = {lim}'})
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
