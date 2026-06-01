import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-before-deploy')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///userdata.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 120
