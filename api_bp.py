from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
import json
import threading

from extensions import db, cache, limiter
from models import StudyData, Chat, Message, ContactMessage, AppSettings, ActivityLog, User, UserMembership, MembershipTier, Payment, WebhookLog
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
    if not current_user.is_authenticated:
        return jsonify({'error': 'You must be logged in to send a message.'}), 401

    data = request.get_json() or {}
    name = _sanitize_field(data.get('name', ''), 100)
    email = _sanitize_email(data.get('email', ''))
    subject = _sanitize_field(data.get('subject', ''), 200)
    message = _sanitize(data.get('message', ''), max_words=None)[:1000]

    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if not email:
        return jsonify({'error': 'A valid email address is required'}), 400
    if not subject:
        return jsonify({'error': 'Subject is required'}), 400
    if not message:
        return jsonify({'error': 'Message is required'}), 400

    msg = ContactMessage(
        name=name,
        email=email,
        subject=subject,
        message=message
    )
    db.session.add(msg)
    db.session.commit()
    
    _send_contact_email(name, email, subject, message)
    return jsonify({'message': "Thanks! We'll get back to you within 24 hours."})


@api_bp.route('/upload', methods=['POST'])
@login_required
@limiter.limit(_rl('rl_upload'))
def upload_files():
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402
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
            today_str=get_today(), model_list=get_extract_model_list(),
            user_id=current_user.id)

        # Increment upload count
        current_user.upload_count = (current_user.upload_count or 0) + (len(file_paths) if file_paths else 1)

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
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402
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
                    max_tokens=max_tok_ovr, model_list=model_list,
                    user_id=uid)
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
    if not check_user_budget(current_user.id):
        return jsonify({'error': 'budget_exhausted'}), 402
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
                    max_tokens=max_tok_ovr, model_list=model_list,
                    user_id=uid)
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


@api_bp.route('/subscriptions/purchase/<int:tier_id>', methods=['POST'])
@login_required
def purchase_subscription(tier_id):
    import datetime
    from models import MembershipTier, UserMembership
    tier = MembershipTier.query.filter_by(id=tier_id, active=True).first()
    if not tier:
        return jsonify({'error': 'Subscription plan not found.'}), 404

    membership = UserMembership.query.filter_by(user_id=current_user.id).first()
    
    old_tier = MembershipTier.query.get(membership.tier_id) if membership else None
    
    # Enforce subscription upgrade/renewal logic
    if old_tier:
        old_exhausted = False
        limit = membership.custom_budget_limit if (membership.custom_budget_limit is not None) else old_tier.budget_limit
        if (membership.usage_cost or 0.0) >= limit:
            old_exhausted = True

        if tier.name == 'Bronze':
            return jsonify({'error': 'You cannot purchase the Bronze plan.'}), 400

        if old_tier.name == 'Bronze':
            # Bronze users can always upgrade to Platinum or Diamond
            pass
        elif old_tier.name == 'Platinum':
            if old_exhausted:
                # Can buy Platinum (renew) or Diamond (upgrade/renew)
                pass
            else:
                # Can only buy Diamond (upgrade)
                if tier.name == 'Platinum':
                    return jsonify({'error': 'You already have an active Platinum membership with tokens left.'}), 400
                elif tier.name == 'Bronze':
                    return jsonify({'error': 'You cannot downgrade to the Bronze plan.'}), 400
        elif old_tier.name == 'Diamond':
            if old_exhausted:
                # Can buy Platinum (renew/downgrade to Platinum) or Diamond (renew)
                pass
            else:
                # Cannot buy Platinum (no tokens left checked) or Diamond or Bronze
                if tier.name == 'Platinum':
                    return jsonify({'error': 'You already have an active Diamond membership with tokens left.'}), 400
                elif tier.name == 'Diamond':
                    return jsonify({'error': 'You already have an active Diamond membership with tokens left.'}), 400
                elif tier.name == 'Bronze':
                    return jsonify({'error': 'You cannot downgrade to the Bronze plan.'}), 400

    # Retrieve gateway
    from razor import gateway
    import time
    
    receipt_id = f"rcpt_{current_user.id}_{int(time.time())}"
    
    try:
        # Create Razorpay Order
        order = gateway.create_order(
            amount_in_inr=float(tier.display_price),
            user_id=current_user.id,
            membership_tier=tier.name,
            receipt_id=receipt_id
        )
    except Exception as e:
        return jsonify({'error': f'Failed to create Razorpay Order: {str(e)}'}), 500

    # Store pending payment record in database
    try:
        payment = Payment(
            user_id=current_user.id,
            membership_tier=tier.name,
            amount=float(tier.display_price),
            currency='INR',
            status='pending',
            razorpay_order_id=order['id']
        )
        db.session.add(payment)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to record transaction: {str(e)}'}), 500

    _log_activity('payment_order_created', {
        'order_id': order['id'],
        'tier': tier.name,
        'amount': tier.display_price
    })

    # Return order details to frontend for Checkout popup
    return jsonify({
        'key_id': gateway.key_id,
        'order_id': order['id'],
        'amount': order['amount'],
        'currency': order['currency'],
        'tier_name': tier.name,
        'user': {
            'username': current_user.username,
            'email': current_user.email,
            'full_name': current_user.full_name or ''
        }
    })


