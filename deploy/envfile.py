"""Read .env files and reject a public server that is still on local defaults."""
import re

_HOST = re.compile(
    r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'
)
_IP = re.compile(r'^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$')
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
_SECRET = re.compile(r'^[A-Za-z0-9_-]{50,}$')
_PASSWORD = re.compile(r'^[A-Za-z0-9]{12,}$')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_BOT_NAME = re.compile(r'^[A-Za-z0-9_]{4,32}$')


def read_env(path):
    """Parse the same line shape as config.settings.load_env_file."""
    values = {}
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
        if key:
            values[key] = value
    return values


def sql_ident(value):
    if not _IDENT.fullmatch(value or ''):
        raise ValueError(value)
    return value


def _get(env, key):
    return str(env.get(key, '') or '').strip()


def _public_ip(host):
    if not _IP.fullmatch(host):
        return False
    parts = [int(part) for part in host.split('.')]
    if parts[0] in {0, 10, 127} or host == '0.0.0.0':
        return False
    if parts[0] == 192 and parts[1] == 168:
        return False
    if parts[0] == 172 and 16 <= parts[1] <= 31:
        return False
    return True


def _bad_host(host):
    host = host.lower().rstrip('.')
    return host in {'localhost', '127.0.0.1', '*', 'example.com'} or host.endswith(('.example.com', '.local'))


def _placeholder_email(value):
    text = value.lower()
    return '@example.com' in text or '.example.com' in text


