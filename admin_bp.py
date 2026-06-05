from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
import datetime
import json

from extensions import db, RATE_LIMIT_DEFAULTS, DEFAULT_WORD_LIMIT, DEFAULT_SCHED_PREF_LIMIT
from models import User, ActivityLog, RequestLog, StudyData, Chat, MembershipTier, UserMembership, UsageLog, Payment, WebhookLog
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
    return render_template('Admin.html',
        daily_logs=daily_logs,
        test_today=_get_setting('test_today'),
        real_today=datetime.date.today().strftime('%d-%m-%Y'),
        default_cost_limit=get_default_cost_limit(),
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
        use_chinese=get_use_chinese(),
        model_costs=get_model_costs())


@admin_bp.route('/admin/api/stats')
@login_required
def admin_api_stats():
    if not current_user.is_admin: abort(403)
    
    today_str = get_today()
    today_date = parse_dmy(today_str)
    start_of_today = datetime.datetime.combine(today_date, datetime.time.min)
    end_of_today = datetime.datetime.combine(today_date, datetime.time.max)
    
    # 1. Active Users Today (unique userids in RequestLog or ActivityLog today)
    req_uids = {r[0] for r in db.session.query(RequestLog.userid).filter(
        RequestLog.timestamp >= start_of_today,
        RequestLog.timestamp <= end_of_today,
        RequestLog.userid.isnot(None)
    ).all()}
    
    act_uids = {r[0] for r in db.session.query(ActivityLog.userid).filter(
        ActivityLog.timestamp >= start_of_today,
        ActivityLog.timestamp <= end_of_today,
        ActivityLog.userid.isnot(None)
    ).all()}
    
    active_users_today = len(req_uids.union(act_uids))
    
    # 2. Total Requests Today
    requests_today = RequestLog.query.filter(
        RequestLog.timestamp >= start_of_today,
        RequestLog.timestamp <= end_of_today
    ).count()
    
    # 3. Uploads Today
    uploads_today = ActivityLog.query.filter(
        ActivityLog.action == 'upload',
        ActivityLog.timestamp >= start_of_today,
        ActivityLog.timestamp <= end_of_today
    ).count()
    
    # 4. LLM calls today (tokens & cost)
    llm_logs_today = ActivityLog.query.filter(
        ActivityLog.action == 'llm_call',
        ActivityLog.timestamp >= start_of_today,
        ActivityLog.timestamp <= end_of_today
    ).all()
    
    tokens_today = 0
    cost_today = 0.0
    for log in llm_logs_today:
        try:
            dt = json.loads(log.detail)
            tokens_today += dt.get('prompt_tokens', 0) + dt.get('completion_tokens', 0)
            cost_today += dt.get('cost', 0.0)
        except:
            pass
            
    # 5. Schedules generated today
    schedules_today = ActivityLog.query.filter(
        ActivityLog.action.in_(['generate', 'regenerate']),
        ActivityLog.timestamp >= start_of_today,
        ActivityLog.timestamp <= end_of_today
    ).count()
    
    return jsonify({
        'active_users_today': active_users_today,
        'requests_today': requests_today,
        'uploads_today': uploads_today,
        'tokens_today': tokens_today,
        'cost_today': round(cost_today, 4),
        'schedules_today': schedules_today,
        'total_users': User.query.count(),
        'total_requests': RequestLog.query.count(),
        'total_schedules': StudyData.query.filter(StudyData.schedule_json.isnot(None)).count(),
        'total_chats': Chat.query.count()
    })


