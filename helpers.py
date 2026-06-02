import threading
import time
import datetime
import json
import re
import os
import urllib.parse
import secrets as _secrets
from flask import render_template, abort, session, request
from flask_login import current_user
from extensions import cache, RATE_LIMIT_DEFAULTS, DEFAULT_WORD_LIMIT, DEFAULT_SCHED_PREF_LIMIT
from extensions import _settings_cache_lock
from models import AppSettings, StudyData, ActivityLog, PasswordResetOTP, User, EmailOTP
from extensions import db
from schedule_planner import MODELS as SCHED_MODELS
from text_extractor import VISION_MODELS
from teacher import TEACHER_MODELS

# Explicit exports: include underscore-prefixed helpers used by app.py
__all__ = [
    '_reload_settings_cache', '_get_setting', '_set_setting', 'get_today', 'parse_dmy',
    'get_max_tokens', 'get_word_limit', 'get_sched_pref_limit', '_parse_model_list',
    'get_use_chinese', '_sanitize', '_sanitize_field', '_sanitize_email',
    '_sanitize_topics_payload', '_sanitize_study_days_payload', '_sanitize_schedule_preferences',
    '_generation_inputs_snapshot', '_rl', '_get_study_data', '_ensure_runtime_schema',
    '_require_owner', '_delete_files', '_render_error', '_log_activity', '_extract_meta',
    '_build_llm_notice', '_get_today_slot', 'HARDCODED_OTP', '_send_otp_email', '_send_welcome_email',
    '_create_otp', '_verify_otp', '_create_login_otp', '_verify_login_otp', 'log_request', '_job_create', '_job_set', '_job_get',
    'get_sched_model_list', 'get_extract_model_list', 'get_teacher_model_list',
    'get_model_costs', 'save_model_costs', 'track_llm_call', 'check_user_cost_limit'
]


def get_sched_model_list() -> list:
    return _parse_model_list('sched_model_list', SCHED_MODELS)


def get_extract_model_list() -> list:
    return _parse_model_list('extract_model_list', VISION_MODELS)


def get_teacher_model_list() -> list:
    return _parse_model_list('teacher_model_list', TEACHER_MODELS)

# Settings cache
_settings_cache = {}
_settings_cache_ts = 0.0
SETTINGS_CACHE_TTL = 30

# Async job store
_jobs = {}
_jobs_lock = threading.Lock()
JOB_TTL_S = 1800


def _reload_settings_cache():
    global _settings_cache, _settings_cache_ts
    try:
        rows = AppSettings.query.all()
        with _settings_cache_lock:
            _settings_cache = {r.key: r.value for r in rows}
            _settings_cache_ts = time.time()
    except Exception as exc:
        print(f'[SETTINGS] Cache reload failed: {exc}')


def _get_setting(key: str, fallback=None):
    global _settings_cache_ts
    if time.time() - _settings_cache_ts > SETTINGS_CACHE_TTL:
        _reload_settings_cache()
    with _settings_cache_lock:
        return _settings_cache.get(key, fallback)


def _set_setting(key: str, value: str):
    global _settings_cache_ts
    row = AppSettings.query.get(key)
    if row:
        row.value = value
    else:
        db.session.add(AppSettings(key=key, value=value))
    db.session.commit()
    with _settings_cache_lock:
        _settings_cache_ts = 0.0


def get_today() -> str:
    val = _get_setting('test_today')
    return val if val else datetime.date.today().strftime('%d-%m-%Y')


def parse_dmy(s: str) -> datetime.date:
    d, m, y = s.strip().split('-')
    return datetime.date(int(y), int(m), int(d))


def get_max_tokens():
    try:
        v = _get_setting('max_tokens')
        return int(v) if v else None
    except:
        return None


def get_word_limit() -> int:
    try:
        return int(_get_setting('text_word_limit') or DEFAULT_WORD_LIMIT)
    except:
        return DEFAULT_WORD_LIMIT


def get_sched_pref_limit() -> int:
    try:
        return int(_get_setting('sched_pref_limit') or DEFAULT_SCHED_PREF_LIMIT)
    except:
        return DEFAULT_SCHED_PREF_LIMIT


def _parse_model_list(key: str, default: list) -> list:
    val = _get_setting(key)
    if val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed:
                return parsed
        except:
            pass
    return default


def get_use_chinese() -> bool:
    return _get_setting('use_chinese_prompts', 'false').lower() == 'true'


