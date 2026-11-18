"""Settings for the local-first NoName independent brands store."""
import os
import sys
from pathlib import Path

from .runtime import cache_settings, database_settings

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_allowed(key):
    # Same .env lines as deploy/envfile.py. A value already set in the process wins.
    return key.startswith(('TELEGRAM_', 'DJANGO_', 'CDEK_'))


def load_env_file(path):
    """Fill empty environment variables from a local .env file."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if _env_allowed(key) and not os.environ.get(key):
            os.environ[key] = value


# Tests keep using explicit settings. A filled .env must not notify Telegram during manage.py test.
if 'test' not in sys.argv:
    load_env_file(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes')


def env_int(name, default, upper):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= upper:
        raise RuntimeError(f'{name} должен быть целым числом от 1 до {upper}.')
    return int(raw)


# Development fallback only. Set DJANGO_SECRET_KEY and DJANGO_DEBUG=0 in production.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'unsafe-local-development-key-change-before-deploying-2026')
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if host.strip()]

if not DEBUG and SECRET_KEY.startswith('unsafe-local-'):
    raise RuntimeError('Set DJANGO_SECRET_KEY when DJANGO_DEBUG=0')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'shop',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django_ratelimit.middleware.RatelimitMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'shop.middleware.ContentSecurityPolicyMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'shop.context_processors.store_context',
            ],
        },
    },
]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
# Tests stay on SQLite and process memory even when the server environment points at PostgreSQL.
_runtime_env = {} if 'test' in sys.argv else os.environ
DATABASES = database_settings(_runtime_env, BASE_DIR)
CACHES = cache_settings(_runtime_env)
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
# On a public IP there is no certificate yet, so the cookies must travel over HTTP.
SESSION_COOKIE_SECURE = env_bool('DJANGO_SESSION_COOKIE_SECURE', SECURE_SSL_REDIRECT)
CSRF_COOKIE_SECURE = env_bool('DJANGO_CSRF_COOKIE_SECURE', SECURE_SSL_REDIRECT)
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_SECURE_HSTS_SECONDS', '0' if DEBUG else '3600'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD = env_bool('DJANGO_SECURE_HSTS_PRELOAD')
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]
def client_ip(request):
    """Client address accepted by Caddy. The last hop is that address."""
    parts = [part.strip() for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if part.strip()]
    raw = parts[-1] if parts else ''
    if raw.startswith('[') and ']' in raw:
        raw = raw[1:raw.index(']')]
    return raw


if env_bool('DJANGO_TRUST_X_FORWARDED_PROTO'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # deploy/Caddyfile replaces X-Forwarded-For with the client Caddy accepted.
    RATELIMIT_IP_META_KEY = client_ip

if not DEBUG:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
        },
        'handlers': {
            'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
        },
        'root': {'handlers': ['console'], 'level': 'INFO'},
        'loggers': {
            'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
            'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        },
    }

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 12
RATELIMIT_ENABLE = env_bool('DJANGO_RATELIMIT_ENABLE', True)
RATELIMIT_VIEW = 'shop.views.ratelimited_error'

# Telegram Bot API. Leave the token blank locally: the store then skips notifications.
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
TELEGRAM_NEWS_URL = os.environ.get('TELEGRAM_NEWS_URL', '').strip()

# Local letters print in the runserver console. Set DJANGO_EMAIL_HOST to send them for real.
DEFAULT_FROM_EMAIL = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL', 'NoName <hello@noname.local>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ORDER_NOTICE_EMAIL = os.environ.get('DJANGO_ORDER_EMAIL', 'hello@noname.local').strip()
SITE_URL = os.environ.get('DJANGO_SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_TIMEOUT = 10
if os.environ.get('DJANGO_EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.environ['DJANGO_EMAIL_HOST']
    EMAIL_PORT = int(os.environ.get('DJANGO_EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('DJANGO_EMAIL_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_PASSWORD', '')
    EMAIL_USE_TLS = env_bool('DJANGO_EMAIL_USE_TLS', True)

# Empty credentials keep the fixed СДЭК tariff. A quote replaces that price only for the order being placed.
CDEK_CLIENT_ID = os.environ.get('CDEK_CLIENT_ID', '').strip()
CDEK_CLIENT_SECRET = os.environ.get('CDEK_CLIENT_SECRET', '').strip()
CDEK_API_URL = os.environ.get('CDEK_API_URL', 'https://api.cdek.ru/v2').strip() or 'https://api.cdek.ru/v2'
CDEK_FROM_POSTAL_CODE = os.environ.get('CDEK_FROM_POSTAL_CODE', '').strip()
CDEK_TARIFF_CODE = env_int('CDEK_TARIFF_CODE', 136, 9999)
CDEK_ITEM_WEIGHT_GRAMS = env_int('CDEK_ITEM_WEIGHT_GRAMS', 500, 30000)