def activate_user_membership(payment, payment_id, signature):
    """
    Helper function to verify status and activate user membership.
    Must be run inside a try block.
    """
    import datetime
    
    # Prevent duplicate activations (idempotency check)
    if payment.status != 'pending':
        return
        
    payment.status = 'paid'
    payment.razorpay_payment_id = payment_id
    payment.razorpay_signature = signature
    payment.updated_at = datetime.datetime.utcnow()
    
    user = User.query.get(payment.user_id)
    if not user:
        raise ValueError(f"User with ID {payment.user_id} not found.")
        
    tier = MembershipTier.query.filter_by(name=payment.membership_tier, active=True).first()
    if not tier:
        raise ValueError(f"Membership tier {payment.membership_tier} not found or inactive.")
        
    membership = UserMembership.query.filter_by(user_id=user.id).first()
    old_tier = MembershipTier.query.get(membership.tier_id) if membership else None
    
    bronze_exhausted = False
    if old_tier and old_tier.name == 'Bronze' and (membership.usage_cost >= old_tier.budget_limit):
        bronze_exhausted = True
        
    # Calculate limits rollover (Platinum -> Diamond only)
    if old_tier and old_tier.name == 'Platinum' and tier.name == 'Diamond':
        current_limit = membership.custom_budget_limit if (membership.custom_budget_limit is not None) else old_tier.budget_limit
        remaining = current_limit - (membership.usage_cost or 0.0)
        if remaining > 0:
            membership.custom_budget_limit = tier.budget_limit + remaining
        else:
            membership.custom_budget_limit = None
    else:
        if membership:
            membership.custom_budget_limit = None

    if not membership:
        membership = UserMembership(
            user_id=user.id,
            tier_id=tier.id,
            usage_cost=0.0,
            usage_percentage=0.0,
            total_amount_paid=float(tier.display_price),
            bronze_exhausted_before=bronze_exhausted,
            upgraded_at=datetime.datetime.utcnow()
        )
        db.session.add(membership)
    else:
        membership.tier_id = tier.id
        membership.usage_cost = 0.0
        membership.usage_percentage = 0.0
        membership.total_amount_paid = (membership.total_amount_paid or 0.0) + float(tier.display_price)
        membership.upgraded_at = datetime.datetime.utcnow()
        membership.bronze_exhausted_before = bronze_exhausted
        
    db.session.commit()
    
    _log_activity('upgrade', {'tier': tier.name}, user_id=user.id)
    _log_activity('payment_verified', {
        'payment_id': payment_id,
        'order_id': payment.razorpay_order_id,
        'amount': payment.amount,
        'tier': tier.name
    }, user_id=user.id)
    
    # Send email notifications
    try:
        from helpers import send_payment_success_email
        send_payment_success_email(
            user.email,
            user.username,
            tier.name,
            payment.amount,
            payment_id,
            payment.razorpay_order_id
        )
    except Exception as e:
        print(f"[ERROR] Failed to send payment success email: {e}")