def _sanitize(text: str, max_words: int = None) -> str:
    if not isinstance(text, str):
        return ''
    text = text.replace('\x00', '')
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if max_words:
        words = text.split()
        if len(words) > max_words:
            text = ' '.join(words[:max_words])
    return text


def _sanitize_field(s: str, max_len: int = 200) -> str:
    if not isinstance(s, str):
        return ''
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return s[:max_len].strip()


def _sanitize_email(s: str) -> str:
    s = _sanitize_field(s, 120).lower()
    return s if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', s) else ''


def _sanitize_topics_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    clean = {}
    for k, v in payload.items():
        ck = _sanitize_field(str(k))
        if isinstance(v, dict):
            clean[ck] = _sanitize_topics_payload(v)
        elif isinstance(v, list):
            clean[ck] = [_sanitize_field(str(i)) for i in v if str(i).strip()]
        elif isinstance(v, str):
            clean[ck] = _sanitize_field(v)
        elif isinstance(v, (int, float)):
            clean[ck] = v
        else:
            clean[ck] = _sanitize_field(str(v))
    return clean


def _sanitize_study_days_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    clean = {}
    for date, hours in payload.items():
        date_key = _sanitize_field(str(date), 20)
        try:
            value = max(0, min(24, int(hours)))
        except (TypeError, ValueError):
            value = 0
        clean[date_key] = str(value)
    return clean


def _sanitize_schedule_preferences(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {
        'preference_note': _sanitize_field(payload.get('preference_note', ''), get_sched_pref_limit()),
    }


def _generation_inputs_snapshot(topic_data: dict, source: str) -> dict:
    return {
        'source': source,
        'saved_at': datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'user_inputs': topic_data
    }


def _rl(key: str):
    def _limit():
        try:
            if current_user.is_authenticated and current_user.is_admin:
                return '10000 per hour'
        except:
            pass
        return _get_setting(key, RATE_LIMIT_DEFAULTS[key])
    return _limit


def _get_study_data() -> StudyData | None:
    return StudyData.query.filter_by(userid=current_user.id).first()


def _ensure_runtime_schema():
    try:
        with db.engine.connect() as conn:
            rows = conn.exec_driver_sql("PRAGMA table_info(study_data)").fetchall()
            existing = {row[1] for row in rows}
            if 'generation_inputs_json' not in existing:
                conn.exec_driver_sql("ALTER TABLE study_data ADD COLUMN generation_inputs_json TEXT")
            if 'pending_generation_inputs_json' not in existing:
                conn.exec_driver_sql("ALTER TABLE study_data ADD COLUMN pending_generation_inputs_json TEXT")

            rows_chat = conn.exec_driver_sql("PRAGMA table_info(chat)").fetchall()
            existing_chat = {row[1] for row in rows_chat}
            if 'schedule_date' not in existing_chat:
                conn.exec_driver_sql("ALTER TABLE chat ADD COLUMN schedule_date VARCHAR(20)")

            rows_user = conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()
            existing_user = {row[1] for row in rows_user}
            if 'upload_count' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN upload_count INTEGER DEFAULT 0 NOT NULL")
            if 'generations_count' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN generations_count INTEGER DEFAULT 0 NOT NULL")
            if 'last_active' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN last_active DATETIME")
            if 'input_tokens_used' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN input_tokens_used INTEGER DEFAULT 0 NOT NULL")
            if 'output_tokens_used' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN output_tokens_used INTEGER DEFAULT 0 NOT NULL")
            if 'last_model_used' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN last_model_used VARCHAR(100)")
            if 'total_cost' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN total_cost FLOAT DEFAULT 0.0 NOT NULL")
            if 'cost_limit' not in existing_user:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN cost_limit FLOAT DEFAULT 1000.0 NOT NULL")
            else:
                # Update users who have the old $10.0 limit to the new ₹1000.0 limit
                conn.exec_driver_sql("UPDATE user SET cost_limit = 1000.0 WHERE cost_limit = 10.0")

            conn.commit()
    except Exception as exc:
        print(f'[SCHEMA] Runtime migration skipped: {exc}')


def _require_owner(userid: int):
    if userid != current_user.id and not current_user.is_admin:
        abort(403)


def _delete_files(paths: list):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f'[WARN] {p}: {e}')


def _render_error(code, title, message):
    return render_template('error.html', code=code, title=title, message=message), code


