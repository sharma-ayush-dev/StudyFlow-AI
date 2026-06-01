from flask import Blueprint, render_template, redirect, url_for, request, jsonify, session
from flask_login import login_required, current_user
import json
import datetime
import urllib.parse
import re

from extensions import db, cache, limiter
from models import Chat, StudyData, Message, User
from helpers import *

pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/upload_page', endpoint='upload_page')
@login_required
def upload_page():
    return render_template('Upload-page.html', word_limit=get_word_limit())


@pages_bp.route('/status', endpoint='status')
@login_required
def status_page():
    user = _get_study_data()
    if not user: return redirect(url_for('upload_page'))
    data = json.loads(user.extracted_json)
    if user.topic_status:
        saved = json.loads(user.topic_status)
        if 'study_days' in saved:
            data['study_days'] = saved['study_days']
        if 'Subjects' in saved:
            for subj, topics in saved['Subjects'].items():
                if subj in data.get('Subjects', {}):
                    for t, tdata in topics.items():
                        if t in data['Subjects'][subj]:
                            data['Subjects'][subj][t] = tdata
        if 'schedule_preferences' in saved:
            data['schedule_preferences'] = saved['schedule_preferences']
    pref_limit = get_sched_pref_limit()
    return render_template('Status.html', data=data, pref_limit=pref_limit)


@pages_bp.route('/schedule_page', endpoint='schedule_page')
@login_required
def schedule_page():
    return render_template('Schedule.html')


@pages_bp.route('/progress_page', endpoint='progress_page')
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
        userid=current_user.id, is_admin=current_user.is_admin,
        pref_limit=get_sched_pref_limit())


def _safe_parse_dmy(s):
    try: return parse_dmy(s)
    except: return None


@pages_bp.route('/study/<subject>/<topic>', endpoint='study_page')
@login_required
def study_page(subject: str, topic: str):
    subject = _sanitize_field(urllib.parse.unquote(subject), 200)
    topic   = _sanitize_field(urllib.parse.unquote(topic),   200)
    schedule_date = _sanitize_field(request.args.get('date', ''), 20) or get_today()

    user_data = _get_study_data()
    if not user_data: return redirect(url_for('upload_page'))

    schedule  = json.loads(user_data.schedule_json) if user_data.schedule_json else {}
    today_str = get_today()
    slot_date = schedule_date or today_str
    slot      = _get_today_slot(schedule, subject, topic, slot_date)
    hours     = slot.get('hours')
    subtopics = slot.get('subtopics', [])

    if not subtopics and user_data.extracted_json:
        extracted = json.loads(user_data.extracted_json)
        topic_data = (extracted.get('Subjects', {})
                               .get(subject, {})
                               .get(topic, {}))
        if isinstance(topic_data, dict):
            subtopics = topic_data.get('subtopics', [])

    chat = Chat.query.filter_by(
        userid=current_user.id, subject=subject, topic=topic,
        schedule_date=schedule_date).first()
    if not chat:
        chat = Chat(userid=current_user.id, subject=subject, topic=topic,
                    schedule_date=schedule_date)
        db.session.add(chat)
        db.session.commit()

    return render_template('Study.html',
        subject    = subject,
        topic      = topic,
        subtopics  = subtopics,
        hours      = hours,
        chat_id    = chat.id,
        userid     = current_user.id,
        today_str  = today_str,
        schedule_date = schedule_date or ''
    )


@pages_bp.route('/privacy', endpoint='privacy')
def privacy():
    return render_template('privacy.html')


@pages_bp.route('/terms', endpoint='terms')
def terms():
    return render_template('terms.html')


@pages_bp.route('/contact', endpoint='contact')
def contact():
    return render_template('contact.html')


@pages_bp.route('/settings', endpoint='settings_page')
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


@pages_bp.route('/settings/update', methods=['POST'], endpoint='settings_update')
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