def production_issues(env):
    """Return (errors, warnings). Errors must be fixed before the server starts."""
    errors = []
    warnings = []

    if _get(env, 'DJANGO_DEBUG') != '0':
        errors.append('DJANGO_DEBUG=0')
    if not _SECRET.fullmatch(_get(env, 'DJANGO_SECRET_KEY')):
        errors.append(
            'DJANGO_SECRET_KEY: 50+ символов из букв, цифр, дефиса и подчёркивания. '
            'python3 -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    if _get(env, 'DJANGO_DB_ENGINE').lower() not in {'postgres', 'postgresql'}:
        errors.append('DJANGO_DB_ENGINE=postgresql')
    if _get(env, 'DJANGO_DB_HOST') != 'postgres':
        errors.append('DJANGO_DB_HOST=postgres')
    try:
        sql_ident(_get(env, 'DJANGO_DB_NAME'))
        sql_ident(_get(env, 'DJANGO_DB_USER'))
    except ValueError:
        errors.append('DJANGO_DB_NAME и DJANGO_DB_USER: буквы, цифры и _.')
    if not _PASSWORD.fullmatch(_get(env, 'DJANGO_DB_PASSWORD')):
        errors.append('DJANGO_DB_PASSWORD: 12+ букв и цифр, без знака $.')
    if not _PASSWORD.fullmatch(_get(env, 'REDIS_PASSWORD')):
        errors.append('REDIS_PASSWORD: 12+ букв и цифр, без знака $.')

    cache = _get(env, 'DJANGO_CACHE_URL')
    if cache:
        if not (cache.startswith('redis://') or cache.startswith('rediss://')):
            errors.append('DJANGO_CACHE_URL должен начинаться с redis://.')
        if '127.0.0.1' in cache or 'localhost' in cache:
            errors.append('DJANGO_CACHE_URL оставьте пустым: контейнер сам соберёт адрес Redis.')

    workers = _get(env, 'WEB_WORKERS')
    if workers and (not workers.isdigit() or not 1 <= int(workers) <= 8):
        errors.append('WEB_WORKERS: число от 1 до 8.')

    hosts = [part.strip() for part in _get(env, 'DJANGO_ALLOWED_HOSTS').split(',') if part.strip()]
    if not hosts or any(_bad_host(host) for host in hosts):
        errors.append('DJANGO_ALLOWED_HOSTS: ваш домен.')

    address = _get(env, 'SITE_ADDRESS').lower().rstrip('.')
    if address == ':80':
        if len(hosts) != 1 or not _public_ip(hosts[0]):
            errors.append('DJANGO_ALLOWED_HOSTS: один публичный IP.')
        else:
            origin = f'http://{hosts[0]}'
            if _get(env, 'DJANGO_SITE_URL').rstrip('/') != origin:
                errors.append(f'DJANGO_SITE_URL={origin}')
            origins = [part.strip() for part in _get(env, 'DJANGO_CSRF_TRUSTED_ORIGINS').split(',') if part.strip()]
            if origins != [origin]:
                errors.append(f'DJANGO_CSRF_TRUSTED_ORIGINS={origin}')
            if _get(env, 'DJANGO_SECURE_SSL_REDIRECT') != '0':
                errors.append('DJANGO_SECURE_SSL_REDIRECT=0')
            if (_get(env, 'DJANGO_SECURE_HSTS_SECONDS') or '0') != '0':
                errors.append('DJANGO_SECURE_HSTS_SECONDS=0')
            if _get(env, 'DJANGO_TRUST_X_FORWARDED_PROTO') != '1':
                errors.append('DJANGO_TRUST_X_FORWARDED_PROTO=1')
    elif not _HOST.fullmatch(address):
        errors.append('SITE_ADDRESS: домен без https:// и без порта.')
    elif _bad_host(address):
        errors.append('SITE_ADDRESS: замените shop.example.com на свой домен.')
    else:
        known = {host.lower().rstrip('.') for host in hosts}
        origin = f'https://{address}'
        if address not in known:
            errors.append('SITE_ADDRESS должен входить в DJANGO_ALLOWED_HOSTS.')
        if _get(env, 'DJANGO_SITE_URL').rstrip('/') != origin:
            errors.append(f'DJANGO_SITE_URL={origin}')
        origins = [part.strip() for part in _get(env, 'DJANGO_CSRF_TRUSTED_ORIGINS').split(',') if part.strip()]
        if not origins or any(not item.startswith('https://') for item in origins) or origin not in origins:
            errors.append(f'DJANGO_CSRF_TRUSTED_ORIGINS={origin}')
        if _get(env, 'DJANGO_SECURE_SSL_REDIRECT') != '1':
            errors.append('DJANGO_SECURE_SSL_REDIRECT=1')
        if _get(env, 'DJANGO_TRUST_X_FORWARDED_PROTO') != '1':
            errors.append('DJANGO_TRUST_X_FORWARDED_PROTO=1')

    acme = _get(env, 'ACME_EMAIL')
    if not _EMAIL.fullmatch(acme) or _placeholder_email(acme):
        errors.append('ACME_EMAIL: живая почта для сертификата.')

    token = _get(env, 'TELEGRAM_BOT_TOKEN')
    if not token or any(char.isspace() for char in token) or '$' in token:
        errors.append('TELEGRAM_BOT_TOKEN из @BotFather.')
    if not _BOT_NAME.fullmatch(_get(env, 'TELEGRAM_BOT_USERNAME').lstrip('@')):
        errors.append('TELEGRAM_BOT_USERNAME без @.')
    news = _get(env, 'TELEGRAM_NEWS_URL')
    if news and not (news.startswith('https://t.me/') or news.startswith('https://telegram.me/')):
        errors.append('TELEGRAM_NEWS_URL: https://t.me/имя или https://t.me/+код.')

    if not _get(env, 'DJANGO_EMAIL_HOST'):
        warnings.append('DJANGO_EMAIL_HOST пуст: письма останутся в логе контейнера web.')
    sender = _get(env, 'DJANGO_DEFAULT_FROM_EMAIL')
    if not sender or 'noname.local' in sender or _placeholder_email(sender):
        warnings.append('DJANGO_DEFAULT_FROM_EMAIL: укажите отправителя на вашем домене.')
    order_mail = _get(env, 'DJANGO_ORDER_EMAIL')
    if not order_mail or order_mail.endswith('noname.local') or _placeholder_email(order_mail):
        warnings.append('DJANGO_ORDER_EMAIL: укажите живой ящик администратора.')
    if not _get(env, 'CDEK_CLIENT_ID'):
        warnings.append('CDEK_CLIENT_ID пуст: на витрине останутся фиксированные тарифы.')
    return errors, warnings