@admin_bp.route('/admin/api/logs/activity')
@login_required
def admin_api_logs_activity():
    if not current_user.is_admin: abort(403)
    limit = max(10, min(100, int(request.args.get('limit', 50))))
    page = max(1, int(request.args.get('page', 1)))
    
    query_results = (db.session.query(ActivityLog, User.username)
                     .outerjoin(User, User.id == ActivityLog.userid)
                     .order_by(ActivityLog.timestamp.desc())
                     .limit(500)
                     .all())
    
    total = len(query_results)
    pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    paginated = query_results[offset:offset+limit]
    
    formatted = []
    for log, username in paginated:
        detail_text = ""
        if log.detail:
            try:
                dt = json.loads(log.detail)
                if log.action == 'upload':
                    detail_text = f"Uploaded {dt.get('files', 0)} files (manual text: {'Yes' if dt.get('manual') else 'No'})"
                elif log.action == 'study_start':
                    detail_text = f"Tutoring slot: {dt.get('subject')} - {dt.get('topic')}"
                elif log.action == 'llm_call':
                    detail_text = f"LLM Call: {dt.get('model')} (In: {dt.get('prompt_tokens', 0)}, Out: {dt.get('completion_tokens', 0)} tokens, Cost: ₹{round(dt.get('cost', 0.0), 4)})"
                elif log.action == 'register':
                    detail_text = f"Registered username: {dt.get('username')}"
                else:
                    detail_text = json.dumps(dt)
            except:
                detail_text = log.detail
        else:
            detail_text = "—"
            
        formatted.append({
            'id': log.id,
            'timestamp': log.timestamp.strftime('%d %b %H:%M:%S'),
            'username': username or 'Guest',
            'action': log.action,
            'detail': detail_text,
            'ip_address': log.ip_address or '—',
            'user_agent': log.user_agent or '—'
        })
        
    return jsonify({
        'logs': formatted,
        'total': total,
        'pages': pages,
        'current_page': page
    })


@admin_bp.route('/admin/api/logs/requests')
@login_required
def admin_api_logs_requests():
    if not current_user.is_admin: abort(403)
    limit = max(10, min(100, int(request.args.get('limit', 50))))
    page = max(1, int(request.args.get('page', 1)))
    
    query_results = (db.session.query(RequestLog, User.username)
                     .outerjoin(User, User.id == RequestLog.userid)
                     .order_by(RequestLog.timestamp.desc())
                     .limit(500)
                     .all())
    
    total = len(query_results)
    pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    paginated = query_results[offset:offset+limit]
    
    formatted = []
    for log, username in paginated:
        formatted.append({
            'id': log.id,
            'timestamp': log.timestamp.strftime('%d %b %H:%M:%S'),
            'username': username or 'Guest',
            'method': log.method,
            'path': log.path,
            'status_code': log.status_code,
            'ip_address': log.ip_address or '—',
            'user_agent': log.user_agent or '—'
        })
        
    return jsonify({
        'logs': formatted,
        'total': total,
        'pages': pages,
        'current_page': page
    })


