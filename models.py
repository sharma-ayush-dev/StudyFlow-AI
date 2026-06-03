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
    full_name = db.Column(db.String(50), nullable=True)
    upload_count = db.Column(db.Integer, default=0, nullable=False)
    generations_count = db.Column(db.Integer, default=0, nullable=False)
    last_active = db.Column(db.DateTime, nullable=True)
    input_tokens_used = db.Column(db.Integer, default=0, nullable=False)
    output_tokens_used = db.Column(db.Integer, default=0, nullable=False)
    last_model_used = db.Column(db.String(100), nullable=True)
    total_cost = db.Column(db.Float, default=0.0, nullable=False)
    cost_limit = db.Column(db.Float, default=2.0, nullable=False)
    membership_rel = db.relationship('UserMembership', backref='user', uselist=False, cascade='all, delete-orphan')

    @property
    def membership(self):
        from models import UserMembership
        return UserMembership.query.filter_by(user_id=self.id).first()

    @property
    def is_membership_exhausted(self):
        m = self.membership
        if not m or not m.tier:
            return False
        limit = m.custom_budget_limit if (m.custom_budget_limit is not None) else m.tier.budget_limit
        return (m.usage_cost or 0.0) >= limit

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
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(256), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class RequestLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    method = db.Column(db.String(10))
    path = db.Column(db.String(255))
    status_code = db.Column(db.Integer)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(256), nullable=True)
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


class MembershipTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_price = db.Column(db.Integer, default=0, nullable=False)
    model_id = db.Column(db.String(100), nullable=False)
    budget_limit = db.Column(db.Float, default=1.0, nullable=False)
    speed_label = db.Column(db.String(50), nullable=False)
    tutor_quality_label = db.Column(db.String(50), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)


class UserMembership(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    tier_id = db.Column(db.Integer, db.ForeignKey('membership_tier.id'), nullable=False)
    usage_cost = db.Column(db.Float, default=0.0, nullable=False)
    usage_percentage = db.Column(db.Float, default=0.0, nullable=False)
    upgraded_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    total_amount_paid = db.Column(db.Float, default=0.0, nullable=False)
    custom_budget_limit = db.Column(db.Float, nullable=True)
    bronze_exhausted_before = db.Column(db.Boolean, default=False, nullable=False)

    tier = db.relationship('MembershipTier', backref=db.backref('memberships', lazy=True))


class UsageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.String(255), nullable=False)
    model_used = db.Column(db.String(100), nullable=False)
    input_tokens = db.Column(db.Integer, default=0, nullable=False)
    output_tokens = db.Column(db.Integer, default=0, nullable=False)
    total_tokens = db.Column(db.Integer, default=0, nullable=False)
    request_cost = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    membership_tier = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    status = db.Column(db.String(20), default='pending')  # pending, paid, failed, refunded
    razorpay_order_id = db.Column(db.String(100), nullable=False, unique=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True)
    razorpay_signature = db.Column(db.String(256), nullable=True)
    refund_id = db.Column(db.String(100), nullable=True)
    failure_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationship
    user = db.relationship('User', backref=db.backref('payments', lazy=True))


class WebhookLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Relationship
    payment = db.relationship('Payment', backref=db.backref('webhooks', lazy=True))

