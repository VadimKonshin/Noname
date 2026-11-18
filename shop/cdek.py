"""CDEK calculator. The price stored on an order always comes from this response, never from the form."""
import json
import logging
import ssl
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import certifi
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {'api.cdek.ru', 'api.edu.cdek.ru'}
TOKEN_CACHE_KEY = 'cdek-oauth-token'
MAX_POINTS = 12


class CdekUnavailable(Exception):
    """The carrier did not return a usable price. Callers must not invent one."""


class CdekQuote:
    def __init__(self, price, eta, points, postal_code, pieces, weight):
        self.price = price
        self.eta = eta
        self.points = points
        self.postal_code = postal_code
        self.pieces = pieces
        self.weight = weight


def parcel_weight(lines):
    """Grams for the whole parcel. Missing weight falls back to the per-item estimate."""
    total = 0
    for line in lines:
        product = line.get('product')
        grams = getattr(product, 'weight_grams', None) or settings.CDEK_ITEM_WEIGHT_GRAMS
        quantity = int(line.get('quantity') or 0)
        if quantity > 0:
            total += int(grams) * quantity
    if total <= 0:
        total = settings.CDEK_ITEM_WEIGHT_GRAMS
    return max(100, min(30000, total))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CdekUnavailable


def configured():
    return bool(
        settings.CDEK_CLIENT_ID
        and settings.CDEK_CLIENT_SECRET
        and len(settings.CDEK_FROM_POSTAL_CODE) == 6
        and settings.CDEK_FROM_POSTAL_CODE.isdigit()
    )


def quote_delivery(postal_code, pieces, weight_grams=None):
    if not configured():
        raise CdekUnavailable
    code = ''.join(character for character in str(postal_code or '') if character.isdigit())
    if len(code) != 6:
        raise CdekUnavailable
    pieces = max(1, min(int(pieces), 50))
    if weight_grams is None:
        weight = pieces * settings.CDEK_ITEM_WEIGHT_GRAMS
    else:
        weight = int(weight_grams)
    weight = max(100, min(30000, weight))
    token = _token()
    calculated = _request('POST', '/calculator/tariff', token=token, payload={
        'type': 1,
        'tariff_code': settings.CDEK_TARIFF_CODE,
        'from_location': {'postal_code': settings.CDEK_FROM_POSTAL_CODE},
        'to_location': {'postal_code': code},
        'packages': [{'weight': weight, 'length': 20, 'width': 15, 'height': 10}],
    })
    price = _money(calculated.get('total_sum', calculated.get('delivery_sum')))
    if price is None or price <= 0:
        raise CdekUnavailable
    period_min = calculated.get('period_min')
    period_max = calculated.get('period_max')
    if isinstance(period_min, int) and isinstance(period_max, int) and 0 < period_min <= period_max <= 60:
        eta = f'{period_min}–{period_max} дн.'
    else:
        eta = ''
    points = _points(token, code)
    if not points:
        raise CdekUnavailable
    return CdekQuote(price=price, eta=eta, points=points, postal_code=code, pieces=pieces, weight=weight)


def _token():
    cached = cache.get(TOKEN_CACHE_KEY)
    if isinstance(cached, str) and cached:
        return cached
    payload = _request('POST', '/oauth/token', form={
        'grant_type': 'client_credentials',
        'client_id': settings.CDEK_CLIENT_ID,
        'client_secret': settings.CDEK_CLIENT_SECRET,
    })
    token = payload.get('access_token')
    if not isinstance(token, str) or not token or len(token) > 2048:
        raise CdekUnavailable
    expires = payload.get('expires_in')
    ttl = expires - 60 if isinstance(expires, int) and expires > 120 else 300
    cache.set(TOKEN_CACHE_KEY, token, ttl)
    return token


def _points(token, postal_code):
    payload = _request(
        'GET',
        f'/deliverypoints?country_code=RU&type=PVZ&postal_code={postal_code}',
        token=token,
    )
    if not isinstance(payload, list):
        return []
    points = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip()
        location = item.get('location') if isinstance(item.get('location'), dict) else {}
        city = str(location.get('city') or '').strip()
        address = str(location.get('address') or '').strip()
        name = str(item.get('name') or '').strip()
        if not code or not address or len(code) > 64:
            continue
        full = ', '.join(part for part in (city, address) if part)[:250]
        points.append({'code': code, 'name': (name or code)[:120], 'address': full})
        if len(points) == MAX_POINTS:
            break
    return points


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount < 0 or amount > Decimal('100000.00'):
        return None
    return amount


def _base_url():
    parsed = urlsplit(settings.CDEK_API_URL.strip())
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or host not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise CdekUnavailable
    return f'https://{host}/v2'


def _request(method, path, token=None, payload=None, form=None):
    if not path.startswith('/'):
        raise CdekUnavailable
    data = None
    headers = {'Accept': 'application/json', 'User-Agent': 'NoName'}
    if form is not None:
        from urllib.parse import urlencode
        data = urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(f'{_base_url()}{path}', data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=8) as response:
            raw = response.read(1_000_000)
    except CdekUnavailable:
        raise
    except Exception:
        logger.warning('CDEK request failed for %s', path.split('?', 1)[0])
        raise CdekUnavailable
    try:
        parsed = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CdekUnavailable
    if not isinstance(parsed, (dict, list)):
        raise CdekUnavailable
    return parsed