@admin_bp.route('/admin/api/users')
@login_required
def admin_list_users():
    if not current_user.is_admin: abort(403)
    limit = max(10, min(100, int(request.args.get('limit', 20))))
    page = max(1, int(request.args.get('page', 1)))
    
    query = User.query.order_by(User.created_at.desc())
    total = query.count()
    pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    users = query.offset(offset).limit(limit).all()
    
    return jsonify({
        'users': [{
            'id': u.id, 'username': u.username, 'email': u.email,
            'full_name': u.full_name or '',
            'course': u.course or '', 'is_admin': u.is_admin,
            'created_at': u.created_at.strftime('%d %b %Y %H:%M'),
            'upload_count': u.upload_count or 0,
            'generations_count': u.generations_count or 0,
            'last_active': u.last_active.strftime('%d %b %H:%M') if u.last_active else 'Never',
            'input_tokens_used': u.input_tokens_used or 0,
            'output_tokens_used': u.output_tokens_used or 0,
            'last_model_used': u.last_model_used or 'None',
            'total_cost': round(u.total_cost or 0.0, 4),
            'membership_tier_id': u.membership.tier_id if u.membership else None,
            'membership_tier_name': u.membership.tier.name if (u.membership and u.membership.tier) else 'Bronze',
            'membership_usage_cost': round(u.membership.usage_cost or 0.0, 4) if u.membership else 0.0,
            'membership_usage_percentage': round(u.membership.usage_percentage or 0.0, 2) if u.membership else 0.0,
            'membership_budget_limit': round(u.membership.custom_budget_limit if (u.membership and u.membership.custom_budget_limit is not None) else (u.membership.tier.budget_limit if (u.membership and u.membership.tier) else 1.0), 2),
            'membership_total_amount_paid': round(u.membership.total_amount_paid or 0.0, 2) if u.membership else 0.0,
            'net_profit_loss': round((u.membership.total_amount_paid or 0.0) - (u.total_cost or 0.0), 4) if u.membership else round(-(u.total_cost or 0.0), 4)
        } for u in users],
        'total': total,
        'pages': pages,
        'current_page': page
    })


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
    if 'cost_limit' in data:
        try:
            cl = float(data['cost_limit'])
            user.cost_limit = max(0.0, cl)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid cost limit'}), 400
    if data.get('reset_cost'):
        user.total_cost = 0.0

    if 'membership_tier_id' in data:
        try:
            tid = int(data['membership_tier_id'])
            t = MembershipTier.query.get(tid)
            if t:
                um = UserMembership.query.filter_by(user_id=uid).first()
                if not um:
                    um = UserMembership(user_id=uid, tier_id=t.id, usage_cost=0.0, usage_percentage=0.0)
                    db.session.add(um)
                else:
                    if um.tier_id != t.id:
                        um.tier_id = t.id
                        um.custom_budget_limit = None
                    
                    effective_limit = um.custom_budget_limit if um.custom_budget_limit is not None else t.budget_limit
                    if effective_limit > 0:
                        um.usage_percentage = min(100.0, (um.usage_cost / effective_limit) * 100.0)
                    else:
                        um.usage_percentage = 100.0
                um.upgraded_at = datetime.datetime.utcnow()
        except (ValueError, TypeError):
            pass

    if data.get('reset_membership_usage'):
        um = UserMembership.query.filter_by(user_id=uid).first()
        if um:
            um.usage_cost = 0.0
            um.usage_percentage = 0.0

    if 'membership_usage_adjust' in data:
        try:
            adjust = float(data['membership_usage_adjust'])
            um = UserMembership.query.filter_by(user_id=uid).first()
            if um:
                t = MembershipTier.query.get(um.tier_id)
                um.usage_cost = max(0.0, (um.usage_cost or 0.0) + adjust)
                if t and t.budget_limit > 0:
                    um.usage_percentage = min(100.0, (um.usage_cost / t.budget_limit) * 100.0)
                else:
                    um.usage_percentage = 100.0
        except (ValueError, TypeError):
            pass

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
    if ep == 'set_model_costs':
        costs = data.get('costs', {})
        cleaned = {}
        for m, val in costs.items():
            if not m: continue
            try:
                inp = float(val.get('input', 0.0))
                out = float(val.get('output', 0.0))
                cleaned[m] = {'input': max(0.0, inp), 'output': max(0.0, out)}
            except:
                pass
        save_model_costs(cleaned)
        return jsonify({'message': 'Model costs updated successfully'})
    if ep == 'set_default_cost_limit':
        try:
            val = float(data.get('limit', 2.0))
            if val < 0: raise ValueError
        except:
            return jsonify({'error': 'Must be a non-negative number'}), 400
        _set_setting('default_cost_limit', str(val))
        db.session.query(User).update({User.cost_limit: val})
        db.session.commit()
        return jsonify({'message': f'Default cost limit updated to ₹{val:.2f} for all users.'})
    abort(404)


@admin_bp.route('/admin/api/tiers', methods=['GET'])
@login_required
def admin_list_tiers():
    if not current_user.is_admin: abort(403)
    tiers = MembershipTier.query.order_by(MembershipTier.display_order).all()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'display_price': t.display_price,
        'model_id': t.model_id,
        'budget_limit': t.budget_limit,
        'token_multiplier': t.token_multiplier,
        'speed_label': t.speed_label,
        'tutor_quality_label': t.tutor_quality_label,
        'display_order': t.display_order,
        'active': t.active
    } for t in tiers])


