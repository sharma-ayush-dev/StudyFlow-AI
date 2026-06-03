from flask import Blueprint, request, jsonify, Response, stream_with_context
from flask_login import login_required, current_user
import json
import datetime

from extensions import db, limiter
from models import Chat, Message, User, StudyData
from helpers import *
from teacher import stream_reply, stream_quiz, get_initial_message, get_reply, get_quiz

chat_bp = Blueprint('chat', __name__)


def _get_chat_or_403(chat_id: int) -> Chat:
    chat = Chat.query.get_or_404(chat_id)
    if chat.userid != current_user.id and not current_user.is_admin:
        from flask import abort
        abort(403)
    return chat


def _get_chat_history(chat_id: int) -> list:
    msgs = (Message.query
            .filter_by(chat_id=chat_id)
            .order_by(Message.timestamp)
            .all())
    return [{'role': m.role, 'content': m.content} for m in msgs]


def _save_message(chat_id: int, role: str, content: str, chat: Chat = None) -> Message:
    msg = Message(chat_id=chat_id, role=role, content=content)
    db.session.add(msg)
    if chat is None:
        chat = db.session.get(Chat, chat_id)
    if chat:
        chat.updated_at = datetime.datetime.utcnow()
    db.session.commit()
    return msg


def _get_topic_context(chat: Chat) -> tuple:
    user = User.query.get(chat.userid)
    course = user.course if user else None
    user_data = StudyData.query.filter_by(userid=chat.userid).first()
    if not user_data: return [], None, course

    schedule = json.loads(user_data.schedule_json) if user_data.schedule_json else {}
    today_str = get_today()
    slot_date = chat.schedule_date or today_str
    slot = _get_today_slot(schedule, chat.subject, chat.topic, slot_date)
    hours = slot.get('hours')
    subtopics = slot.get('subtopics', [])

    if not subtopics and user_data.extracted_json:
        extracted = json.loads(user_data.extracted_json)
        tdata = (extracted.get('Subjects', {})
                          .get(chat.subject, {})
                          .get(chat.topic, {}))
        if isinstance(tdata, dict):
            subtopics = tdata.get('subtopics', [])

    return subtopics, hours, course


@chat_bp.route('/api/chat/<int:chat_id>/history')
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


@chat_bp.route('/api/chat/<int:chat_id>/start', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_start(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

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
            use_chinese= get_use_chinese(),
            user_id    = chat.userid
        )
        _save_message(chat_id, 'assistant', content, chat=chat)
        _log_activity('study_start', {'subject': chat.subject, 'topic': chat.topic})
        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'message': 'A backup AI assistant was automatically selected.'}
        return jsonify(resp)
    except RuntimeError as e:
        if 'budget_exhausted' in str(e):
            return jsonify({'error': 'budget_exhausted'}), 402
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/api/chat/<int:chat_id>/send', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_send(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

    user_message = _sanitize((request.json or {}).get('message', ''), max_words=500)
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400
    _save_message(chat_id, 'user', user_message, chat=chat)
    history = _get_chat_history(chat_id)[:-1]
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
            use_chinese = get_use_chinese(),
            user_id     = chat.userid
        )
        _save_message(chat_id, 'assistant', content)
        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'message': 'A backup AI assistant was automatically selected.'}
        return jsonify(resp)
    except RuntimeError as e:
        if 'budget_exhausted' in str(e):
            return jsonify({'error': 'budget_exhausted'}), 402
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/api/chat/<int:chat_id>/quiz', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_quiz(chat_id: int):
    chat    = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

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
            use_chinese = get_use_chinese(),
            user_id     = chat.userid
        )
        _save_message(chat_id, 'assistant', content)
        resp = {'content': content, 'role': 'assistant'}
        if failures:
            resp['notice'] = {'message': 'A backup AI assistant was automatically selected.'}
        return jsonify(resp)
    except RuntimeError as e:
        if 'budget_exhausted' in str(e):
            return jsonify({'error': 'budget_exhausted'}), 402
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/api/chat/<int:chat_id>/message/<int:msg_id>', methods=['PATCH'])
@login_required
def edit_message(chat_id: int, msg_id: int):
    _get_chat_or_403(chat_id)
    msg = Message.query.get_or_404(msg_id)
    if msg.chat_id != chat_id:
        from flask import abort
        abort(403)
    if msg.role != 'user':
        return jsonify({'error': 'Only user messages can be edited'}), 400
    new_content = _sanitize((request.json or {}).get('content', ''), max_words=500)
    if not new_content:
        return jsonify({'error': 'Empty message'}), 400
    msg.content = new_content
    db.session.commit()
    return jsonify({'message': 'edited', 'content': new_content})


