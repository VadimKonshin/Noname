"""Database and cache selection. SQLite and process memory stay the defaults."""
from pathlib import Path


def database_settings(environ, base_dir):
    engine = environ.get('DJANGO_DB_ENGINE', 'sqlite').strip().lower()
    if engine in ('sqlite', 'sqlite3', ''):
        return {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': Path(base_dir) / 'db.sqlite3',
            },
        }
    if engine not in ('postgres', 'postgresql'):
        raise RuntimeError('DJANGO_DB_ENGINE должен быть sqlite или postgresql.')
    name = environ.get('DJANGO_DB_NAME', '').strip()
    user = environ.get('DJANGO_DB_USER', '').strip()
    if not name or not user:
        raise RuntimeError('Для PostgreSQL задайте DJANGO_DB_NAME и DJANGO_DB_USER.')
    port = environ.get('DJANGO_DB_PORT', '5432').strip() or '5432'
    age = environ.get('DJANGO_DB_CONN_MAX_AGE', '60').strip() or '60'
    if not port.isdigit() or not age.isdigit():
        raise RuntimeError('DJANGO_DB_PORT и DJANGO_DB_CONN_MAX_AGE должны быть целыми числами.')
    return {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': name,
            'USER': user,
            'PASSWORD': environ.get('DJANGO_DB_PASSWORD', ''),
            'HOST': environ.get('DJANGO_DB_HOST', '127.0.0.1').strip() or '127.0.0.1',
            'PORT': port,
            'CONN_MAX_AGE': int(age),
        },
    }


def cache_settings(environ):
    url = environ.get('DJANGO_CACHE_URL', '').strip()
    if not url:
        return {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'noname',
                'TIMEOUT': 300,
            },
        }
    if not (url.startswith('redis://') or url.startswith('rediss://')):
        raise RuntimeError('DJANGO_CACHE_URL должен начинаться с redis:// или rediss://.')
    return {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': url,
            'KEY_PREFIX': 'noname',
            'TIMEOUT': 300,
        },
    }