def _log_activity(action: str, detail: dict = None):
    try:
        db.session.add(ActivityLog(
            userid = current_user.id if current_user.is_authenticated else None,
            action = action,
            detail = json.dumps(detail) if detail else None
        ))
        db.session.commit()
    except:
        db.session.rollback()


def _extract_meta(schedule: dict) -> tuple:
    meta = schedule.pop('_meta', {})
    return schedule, meta


def _build_llm_notice(meta: dict) -> dict | None:
    if not meta.get('primary_failed'):
        return None
    return {'type': 'warning', 'model': meta.get('model_used', 'unknown'), 'reasons': meta.get('failure_reasons', [])}


def _get_today_slot(schedule: dict, subject: str, topic: str, today_str: str) -> dict:
    day = schedule.get(today_str, {})
    return day.get(subject, {}).get(topic, {})


HARDCODED_OTP = '1234'


def _send_otp_email(email: str, otp: str):
    # Get Resend configuration directly from environment variables
    resend_api_key = os.environ.get('RESEND_API_KEY')

    if not resend_api_key:
        print("[OTP EMAIL] RESEND_API_KEY not configured. Skipping email delivery.")
        return

    def send_bg():
        import resend
        try:
            resend.api_key = resend_api_key
            from_email = os.environ.get('SMTP_FROM_EMAIL', 'auth@studyflowai.app')
            from_name = os.environ.get('SMTP_FROM_NAME', 'StudyFlow-AI')

            # Styled HTML template with StudyFlow premium dark styling
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>StudyFlow-AI Verification</title>
                <style>
                    body {{
                        background-color: #050505;
                        color: #ffffff;
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 40px 20px;
                    }}
                    .card {{
                        background-color: #0c0c15;
                        border: 1px solid rgba(123, 47, 247, 0.3);
                        border-radius: 20px;
                        padding: 40px;
                        text-align: center;
                        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                    }}
                    .logo {{
                        font-size: 24px;
                        font-weight: bold;
                        letter-spacing: 2px;
                        color: #ffffff;
                        margin-bottom: 24px;
                        text-decoration: none;
                    }}
                    .title {{
                        font-size: 20px;
                        margin-bottom: 10px;
                        color: #ffffff;
                    }}
                    .subtitle {{
                        font-size: 14px;
                        color: #888888;
                        margin-bottom: 30px;
                        line-height: 1.5;
                    }}
                    .otp-box {{
                        background: linear-gradient(135deg, rgba(123, 47, 247, 0.1), rgba(159, 85, 255, 0.1));
                        border: 1px dashed #7b2ff7;
                        border-radius: 12px;
                        padding: 20px;
                        font-size: 32px;
                        font-weight: bold;
                        letter-spacing: 6px;
                        color: #9f55ff;
                        margin: 20px 0;
                        display: inline-block;
                    }}
                    .footer {{
                        margin-top: 40px;
                        font-size: 12px;
                        color: #555555;
                        line-height: 1.5;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="card">
                        <div class="logo" style="letter-spacing: 2px; font-weight: bold;">StudyFlow</div>
                        <h2 class="title" style="margin-top: 20px;">Verification Code</h2>
                        <p class="subtitle">Please use the 6-digit OTP code below to sign in or reset your password on StudyFlow-AI. This code is valid for 10 minutes.</p>
                        
                        <div class="otp-box">{otp}</div>
                        
                        <p class="subtitle" style="font-size:12px; margin-top:20px;">If you did not request this, you can safely ignore this email.</p>
                        
                        <div class="footer">
                            &copy; 2026 StudyFlow-AI. All rights reserved.<br>
                            Sent from {from_email}
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"Your StudyFlow-AI Verification Code is: {otp}\n\nValid for 10 minutes."

            params = {
                "from": f"{from_name} <{from_email}>",
                "to": [email],
                "subject": f"Your StudyFlow-AI Verification Code: {otp}",
                "html": html_content,
                "text": text_content
            }

            resend.Emails.send(params)
            print(f"[RESEND EMAIL SUCCESS] Successfully sent OTP email to {email}")
        except Exception as e:
            print(f"[RESEND EMAIL ERROR] Failed to send OTP email to {email}: {e}")

    threading.Thread(target=send_bg, daemon=True).start()


def _send_welcome_email(email: str, username: str):
    resend_api_key = os.environ.get('RESEND_API_KEY')

    if not resend_api_key:
        print("[WELCOME EMAIL] RESEND_API_KEY not configured. Skipping welcome email.")
        return

    def send_bg():
        import resend
        try:
            resend.api_key = resend_api_key
            from_email = os.environ.get('SMTP_FROM_EMAIL', 'auth@studyflowai.app')
            from_name = os.environ.get('SMTP_FROM_NAME', 'StudyFlow-AI')

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Welcome to StudyFlow-AI</title>
                <style>
                    body {{
                        background-color: #050505;
                        color: #ffffff;
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 40px 20px;
                    }}
                    .card {{
                        background-color: #0c0c15;
                        border: 1px solid rgba(123, 47, 247, 0.3);
                        border-radius: 20px;
                        padding: 40px;
                        text-align: center;
                        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                    }}
                    .logo {{
                        font-size: 26px;
                        font-weight: bold;
                        letter-spacing: 2px;
                        color: #ffffff;
                        margin-bottom: 24px;
                        text-decoration: none;
                    }}
                    .title {{
                        font-size: 22px;
                        margin-bottom: 15px;
                        color: #9f55ff;
                    }}
                    .subtitle {{
                        font-size: 14px;
                        color: #cccccc;
                        margin-bottom: 30px;
                        line-height: 1.6;
                        text-align: left;
                    }}
                    .feature-list {{
                        text-align: left;
                        margin: 25px 0;
                        padding-left: 20px;
                    }}
                    .feature-item {{
                        margin-bottom: 12px;
                        font-size: 14px;
                        color: #ffffff;
                    }}
                    .feature-item strong {{
                        color: #9f55ff;
                    }}
                    .cta-btn {{
                        display: inline-block;
                        background: linear-gradient(135deg, #7b2ff7, #9f55ff);
                        color: #ffffff !important;
                        text-decoration: none;
                        padding: 12px 30px;
                        border-radius: 25px;
                        font-weight: bold;
                        font-size: 15px;
                        margin-top: 15px;
                        box-shadow: 0 4px 15px rgba(123, 47, 247, 0.4);
                    }}
                    .footer {{
                        margin-top: 40px;
                        font-size: 12px;
                        color: #555555;
                        line-height: 1.5;
                        border-top: 1px solid rgba(255, 255, 255, 0.05);
                        padding-top: 20px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="card">
                        <div class="logo">StudyFlow</div>
                        <h2 class="title">Welcome, {username}!</h2>
                        <p class="subtitle">Thank you for joining StudyFlow-AI. We're thrilled to help you plan your studies smarter, optimize your schedule, and ace your goals.</p>
                        
                        <div class="subtitle">
                            <strong>Here is what you can do with your new account:</strong>
                            <ul class="feature-list">
                                <li class="feature-item"><strong>Optimize Schedules</strong>: Generate AI-tailored daily study plans.</li>
                                <li class="feature-item"><strong>Track Progress</strong>: Check off topics as you finish studying them.</li>
                                <li class="feature-item"><strong>Study Chat</strong>: Talk to your personal AI study assistant for questions on any topic.</li>
                            </ul>
                        </div>
                        
                        <a href="http://127.0.0.1:5000" class="cta-btn">Get Started</a>
                        
                        <div class="footer">
                            &copy; 2026 StudyFlow-AI. All rights reserved.<br>
                            Sent to {email}
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"Welcome to StudyFlow-AI, {username}!\n\nGet started planning your studies: http://127.0.0.1:5000"

            params = {
                "from": f"{from_name} <{from_email}>",
                "to": [email],
                "subject": "Welcome to StudyFlow-AI! 🚀",
                "html": html_content,
                "text": text_content
            }

            resend.Emails.send(params)
            print(f"[WELCOME EMAIL SUCCESS] Successfully sent welcome email to {email}")
        except Exception as e:
            print(f"[WELCOME EMAIL ERROR] Failed to send welcome email to {email}: {e}")

    threading.Thread(target=send_bg, daemon=True).start()


def _create_otp(email: str) -> PasswordResetOTP:
    PasswordResetOTP.query.filter_by(email=email, used=False).update({'used': True})
    db.session.commit()

    import random
    otp_code = f"{random.randint(100000, 999999)}"

    otp = PasswordResetOTP(
        email=email,
        otp_code=otp_code,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
    )
    db.session.add(otp)
    db.session.commit()
    _send_otp_email(email, otp_code)
    return otp


def _verify_otp(email: str, code: str) -> bool:
    now = datetime.datetime.utcnow()
    rec = (PasswordResetOTP.query
           .filter_by(email=email, otp_code=code, used=False)
           .filter(PasswordResetOTP.expires_at > now)
           .first())
    if not rec:
        return False
    rec.used = True
    db.session.commit()
    return True


def _create_login_otp(email: str) -> EmailOTP:
    # Mark old login OTPs as used
    EmailOTP.query.filter_by(email=email, used=False).update({'used': True})
    db.session.commit()

    import random
    otp_code = f"{random.randint(100000, 999999)}"

    otp = EmailOTP(
        email=email,
        otp_code=otp_code,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    )
    db.session.add(otp)
    db.session.commit()

    _send_otp_email(email, otp_code)
    return otp



def _verify_login_otp(email: str, code: str) -> bool:
    now = datetime.datetime.utcnow()
    rec = (EmailOTP.query
           .filter_by(email=email, otp_code=code, used=False)
           .filter(EmailOTP.expires_at > now)
           .first())
    if not rec:
        return False
    rec.used = True
    db.session.commit()
    return True



# Request logging helper used as an after_request hook in app
def log_request(response):
    p = request.path
    if (p.startswith('/static') or p.startswith('/admin') or p.startswith('/job/')):
        return response
    uid = current_user.id if current_user.is_authenticated else None
    method = request.method
    status = response.status_code

    def _write():
        try:
            with db.engine.connect() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO request_log (userid, method, path, status_code, timestamp) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (uid, method, p, status)
                )
                if uid:
                    conn.exec_driver_sql(
                        "UPDATE user SET last_active = CURRENT_TIMESTAMP WHERE id = ?",
                        (uid,)
                    )
                conn.commit()
        except Exception:
            pass

    threading.Thread(target=_write, daemon=True).start()
    return response


# Job helpers
def _job_create(user_id: int) -> str:
    job_id = _secrets.token_urlsafe(16)
    with _jobs_lock:
        now = time.time()
        stale = [k for k, v in _jobs.items() if now - v['created_at'] > JOB_TTL_S]
        for k in stale:
            _jobs.pop(k, None)
        _jobs[job_id] = {'status': 'pending', 'result': None, 'error': None, 'user_id': user_id, 'created_at': now}
    return job_id


def _job_set(job_id: str, status: str, result=None, error=None):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(status=status, result=result, error=error)


def _job_get(job_id: str, user_id: int) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        if j is None or j['user_id'] != user_id:
            return None
        return dict(j)


def get_model_costs() -> dict:
    val = _get_setting('model_costs_json')
    if val:
        try:
            return json.loads(val)
        except:
            pass
    # default pricing per 1M tokens in INR (Rupees)
    return {
        "mistralai/mistral-nemo": {"input": 15.0, "output": 50.0}
    }


def save_model_costs(costs: dict):
    _set_setting('model_costs_json', json.dumps(costs))


def track_llm_call(user_id: int, model_name: str, prompt_tokens: int, completion_tokens: int):
    if not user_id:
        return
    try:
        user = User.query.get(user_id)
        if not user:
            return

        costs = get_model_costs()
        pricing = costs.get(model_name, {"input": 0.0, "output": 0.0})

        # Calculate cost
        in_cost = (prompt_tokens * pricing.get("input", 0.0)) / 1_000_000.0
        out_cost = (completion_tokens * pricing.get("output", 0.0)) / 1_000_000.0
        call_cost = in_cost + out_cost

        user.generations_count = (user.generations_count or 0) + 1
        user.input_tokens_used = (user.input_tokens_used or 0) + prompt_tokens
        user.output_tokens_used = (user.output_tokens_used or 0) + completion_tokens
        user.total_cost = (user.total_cost or 0.0) + call_cost
        user.last_model_used = model_name
        user.last_active = datetime.datetime.utcnow()

        # Log LLM call to ActivityLog
        db.session.add(ActivityLog(
            userid = user_id,
            action = 'llm_call',
            detail = json.dumps({
                'model': model_name,
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'cost': call_cost
            })
        ))

        db.session.commit()
    except Exception as e:
        print(f"[ERROR] Failed to track LLM call: {e}")
        db.session.rollback()


def check_user_cost_limit(user_id: int) -> bool:
    if not user_id:
        return True
    try:
        user = User.query.get(user_id)
        if not user:
            return True
        if user.is_admin:
            return True
        if (user.total_cost or 0.0) >= (user.cost_limit or 1000.0):
            return False
    except Exception:
        pass
    return True
