import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-before-deploy')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///userdata.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 120

    # SMTP Configuration
    SMTP_SERVER = os.environ.get('SMTP_SERVER')
    try:
        SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    except (TypeError, ValueError):
        SMTP_PORT = 587
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', 'auth@studyflowai.app')
    SMTP_FROM_NAME = os.environ.get('SMTP_FROM_NAME', 'StudyFlow-AI')
    SMTP_USE_SSL = os.environ.get('SMTP_USE_SSL', 'false').lower() == 'true'