@admin_bp.route('/admin/api/tiers/create', methods=['POST'])
@login_required
def admin_create_tier():
    if not current_user.is_admin: abort(403)
    data = request.get_json() or {}
    name = _sanitize_field(data.get('name', ''), 50)
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if MembershipTier.query.filter_by(name=name).first():
        return jsonify({'error': 'Tier name already exists'}), 409

    try:
        t = MembershipTier(
            name=name,
            display_price=int(data.get('display_price', 0)),
            model_id=_sanitize_field(data.get('model_id', 'mistralai/mistral-nemo'), 100),
            budget_limit=float(data.get('budget_limit', 1.0)),
            token_multiplier=float(data.get('token_multiplier', float(data.get('budget_limit', 1.0)))),
            speed_label=_sanitize_field(data.get('speed_label', 'Standard'), 50),
            tutor_quality_label=_sanitize_field(data.get('tutor_quality_label', 'Standard'), 50),
            display_order=int(data.get('display_order', 0)),
            active=bool(data.get('active', True))
        )
        db.session.add(t)
        db.session.commit()
        return jsonify({'message': 'Tier created successfully', 'id': t.id})
    except Exception as e:
        db.session.rollback()
        print('CREATE TIER ERROR:', e)
        return jsonify({'error': 'Failed to create membership tier. Please check constraints.'}), 400


@admin_bp.route('/admin/api/tiers/<int:tier_id>/update', methods=['POST'])
@login_required
def admin_update_tier(tier_id):
    if not current_user.is_admin: abort(403)
    t = MembershipTier.query.get_or_404(tier_id)
    data = request.get_json() or {}

    if 'name' in data:
        name = _sanitize_field(data['name'], 50)
        if name:
            ex = MembershipTier.query.filter_by(name=name).first()
            if ex and ex.id != tier_id:
                return jsonify({'error': 'Tier name already exists'}), 409
            t.name = name

    if 'display_price' in data:
        try: t.display_price = int(data['display_price'])
        except (ValueError, TypeError): pass

    if 'model_id' in data:
        t.model_id = _sanitize_field(data['model_id'], 100)

    if 'budget_limit' in data:
        try: t.budget_limit = float(data['budget_limit'])
        except (ValueError, TypeError): pass

    if 'token_multiplier' in data:
        try: t.token_multiplier = float(data['token_multiplier'])
        except (ValueError, TypeError): pass

    if 'speed_label' in data:
        t.speed_label = _sanitize_field(data['speed_label'], 50)

    if 'tutor_quality_label' in data:
        t.tutor_quality_label = _sanitize_field(data['tutor_quality_label'], 50)

    if 'display_order' in data:
        try: t.display_order = int(data['display_order'])
        except (ValueError, TypeError): pass

    if 'active' in data:
        t.active = bool(data['active'])

    t.updated_at = datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Tier updated successfully'})


@admin_bp.route('/admin/api/tiers/<int:tier_id>', methods=['DELETE'])
@login_required
def admin_delete_tier(tier_id):
    if not current_user.is_admin: abort(403)
    t = MembershipTier.query.get_or_404(tier_id)
    if t.name == 'Bronze':
        return jsonify({'error': 'Cannot delete the default Bronze tier'}), 400
    db.session.delete(t)
    db.session.commit()
    return jsonify({'message': f'Tier {tier_id} deleted successfully'})


@admin_bp.route('/admin/api/payments/stats', methods=['GET'])
@login_required
def admin_payments_stats():
    if not current_user.is_admin: abort(403)
    
    total_payments = Payment.query.count()
    successful_payments = Payment.query.filter_by(status='paid').count()
    failed_payments = Payment.query.filter_by(status='failed').count()
    refunded_payments = Payment.query.filter_by(status='refunded').count()
    
    # Calculate total revenue (sum of paid amounts)
    revenue_row = db.session.query(db.func.sum(Payment.amount)).filter_by(status='paid').first()
    total_revenue = float(revenue_row[0] or 0.0)
    
    return jsonify({
        'total_revenue': round(total_revenue, 2),
        'total_payments': total_payments,
        'successful_payments': successful_payments,
        'failed_payments': failed_payments,
        'refunded_payments': refunded_payments
    })


