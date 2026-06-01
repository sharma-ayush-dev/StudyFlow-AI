from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
import json
import threading

from extensions import db, cache, limiter
from models import StudyData, Chat, Message, ContactMessage, AppSettings, ActivityLog
from helpers import *
from werkzeug.utils import secure_filename
import os

# use Flask's current_app for app context
# will reference current_app inside request / thread contexts
from text_extractor import organize_with_llm
from schedule_planner import generate_schedule, invalidate_schedule_cache

api_bp = Blueprint('api', __name__)


@api_bp.route('/contact/submit', methods=['POST'])
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


@api_bp.route('/upload', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_upload'))
def upload_files():
    file_paths = []
    try:
        files       = request.files.getlist('files')
        manual_text = _sanitize(request.form.get('manual_text', ''), max_words=get_word_limit())
        if not files and not manual_text:
            return jsonify({'error': 'Please upload files or paste text'}), 400
        
        # Enforce file size checks (individual 5MB, combined 15MB)
        total_size = 0
        valid_files_count = 0
        for file in files:
            if not file.filename: continue
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)  # Reset to start of file
            
            if file_size > 5 * 1024 * 1024:
                return jsonify({'error': f'File "{file.filename}" exceeds the maximum 5MB size limit.'}), 400
            
            total_size += file_size
            valid_files_count += 1
            
        if valid_files_count > 0 and total_size > 15 * 1024 * 1024:
            return jsonify({'error': 'Combined size of all files exceeds the maximum 15MB size limit.'}), 400

        for file in files:
            if not file.filename: continue
            filename = secure_filename(file.filename)
            if not filename: continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in current_app.config.get('ALLOWED_EXTENSIONS', {'.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp', '.txt', '.xlsx', '.pptx', '.ppt'}):
                return jsonify({'error': f'Unsupported file type: {ext}'}), 400
            path = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), filename)
            file.save(path)
            file_paths.append(path)
        if file_paths:
            manual_text = ''
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
            existing.generation_inputs_json = None
            existing.pending_generation_inputs_json = None
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


@api_bp.route('/save_extracted/<int:userid>', methods=['POST'])
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