@chat_bp.route('/api/chat/<int:chat_id>/message/<int:msg_id>', methods=['DELETE'])
@login_required
def delete_message(chat_id: int, msg_id: int):
    _get_chat_or_403(chat_id)
    msg = Message.query.get_or_404(msg_id)
    if msg.chat_id != chat_id:
        from flask import abort
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


@chat_bp.route('/api/chat/<int:chat_id>/regenerate_last', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def regenerate_last(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

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
            use_chinese=get_use_chinese(),
            user_id=chat.userid
        )
        new_msg = _save_message(chat_id, 'assistant', content)
        resp = {'content': content, 'role': 'assistant', 'id': new_msg.id}
        if failures:
            resp['notice'] = {'message': 'A backup AI assistant was automatically selected.'}
        return jsonify(resp)
    except RuntimeError as e:
        if 'budget_exhausted' in str(e):
            return jsonify({'error': 'budget_exhausted'}), 402
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/api/chat/<int:chat_id>/send/stream', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_send_stream(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

    user_message = _sanitize((request.json or {}).get('message', ''), max_words=500)
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400
    user_msg = _save_message(chat_id, 'user', user_message)
    user_msg_id = user_msg.id
    history = _get_chat_history(chat_id)[:-1]
    subtopics, hours, course = _get_topic_context(chat)
    model_list = get_teacher_model_list()
    use_zh = get_use_chinese()
    chat_subject = chat.subject
    chat_topic   = chat.topic

    def generate():
        full_text = ''
        try:
            for chunk in stream_reply(
                history=history,
                new_message=user_message,
                course=course or '',
                subject=chat_subject,
                topic=chat_topic,
                subtopics=subtopics,
                hours=hours,
                model_list=model_list,
                use_chinese=use_zh,
                user_id=chat.userid
            ):
                full_text += chunk
                yield f"data: {json.dumps(chunk)}\n\n"
            asst_msg = _save_message(chat_id, 'assistant', full_text)
            asst_msg_id = asst_msg.id
            yield f"data: [DONE]{json.dumps({'user_msg_id': user_msg_id, 'assistant_msg_id': asst_msg_id})}\n\n"
        except Exception as e:
            msg = str(e)
            if 'budget_exhausted' in msg:
                msg = 'budget_exhausted'
            yield f'data: [ERROR] {msg}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@chat_bp.route('/api/chat/<int:chat_id>/quiz/stream', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_chat'))
def chat_quiz_stream(chat_id: int):
    chat = _get_chat_or_403(chat_id)
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402

    history = _get_chat_history(chat_id)
    if len(history) < 2:
        return jsonify({'error': 'Study a bit first before taking a quiz!'}), 400
    quiz_user_msg = _save_message(chat_id, 'user', 'Quiz me on what we have covered so far.', chat=chat)
    quiz_user_msg_id = quiz_user_msg.id
    subtopics, hours, course = _get_topic_context(chat)
    model_list = get_teacher_model_list()
    use_zh = get_use_chinese()
    chat_subject = chat.subject
    chat_topic   = chat.topic

    def generate():
        full_text = ''
        try:
            for chunk in stream_quiz(
                history=history,
                course=course or '',
                subject=chat_subject,
                topic=chat_topic,
                subtopics=subtopics,
                hours=hours,
                model_list=model_list,
                use_chinese=use_zh,
                user_id=chat.userid
            ):
                full_text += chunk
                yield f"data: {json.dumps(chunk)}\n\n"

            asst_msg = _save_message(chat_id, 'assistant', full_text)
            asst_msg_id = asst_msg.id
            yield f"data: [DONE_QUIZ]{json.dumps({'user_msg_id': quiz_user_msg_id, 'assistant_msg_id': asst_msg_id})}\n\n"
        except Exception as e:
            msg = str(e)
            if 'budget_exhausted' in msg:
                msg = 'budget_exhausted'
            yield f'data: [ERROR] {msg}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