@admin_bp.route('/admin/api/payments', methods=['GET'])
@login_required
def admin_payments_list():
    if not current_user.is_admin: abort(403)
    
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    membership = request.args.get('membership', '').strip()
    start_date_str = request.args.get('start_date', '').strip()
    end_date_str = request.args.get('end_date', '').strip()
    
    limit = max(10, min(100, int(request.args.get('limit', 20))))
    page = max(1, int(request.args.get('page', 1)))
    
    query = db.session.query(Payment).join(User, User.id == Payment.user_id)
    
    # Filters
    if search:
        # Search by username, email, payment_id, order_id
        query = query.filter(db.or_(
            User.username.like(f"%{search}%"),
            User.email.like(f"%{search}%"),
            Payment.razorpay_payment_id.like(f"%{search}%"),
            Payment.razorpay_order_id.like(f"%{search}%")
        ))
        
    if status:
        query = query.filter(Payment.status == status)
        
    if membership:
        query = query.filter(Payment.membership_tier == membership)
        
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(Payment.created_at >= start_date)
        except ValueError:
            pass
            
    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d') + datetime.timedelta(days=1)
            query = query.filter(Payment.created_at < end_date)
        except ValueError:
            pass
            
    # Ordering
    query = query.order_by(Payment.created_at.desc())
    
    # Pagination
    total = query.count()
    pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    payments = query.offset(offset).limit(limit).all()
    
    return jsonify({
        'payments': [{
            'id': p.id,
            'user': {
                'id': p.user.id,
                'username': p.user.username,
                'email': p.user.email
            },
            'membership_tier': p.membership_tier,
            'amount': round(p.amount, 2),
            'status': p.status,
            'razorpay_payment_id': p.razorpay_payment_id or '—',
            'razorpay_order_id': p.razorpay_order_id,
            'created_at': p.created_at.strftime('%d %b %Y %H:%M')
        } for p in payments],
        'total': total,
        'pages': pages,
        'current_page': page
    })


@admin_bp.route('/admin/api/payments/<int:payment_id>', methods=['GET'])
@login_required
def admin_payment_detail(payment_id):
    if not current_user.is_admin: abort(403)
    
    p = Payment.query.get_or_404(payment_id)
    webhooks = WebhookLog.query.filter_by(payment_id=p.id).order_by(WebhookLog.created_at.desc()).all()
    
    return jsonify({
        'id': p.id,
        'user': {
            'id': p.user.id,
            'username': p.user.username,
            'email': p.user.email,
            'full_name': p.user.full_name or '—'
        },
        'membership_tier': p.membership_tier,
        'amount': round(p.amount, 2),
        'currency': p.currency,
        'status': p.status,
        'razorpay_order_id': p.razorpay_order_id,
        'razorpay_payment_id': p.razorpay_payment_id or '—',
        'razorpay_signature': p.razorpay_signature or '—',
        'refund_id': p.refund_id or '—',
        'failure_reason': p.failure_reason or '—',
        'created_at': p.created_at.strftime('%d %b %Y %H:%M:%S'),
        'updated_at': p.updated_at.strftime('%d %b %Y %H:%M:%S'),
        'webhooks': [{
            'id': w.id,
            'event_type': w.event_type,
            'payload': json.loads(w.payload) if w.payload else {},
            'created_at': w.created_at.strftime('%d %b %Y %H:%M:%S')
        } for w in webhooks]
    })