@api_bp.route('/submit_status/<int:userid>', methods=['POST'])
@login_required
def submit_status(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
    payload = _sanitize_topics_payload(request.json or {})
    if 'schedule_preferences' in (request.json or {}):
        payload['schedule_preferences'] = _sanitize_schedule_preferences(
            (request.json or {}).get('schedule_preferences', {}))
    user.topic_status = json.dumps(payload)
    db.session.commit()
    return jsonify({'message': 'saved'})


@api_bp.route('/job/<job_id>/status')
@login_required
def job_status(job_id: str):
    j = _job_get(job_id, current_user.id)
    if j is None:
        return jsonify({'error': 'Job not found'}), 404
    resp = {'status': j['status']}
    if j['status'] == 'done':
        resp['result'] = j['result']
    elif j['status'] == 'error':
        resp['error'] = j['error']
    return jsonify(resp)


@api_bp.route('/generate_schedule/<int:userid>', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_generate'))
def generate(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user or not user.topic_status:
        return jsonify({'error': 'No topic status found'}), 400
    topic_data  = json.loads(user.topic_status)
    generation_inputs = _generation_inputs_snapshot(topic_data, 'status_page')
    today_str   = get_today()
    model_list  = get_sched_model_list()
    max_tok_ovr = get_max_tokens()
    uid         = current_user.id
    job_id      = _job_create(uid)
    app         = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                schedule = generate_schedule(
                    topic_data, today_str=today_str,
                    max_tokens=max_tok_ovr, model_list=model_list)
                schedule, meta = _extract_meta(schedule)
                sd = StudyData.query.filter_by(userid=uid).first()
                if sd:
                    sd.schedule_json = json.dumps(schedule)
                    sd.generation_inputs_json = json.dumps(generation_inputs)
                    db.session.commit()
                old_chats = Chat.query.filter_by(userid=uid).all()
                for oc in old_chats:
                    Message.query.filter_by(chat_id=oc.id).delete()
                Chat.query.filter_by(userid=uid).delete()
                db.session.commit()
                cache.delete(f'schedule_{uid}')
                _log_activity('generate')
                _job_set(job_id, 'done', result={
                    'schedule': schedule,
                    'notice':   _build_llm_notice(meta)
                })
            except ValueError as e:
                _job_set(job_id, 'error', error=str(e))
            except Exception as e:
                _job_set(job_id, 'error', error=f'Generation failed: {e}')

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'job_id': job_id, 'status': 'pending'})


@api_bp.route('/schedule/<int:userid>')
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


@api_bp.route('/update_progress/<int:userid>', methods=['POST'])
@login_required
def update_progress(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
    req_json = request.json or {}
    updated_subjects = _sanitize_topics_payload(req_json.get('Subjects', {}))
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
                try:
                    pct = max(0, min(100, int(val if not isinstance(val, dict) else val.get('status', 0))))
                    existing_topic['status'] = str(pct)
                except (ValueError, TypeError): pass
            else:
                try:
                    pct = max(0, min(100, int(val)))
                    status['Subjects'][subj][topic] = str(pct)
                except (ValueError, TypeError): pass
    updated_days = _sanitize_study_days_payload(req_json.get('study_days', {}))
    if updated_days:
        status['study_days'] = updated_days
    if 'schedule_preferences' in req_json:
        status['schedule_preferences'] = _sanitize_schedule_preferences(
            req_json.get('schedule_preferences', {}))
    user.topic_status = json.dumps(status)
    db.session.commit()
    invalidate_schedule_cache(status, get_today())
    return jsonify({'message': 'progress saved'})


@api_bp.route('/regenerate_schedule/<int:userid>', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_generate'))
def regenerate_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user or not user.topic_status:
        return jsonify({'error': 'No topic status found'}), 400
    topic_data  = json.loads(user.topic_status)
    generation_inputs = _generation_inputs_snapshot(topic_data, 'progress_page')
    old_sched   = json.loads(user.schedule_json) if user.schedule_json else {}
    today_str   = get_today()
    model_list  = get_sched_model_list()
    max_tok_ovr = get_max_tokens()
    uid         = current_user.id
    job_id      = _job_create(uid)
    app         = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                new_schedule = generate_schedule(
                    topic_data, today_str=today_str,
                    max_tokens=max_tok_ovr, model_list=model_list)
                new_schedule, meta = _extract_meta(new_schedule)
                sd = StudyData.query.filter_by(userid=uid).first()
                if sd:
                    sd.pending_schedule_json = json.dumps(new_schedule)
                    sd.pending_generation_inputs_json = json.dumps(generation_inputs)
                    db.session.commit()
                _log_activity('regenerate')
                _job_set(job_id, 'done', result={
                    'old_schedule': old_sched,
                    'new_schedule': new_schedule,
                    'notice':       _build_llm_notice(meta)
                })
            except ValueError as e:
                _job_set(job_id, 'error', error=str(e))
            except Exception as e:
                _job_set(job_id, 'error', error=f'Regeneration failed: {e}')

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'job_id': job_id, 'status': 'pending'})


@api_bp.route('/keep_schedule/<int:userid>', methods=['POST'])
@login_required
def keep_schedule(userid):
    _require_owner(userid)
    user = _get_study_data()
    if not user: return jsonify({'error': 'No data found'}), 404
    choice = _sanitize_field((request.json or {}).get('choice', 'old'), 10)
    if choice == 'new' and user.pending_schedule_json:
        user.schedule_json = user.pending_schedule_json
        user.generation_inputs_json = user.pending_generation_inputs_json
    user.pending_schedule_json = None
    user.pending_generation_inputs_json = None
    db.session.commit()
    cache.delete(f'schedule_{current_user.id}')
    return jsonify({'message': 'saved', 'choice': choice})


@api_bp.route('/me')
@login_required
def me():
    return jsonify({'id': current_user.id, 'username': current_user.username,
                    'is_admin': current_user.is_admin})
