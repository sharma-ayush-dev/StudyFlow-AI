import datetime
from flask import session
from flask_login import UserMixin
from extensions import db, login_manager


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    username_changed_at = db.Column(db.DateTime, nullable=True)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    course = db.Column(db.String(50), nullable=True)
    upload_count = db.Column(db.Integer, default=0, nullable=False)
    generations_count = db.Column(db.Integer, default=0, nullable=False)
    last_active = db.Column(db.DateTime, nullable=True)
    input_tokens_used = db.Column(db.Integer, default=0, nullable=False)
    output_tokens_used = db.Column(db.Integer, default=0, nullable=False)
    last_model_used = db.Column(db.String(100), nullable=True)
    total_cost = db.Column(db.Float, default=0.0, nullable=False)
    cost_limit = db.Column(db.Float, default=1000.0, nullable=False)

    def get_id(self):
        return f"{self.id}:{self.session_version or 0}"

    def set_password(self, p):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(p, method='pbkdf2:sha256:600000', salt_length=16)

    def check_password(self, p):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, p)


class StudyData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    extracted_json = db.Column(db.Text)
    topic_status = db.Column(db.Text)
    schedule_json = db.Column(db.Text)
    pending_schedule_json = db.Column(db.Text)
    generation_inputs_json = db.Column(db.Text)
    pending_generation_inputs_json = db.Column(db.Text)


class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    schedule_date = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('userid', 'subject', 'topic', 'schedule_date', name='uq_user_subject_topic_date'),)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class PasswordResetOTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    otp_code = db.Column(db.String(10), nullable=False, default='1234')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)


class EmailOTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    otp_code = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)



class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class AppSettings(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(512), nullable=True)


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(50))
    detail = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class RequestLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    method = db.Column(db.String(10))
    path = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)


@login_manager.user_loader
def load_user(user_id: str):
    if not user_id:
        return None
    try:
        if ':' in user_id:
            uid_str, version_str = user_id.split(':', 1)
            uid = int(uid_str)
            version = int(version_str)
        else:
            uid = int(user_id)
            version = None
    except ValueError:
        return None

    user = User.query.get(uid)
    if not user:
        return None
    if version is not None and (user.session_version or 0) != version:
        return None
    return user