@api_bp.route('/subscriptions/verify', methods=['POST'])
@login_required
def verify_payment():
    from razor import gateway
    import datetime
    
    data = request.get_json() or {}
    order_id = data.get('razorpay_order_id')
    payment_id = data.get('razorpay_payment_id')
    signature = data.get('razorpay_signature')
    
    if not order_id or not payment_id or not signature:
        return jsonify({'error': 'Missing payment verification details.'}), 400
        
    payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
    if not payment:
        return jsonify({'error': 'Payment record not found.'}), 404
        
    if payment.status == 'paid':
        return jsonify({
            'message': 'Payment already verified and membership activated.',
            'tier': payment.membership_tier,
            'payment_id': payment.razorpay_payment_id,
            'order_id': payment.razorpay_order_id,
            'amount': payment.amount,
            'date': payment.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    # Verify signature
    is_valid = gateway.verify_payment_signature(order_id, payment_id, signature)
    if not is_valid:
        try:
            payment.status = 'failed'
            payment.razorpay_payment_id = payment_id
            payment.razorpay_signature = signature
            payment.failure_reason = 'Signature verification failed'
            db.session.commit()
        except Exception:
            db.session.rollback()
            
        _log_activity('payment_failed', {
            'order_id': order_id,
            'payment_id': payment_id,
            'reason': 'Signature verification failed'
        })
        
        try:
            from helpers import send_payment_failed_email
            send_payment_failed_email(
                current_user.email,
                current_user.username,
                payment.membership_tier,
                'Signature verification failed',
                payment_id
            )
        except Exception as e:
            print(f"[ERROR] Failed to send failed payment email: {e}")
            
        return jsonify({'error': 'Payment verification failed. Invalid signature.'}), 400

    # Signature valid, activate plan
    try:
        activate_user_membership(payment, payment_id, signature)
        return jsonify({
            'message': 'Payment verified successfully. Membership activated!',
            'tier': payment.membership_tier,
            'payment_id': payment_id,
            'order_id': order_id,
            'amount': payment.amount,
            'date': payment.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to activate membership: {str(e)}'}), 500


@api_bp.route('/subscriptions/fail', methods=['POST'])
@login_required
def fail_payment():
    import datetime
    data = request.get_json() or {}
    order_id = data.get('razorpay_order_id')
    payment_id = data.get('razorpay_payment_id')
    error_desc = data.get('error_description', 'Payment failed')

    if not order_id:
        return jsonify({'error': 'Missing order ID.'}), 400

    payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
    if not payment:
        return jsonify({'error': 'Payment record not found.'}), 404

    if payment.status == 'pending':
        try:
            payment.status = 'failed'
            payment.razorpay_payment_id = payment_id
            payment.failure_reason = error_desc
            payment.updated_at = datetime.datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': f'Failed to update payment status: {str(e)}'}), 500

        _log_activity('payment_failed', {
            'order_id': order_id,
            'payment_id': payment_id,
            'reason': error_desc
        })

        try:
            from helpers import send_payment_failed_email
            send_payment_failed_email(
                current_user.email,
                current_user.username,
                payment.membership_tier,
                error_desc,
                payment_id
            )
        except Exception as e:
            print(f"[ERROR] Failed to send payment failed email: {e}")

        return jsonify({'message': 'Payment failure recorded and email queued.'})

    return jsonify({'message': f'Payment already in status: {payment.status}'})


@api_bp.route('/payments/webhook', methods=['POST'])
def razorpay_webhook():
    from razor import gateway
    
    payload_body = request.data
    signature = request.headers.get('X-Razorpay-Signature')
    
    if not signature:
        return jsonify({'error': 'Missing signature header'}), 400
        
    is_valid = gateway.verify_webhook_signature(payload_body, signature)
    if not is_valid:
        return jsonify({'error': 'Invalid webhook signature'}), 400
        
    try:
        event_data = json.loads(payload_body.decode('utf-8'))
    except Exception as e:
        return jsonify({'error': f'Invalid JSON payload: {e}'}), 400
        
    event_type = event_data.get('event')
    event_payload = event_data.get('payload', {})
    
    # Extract IDs depending on the event structure
    order_id = None
    payment_id = None
    
    if 'payment' in event_payload:
        payment_entity = event_payload['payment'].get('entity', {})
        order_id = payment_entity.get('order_id')
        payment_id = payment_entity.get('id')
    elif 'refund' in event_payload:
        refund_entity = event_payload['refund'].get('entity', {})
        payment_id = refund_entity.get('payment_id')
        
    payment = None
    if order_id:
        payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
    elif payment_id:
        payment = Payment.query.filter_by(razorpay_payment_id=payment_id).first()
        
    # Log webhook event to database
    try:
        webhook_log = WebhookLog(
            payment_id=payment.id if payment else None,
            event_type=event_type,
            payload=json.dumps(event_data)
        )
        db.session.add(webhook_log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Failed to save webhook log: {e}")
        
    _log_activity('webhook_received', {
        'event': event_type,
        'payment_id': payment_id,
        'order_id': order_id
    }, user_id=payment.user_id if payment else None)

    # Process events
    if event_type == 'payment.captured':
        if payment:
            if payment.status == 'pending':
                try:
                    activate_user_membership(payment, payment_id, signature or payment.razorpay_signature)
                except Exception as e:
                    db.session.rollback()
                    print(f"[ERROR] Webhook payment.captured failed to activate membership: {e}")
            else:
                print(f"[INFO] Webhook payment.captured: Payment {payment.id} already processed (status: {payment.status})")
        else:
            print(f"[WARN] Webhook payment.captured: No payment record found for order ID: {order_id}")
            
    elif event_type == 'payment.failed':
        if payment:
            if payment.status == 'pending':
                payment_entity = event_payload.get('payment', {}).get('entity', {})
                error_desc = payment_entity.get('error_description', 'Transaction failed')
                
                try:
                    payment.status = 'failed'
                    payment.razorpay_payment_id = payment_id
                    payment.failure_reason = error_desc
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    
                _log_activity('payment_failed', {
                    'order_id': order_id,
                    'payment_id': payment_id,
                    'reason': error_desc
                }, user_id=payment.user_id)
                
                try:
                    from helpers import send_payment_failed_email
                    user = User.query.get(payment.user_id)
                    if user:
                        send_payment_failed_email(user.email, user.username, payment.membership_tier, error_desc, payment_id)
                except Exception as e:
                    print(f"[ERROR] Failed to send payment failed email: {e}")
            else:
                print(f"[INFO] Webhook payment.failed: Payment {payment.id} already processed (status: {payment.status})")
                
    elif event_type == 'refund.processed':
        refund_entity = event_payload.get('refund', {}).get('entity', {})
        refund_id = refund_entity.get('id')
        refund_amount = float(refund_entity.get('amount', 0)) / 100.0
        
        if payment:
            try:
                payment.status = 'refunded'
                payment.refund_id = refund_id
                db.session.commit()
            except Exception:
                db.session.rollback()
                
            try:
                user = User.query.get(payment.user_id)
                bronze_tier = MembershipTier.query.filter_by(name='Bronze').first()
                if user and bronze_tier:
                    membership = UserMembership.query.filter_by(user_id=user.id).first()
                    if membership:
                        membership.tier_id = bronze_tier.id
                        membership.usage_cost = 0.0
                        membership.usage_percentage = 0.0
                        membership.custom_budget_limit = None
                        db.session.commit()
                        
                        _log_activity('refund_processed', {
                            'payment_id': payment_id,
                            'refund_id': refund_id,
                            'amount': refund_amount,
                            'tier': payment.membership_tier
                        }, user_id=user.id)
                        
                        try:
                            from helpers import send_refund_email
                            send_refund_email(user.email, user.username, payment.membership_tier, refund_amount, refund_id)
                        except Exception as e:
                            print(f"[ERROR] Failed to send refund email: {e}")
            except Exception as e:
                db.session.rollback()
                print(f"[ERROR] Webhook refund.processed failed to downgrade membership: {e}")
                
    return jsonify({'status': 'ok'}), 200