@admin_bp.route('/admin/api/payments/<int:payment_id>/refund', methods=['POST'])
@login_required
def admin_payment_refund(payment_id):
    if not current_user.is_admin: abort(403)
    
    p = Payment.query.get_or_404(payment_id)
    if p.status != 'paid':
        return jsonify({'error': 'Only paid transactions can be refunded.'}), 400
        
    if not p.razorpay_payment_id or p.razorpay_payment_id == '—':
        return jsonify({'error': 'No valid Razorpay payment ID found to refund.'}), 400
        
    from razor import gateway
    try:
        # Request refund from Razorpay
        refund = gateway.refund_payment(p.razorpay_payment_id)
        refund_id = refund.get('id')
    except Exception as e:
        print('REFUND ERROR:', e)
        return jsonify({'error': 'Razorpay refund API failed.'}), 500
        
    try:
        # Update payment status in database
        p.status = 'refunded'
        p.refund_id = refund_id
        
        # Reset the user's membership to default Bronze
        user = User.query.get(p.user_id)
        bronze_tier = MembershipTier.query.filter_by(name='Bronze').first()
        
        if user and bronze_tier:
            membership = UserMembership.query.filter_by(user_id=user.id).first()
            if membership:
                membership.tier_id = bronze_tier.id
                membership.usage_cost = 0.0
                membership.usage_percentage = 0.0
                membership.custom_budget_limit = None
                
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print('REFUND DB UPDATE ERROR:', e)
        return jsonify({'error': 'Failed to update database after refund.'}), 500
        
    _log_activity('refund_processed', {
        'payment_id': p.razorpay_payment_id,
        'refund_id': refund_id,
        'amount': p.amount,
        'tier': p.membership_tier
    }, user_id=p.user_id)
    
    # Send refund confirmation email
    try:
        from helpers import send_refund_email
        send_refund_email(p.user.email, p.user.username, p.membership_tier, p.amount, refund_id)
    except Exception as e:
        print(f"[ERROR] Failed to send refund email: {e}")
        
    return jsonify({
        'message': 'Refund processed successfully and user membership downgraded.',
        'refund_id': refund_id
    })


@admin_bp.route('/admin/api/payments/export', methods=['GET'])
@login_required
def admin_payments_export():
    if not current_user.is_admin: abort(403)
    
    import io
    import csv
    from flask import Response
    
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    membership = request.args.get('membership', '').strip()
    start_date_str = request.args.get('start_date', '').strip()
    end_date_str = request.args.get('end_date', '').strip()
    
    query = db.session.query(Payment).join(User, User.id == Payment.user_id)
    
    # Apply filters
    if search:
        query = query.filter(db.or_(
            User.username.like(f"%{search}%"),
            User.email.like(f"%{search}%"),
            Payment.razorpay_payment_id.like(f"%{search}%"),
            Payment.razorpay_order_id.like(f"%{search}%")
        ))
    if status:
        query = query.filter(Payment.status == status)
    if membership:
        query = query.filter(Payment.membership_tier == membership)
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(Payment.created_at >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d') + datetime.timedelta(days=1)
            query = query.filter(Payment.created_at < end_date)
        except ValueError:
            pass
            
    payments = query.order_by(Payment.created_at.desc()).all()
    
    # Generate CSV in memory
    dest = io.StringIO()
    writer = csv.writer(dest)
    
    # Write header
    writer.writerow([
        'Payment ID', 'Username', 'User Email', 'Membership Tier', 
        'Amount (INR)', 'Status', 'Razorpay Payment ID', 
        'Razorpay Order ID', 'Refund ID', 'Failure Reason', 'Date'
    ])
    
    def _sanitize_csv_val(val):
        if val is None:
            return ""
        val_str = str(val)
        if val_str and val_str[0] in ('=', '+', '-', '@', '\t', '\r'):
            return f"'{val_str}"
        return val_str

    # Write rows
    for p in payments:
        writer.writerow([
            p.id,
            _sanitize_csv_val(p.user.username),
            _sanitize_csv_val(p.user.email),
            _sanitize_csv_val(p.membership_tier),
            p.amount,
            p.status,
            _sanitize_csv_val(p.razorpay_payment_id or '—'),
            _sanitize_csv_val(p.razorpay_order_id),
            _sanitize_csv_val(p.refund_id or '—'),
            _sanitize_csv_val(p.failure_reason or '—'),
            p.created_at.strftime('%Y-%m-%d %H:%M:%S')
        ])
        
    output = dest.getvalue()
    dest.close()
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=payments_export.csv"}
    )

