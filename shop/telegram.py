"""Telegram bot: personal manager alerts and buyer order-status checks."""
import hashlib
import http.client
import json
import logging
import re
import socket
import ssl
import sys
import threading
import time
import urllib.request
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 3900
_API_HOST = 'api.telegram.org'
# The handshake still goes to api.telegram.org. This label is only the SNI
# value: some networks reset the connection when the ClientHello names Telegram.
_DECOY_SNI = 'example.com'
_POLLER_STARTED = False
_direct_tls_blocked = False
_route_checked = False


def news_url():
    """App link for the buyers group. https://t.me is blocked on this network, so the page opens Telegram directly."""
    return _telegram_app_link(getattr(settings, 'TELEGRAM_NEWS_URL', ''))


def public_bot_url():
    username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username or ''):
        return f'tg://resolve?domain={username}'
    return ''


def _telegram_app_link(url):
    url = str(url or '').strip()
    if len(url) > 300 or any(char.isspace() for char in url):
        return ''
    if url.startswith('https://t.me/'):
        rest = url[len('https://t.me/'):]
    elif url.startswith('https://telegram.me/'):
        rest = url[len('https://telegram.me/'):]
    else:
        return ''
    rest = rest.split('#', 1)[0].split('?', 1)[0].strip('/')
    if rest.startswith('+') or rest.startswith('joinchat/'):
        code = rest[1:] if rest.startswith('+') else rest[len('joinchat/'):]
        if re.fullmatch(r'[A-Za-z0-9_-]{10,64}', code or ''):
            return f'tg://join?invite={code}'
        return ''
    username = rest.split('/', 1)[0]
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username or ''):
        return f'tg://resolve?domain={username}'
    return ''


def notify_new_order(order_id):
    transaction.on_commit(lambda: _safe(_deliver_order, order_id))


def notify_brand_contact(message_id):
    transaction.on_commit(lambda: _safe(_deliver_contact, message_id))


def notify_status_change(order_id):
    transaction.on_commit(lambda: _safe(_deliver_status, order_id))


def issue_bind_token(manager):
    """Return a one-time code the manager sends to the bot from a private chat."""
    import secrets
    from datetime import timedelta

    token = secrets.token_urlsafe(24)
    manager.telegram_bind_hash = _digest(token)
    manager.telegram_bind_expires = timezone.now() + timedelta(minutes=30)
    manager.save(update_fields=['telegram_bind_hash', 'telegram_bind_expires'])
    return token


def cabinet_context(manager, session_token):
    token = str(session_token or '')
    link = ''
    code = ''
    expires = manager.telegram_bind_expires
    if token and expires and expires > timezone.now() and secrets_equal(manager.telegram_bind_hash, _digest(token)):
        code = token
        username = bot_username()
        if username:
            link = f'tg://resolve?domain={username}&start={token}'
    return {
        'telegram_connected': bool(manager.telegram_chat_id),
        'telegram_link': link,
        'telegram_start_code': '' if link else code,
    }


def handle_update(update):
    message = update.get('message') if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return
    chat = message.get('chat') if isinstance(message.get('chat'), dict) else {}
    chat_id = chat.get('id')
    if type(chat_id) is not int:
        return
    if chat.get('type') != 'private':
        _send(chat_id, 'Статус заказа и уведомления менеджера работают только в личном чате с ботом.')
        return
    text = str(message.get('text') or '').strip()
    if not text:
        return
    if text.startswith('/start'):
        payload = text.split(maxsplit=1)[1].strip() if ' ' in text else ''
        _send(chat_id, bind_manager(chat_id, payload) if payload else _help_text())
        return
    query = _status_query(text)
    if query is None:
        _send(chat_id, _help_text())
        return
    _send(chat_id, order_status(chat_id, *query))


def bind_manager(chat_id, code):
    from .models import BrandManager, UserProfile

    if _bump(f'telegram-bind-{chat_id}', BIND_LIMIT):
        return 'Слишком много попыток подключения. Попробуйте через час.'
    if not code or len(str(code)) > 200:
        return _bind_rejected()
    with transaction.atomic():
        # Lock only this row. Brand is optional, and PostgreSQL rejects
        # FOR UPDATE on the nullable side of that outer join.
        manager = (BrandManager.objects.select_for_update(of=('self',))
                   .select_related('brand')
                   .filter(telegram_bind_hash=_digest(code), telegram_bind_expires__gt=timezone.now())
                   .first())
        if manager is None:
            return _bind_rejected()
        email_ready = UserProfile.objects.filter(
            user_id=manager.user_id, email_verified_at__isnull=False,
        ).exists()
        if not email_ready or not manager.is_approved or manager.brand_id is None or manager.brand.is_deleted:
            manager.telegram_bind_hash = ''
            manager.telegram_bind_expires = None
            manager.save(update_fields=['telegram_bind_hash', 'telegram_bind_expires'])
            return _bind_rejected()
        chat = str(chat_id)
        BrandManager.objects.filter(telegram_chat_id=chat).exclude(pk=manager.pk).update(telegram_chat_id=None)
        manager.telegram_chat_id = chat
        manager.telegram_bind_hash = ''
        manager.telegram_bind_expires = None
        manager.save(update_fields=['telegram_chat_id', 'telegram_bind_hash', 'telegram_bind_expires'])
        brand_name = _html(manager.brand.name)
    return f'Готово. Заказы и сообщения бренда «{brand_name}» будут приходить в этот чат.'


def order_for_phone(order_number, phone):
    """Order that matches the public 8-digit number and the last 10 phone digits. Does not bind a chat."""
    from .models import Order

    number = str(order_number or '').strip()
    if not re.fullmatch(r'[1-9]\d{7}', number):
        return None
    order = Order.objects.prefetch_related('items').filter(public_number=number).first()
    if order is None or not _phones_match(order.phone, phone):
        return None
    return order


def consume_phone_lookup(scope, phone):
    """True when this phone has already been checked too often. A new IP does not reset the budget."""
    phone_key = _phone_limit_key(phone)
    if not phone_key:
        return False
    return _bump(f'{scope}-status-phone-{phone_key}', PHONE_STATUS_LIMIT)


def order_status(chat_id, order_id, phone):
    from django.db.models import Q
    from .models import Order

    if _status_limited(chat_id, phone):
        return 'Слишком много проверок. Попробуйте через час.'
    number = str(order_id or '').strip()
    order = Order.objects.prefetch_related('items').filter(public_number=number).first()
    if order is None or not _phones_match(order.phone, phone):
        return 'Заказ с таким номером и телефоном не найден.'
    chat = str(chat_id)
    current = order.buyer_telegram_chat_id or ''
    if current == chat:
        bound_here = True
    elif current:
        bound_here = False
    else:
        bound_here = Order.objects.filter(pk=order.pk).filter(
            Q(buyer_telegram_chat_id='') | Q(buyer_telegram_chat_id__isnull=True),
        ).update(buyer_telegram_chat_id=chat) == 1
    if bound_here:
        footer = 'Если статус станет «отправлен» или «отменён», бот напишет в этот чат.'
    else:
        footer = 'Уведомления об отправке и отмене остаются в чате, который первым проверил этот заказ.'
    lines = [
        f'<b>Заказ №{order.public_number}</b>',
        f'Статус: {_html(order.get_status_display())}',
        '',
    ]
    for item in order.items.all():
        mode = 'предзаказ' if item.is_preorder else 'в наличии'
        lines.append(
            f'• {_html(item.product_title)}, размер {_html(item.size)}, '
            f'{item.quantity} шт. × {_money(item.unit_price)} ({mode})'
        )
    lines.extend([
        '',
        f'Доставка: {_html(order.delivery_name)}',
        f'Итого: {_money(order.total)}',
    ])
    if order.tracking_number:
        lines.append(f'Трек: {_html(order.tracking_number)}')
    lines.extend([
        '',
        footer,
    ])
    return '\n'.join(lines)


def start_polling():
    global _POLLER_STARTED
    if _POLLER_STARTED or not getattr(settings, 'TELEGRAM_BOT_TOKEN', ''):
        return
    _POLLER_STARTED = True
    threading.Thread(target=poll_forever, name='telegram-bot', daemon=True).start()


def poll_forever():
    offset = None
    while True:
        close_old_connections()
        payload = {'timeout': 25, 'allowed_updates': ['message']}
        if offset is not None:
            payload['offset'] = offset
        try:
            updates = _api('getUpdates', payload, timeout=40)
        except Exception:
            logger.exception('Telegram poll failed')
            time.sleep(3)
            continue
        if not isinstance(updates, list):
            time.sleep(3)
            continue
        for update in updates:
            update_id = update.get('update_id') if isinstance(update, dict) else None
            if type(update_id) is int:
                offset = update_id + 1
            try:
                handle_update(update)
            except Exception:
                logger.exception('Telegram update failed')
        close_old_connections()


def bot_username():
    configured = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', configured or ''):
        return configured
    if not getattr(settings, 'TELEGRAM_BOT_TOKEN', ''):
        return ''
    cached = cache.get('telegram-bot-username')
    if cached is not None:
        return cached
    result = _api('getMe', {})
    username = (result or {}).get('username') if isinstance(result, dict) else ''
    username = username if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username or '') else ''
    cache.set('telegram-bot-username', username, 3600 if username else 60)
    return username


def _safe(func, argument):
    try:
        func(argument)
    except Exception:
        logger.exception('Telegram notification failed')


def _deliver_order(order_id):
    from .access import scoped_order_items
    from .models import Order

    order = Order.objects.prefetch_related('items__product__brand').filter(pk=order_id).first()
    if order is None:
        return False
    sent = False
    for brand in order_brands(order):
        items = list(scoped_order_items(brand, order.items.select_related('product')))
        if not items:
            continue
        text = _brand_order_text(order, brand, items)
        for chat_id in _manager_chats(brand):
            sent = _send(chat_id, text) or sent
    return sent


def _deliver_contact(message_id):
    from .models import BrandMessage

    note = BrandMessage.objects.select_related('brand', 'product', 'user').filter(pk=message_id).first()
    if note is None or note.brand.is_deleted:
        return False
    sent = False
    text = _contact_text(note)
    for chat_id in _manager_chats(note.brand):
        sent = _send(chat_id, text) or sent
    return sent


def _deliver_status(order_id):
    from .access import scoped_order_items
    from .models import Order

    order = Order.objects.prefetch_related('items__product__brand').filter(pk=order_id).first()
    if order is None or order.status not in (Order.SHIPPED, Order.CANCELLED):
        return False
    sent = False
    if order.buyer_telegram_chat_id:
        sent = _send(order.buyer_telegram_chat_id, _buyer_status_text(order)) or sent
    for brand in order_brands(order):
        items = list(scoped_order_items(brand, order.items.select_related('product')))
        if not items:
            continue
        text = _brand_status_text(order, brand, items)
        for chat_id in _manager_chats(brand):
            sent = _send(chat_id, text) or sent
    return sent


def order_brands(order):
    from .models import Brand

    found = {}
    missing_names = set()
    for item in order.items.all():
        brand = item.product.brand if item.product_id and item.product and item.product.brand_id else None
        if brand is not None and not brand.is_deleted:
            found[brand.pk] = brand
        elif item.brand_name:
            missing_names.add(item.brand_name)
    if missing_names:
        for brand in Brand.objects.filter(name__in=missing_names, is_deleted=False):
            found[brand.pk] = brand
    return list(found.values())


def _manager_chats(brand):
    from .access import confirmed_managers

    return list(
        confirmed_managers(brand)
        .exclude(telegram_chat_id__isnull=True)
        .exclude(telegram_chat_id='')
        .values_list('telegram_chat_id', flat=True)
    )


def _brand_order_text(order, brand, items):
    lines = [
        f'<b>Новый заказ №{order.public_number}</b>',
        f'Бренд: {_html(brand.name)}',
        f'Статус: {_html(order.get_status_display())}',
        '',
        f'Получатель: {_html(order.full_name)}',
        f'Почта: {_html(order.email)}',
        f'Телефон: {_html(order.phone)}',
        '',
        f'Доставка: {_html(order.delivery_name)}',
    ]
    if order.delivery_eta:
        lines.append(f'Срок: {_html(order.delivery_eta)}')
    if order.pickup_address:
        lines.append(f'Самовывоз: {_html(order.pickup_address)}')
    if order.delivery_point_code:
        lines.append(f'Пункт: {_html(order.delivery_point_code)}')
    if order.delivery_point_address:
        lines.append(f'Адрес пункта: {_html(order.delivery_point_address)}')
    address = ', '.join(part for part in (order.postal_code, order.city, order.address) if part)
    if address:
        lines.append(f'Адрес: {_html(address)}')
    if order.comment:
        lines.extend(['', f'Комментарий: {_html(order.comment)}'])
    lines.extend(['', '<b>Ваши позиции</b>'])
    subtotal = Decimal('0')
    for item in items:
        subtotal += item.line_total
        if item.is_preorder:
            mode = f'предзаказ, {item.preorder_days} дн.' if item.preorder_days else 'предзаказ'
        else:
            mode = 'в наличии'
        lines.append(
            f'• {_html(item.product_title)}, размер {_html(item.size)}, '
            f'{item.quantity} шт. × {_money(item.unit_price)} = {_money(item.line_total)} ({mode})'
        )
    lines.extend(['', f'<b>Сумма ваших позиций: {_money(subtotal)}</b>', '', _stamp(order.created_at)])
    return '\n'.join(lines)


def _buyer_status_text(order):
    lines = [
        f'<b>Заказ №{order.public_number}</b>',
        f'Статус: {_html(order.get_status_display())}',
        '',
    ]
    for item in order.items.all():
        lines.append(f'• {_html(item.product_title)}, размер {_html(item.size)}, {item.quantity} шт.')
    if order.delivery_name:
        lines.extend(['', f'Доставка: {_html(order.delivery_name)}'])
    if order.tracking_number:
        lines.append(f'Трек: {_html(order.tracking_number)}')
    return '\n'.join(lines)


def _brand_status_text(order, brand, items):
    lines = [
        f'<b>Статус заказа №{order.public_number} изменился</b>',
        f'Бренд: {_html(brand.name)}',
        f'Статус: {_html(order.get_status_display())}',
    ]
    if order.tracking_number:
        lines.append(f'Трек: {_html(order.tracking_number)}')
    lines.extend([
        '',
        '<b>Ваши позиции</b>',
    ])
    subtotal = Decimal('0')
    for item in items:
        subtotal += item.line_total
        lines.append(
            f'• {_html(item.product_title)}, размер {_html(item.size)}, '
            f'{item.quantity} шт. = {_money(item.line_total)}'
        )
    lines.extend(['', f'<b>Сумма ваших позиций: {_money(subtotal)}</b>'])
    return '\n'.join(lines)


def _contact_text(note):
    product = note.product.title if note.product_id and note.product else 'не выбрана'
    lines = [
        '<b>Покупатель хочет связаться с менеджером</b>',
        '',
        f'Бренд: {_html(note.brand.name)}',
        f'Вещь: {_html(product)}',
        f'Имя: {_html(note.sender_name)}',
        f'Почта: {_html(note.sender_email) or "не оставлена"}',
        f'Telegram: {_html(note.sender_telegram) or "не оставлен"}',
        '',
        _html(note.body),
        '',
        _stamp(note.created_at),
    ]
    return '\n'.join(lines)


def _help_text():
    return (
        'Менеджер бренда подключает уведомления ссылкой из кабинета. '
        'Сюда приходят только заказы и сообщения его бренда.\n\n'
        'Покупатель может проверить статус. Напишите 8-значный номер заказа и телефон из заказа:\n'
        '48291037 +79991234567\n\n'
        'После первой успешной проверки бот напишет в этот чат, когда заказ отправят или отменят.'
    )


def _status_query(text):
    cleaned = re.sub(r'^/status(?:@\w+)?\s*', '', text.strip(), count=1, flags=re.I)
    match = re.fullmatch(r'([1-9]\d{7})\s+(\+?\d[\d\s()\-]{8,40})', cleaned)
    if not match:
        return None
    return match.group(1), match.group(2)


def _phones_match(stored, given):
    left = re.sub(r'\D', '', stored or '')
    right = re.sub(r'\D', '', given or '')
    return len(left) >= 10 and len(right) >= 10 and left[-10:] == right[-10:]


CHAT_STATUS_LIMIT = 8
PHONE_STATUS_LIMIT = 30
BIND_LIMIT = 8
LIMIT_WINDOW = 3600


def _bind_rejected():
    return 'Ссылка устарела или кабинет ещё не подтверждён. Откройте кабинет бренда и получите новую.'


def _status_limited(chat_id, phone):
    """A new chat does not reset the budget of a phone that is already being checked."""
    if _bump(f'telegram-status-chat-{chat_id}', CHAT_STATUS_LIMIT):
        return True
    phone_key = _phone_limit_key(phone)
    return bool(phone_key) and _bump(f'telegram-status-phone-{phone_key}', PHONE_STATUS_LIMIT)


def _phone_limit_key(phone):
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) < 10:
        return ''
    return hashlib.sha256(digits[-10:].encode()).hexdigest()


def _bump(key, limit):
    if cache.add(key, 1, LIMIT_WINDOW):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, LIMIT_WINDOW)
        return False
    if count == 1:
        cache.touch(key, LIMIT_WINDOW)
    return count > limit


def _digest(code):
    return hashlib.sha256(str(code).encode()).hexdigest()


def secrets_equal(left, right):
    import secrets
    left, right = str(left), str(right)
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def _send(chat_id, text):
    chat_id = str(chat_id or '').strip()
    if not getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or not chat_id.lstrip('-').isdigit() or not text:
        return False
    sent = False
    for part in _chunks(text):
        result = _api('sendMessage', {
            'chat_id': chat_id,
            'text': part,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        })
        if result is None:
            return False
        sent = True
    return sent


def _certificate_matches(cert, hostname):
    """True when a DNS name on the certificate covers hostname, including one wildcard label."""
    hostname = str(hostname or '').lower().rstrip('.')
    if not hostname or not isinstance(cert, dict):
        return False
    names = [value for kind, value in cert.get('subjectAltName', ()) if kind == 'DNS' and value]
    for pattern in names:
        pattern = pattern.lower().rstrip('.')
        if pattern == hostname:
            return True
        if pattern.startswith('*.') and pattern.count('*') == 1:
            rest = pattern[2:]
            label, dot, tail = hostname.partition('.')
            if dot and label and tail == rest:
                return True
    return False


def _blocked_handshake(exc):
    reason = getattr(exc, 'reason', exc)
    if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
        return True
    text = str(reason)
    return 'UNEXPECTED_EOF_WHILE_READING' in text or 'handshake operation timed out' in text


def _note_direct_route():
    """One short handshake. A reset or a stall means the server name itself is being dropped."""
    global _route_checked, _direct_tls_blocked
    if _route_checked or _direct_tls_blocked or 'test' in sys.argv:
        return
    _route_checked = True
    raw = None
    try:
        raw = socket.create_connection((_API_HOST, 443), 4)
        context = ssl.create_default_context(cafile=certifi.where())
        secure = context.wrap_socket(raw, server_hostname=_API_HOST)
        secure.close()
        raw = None
    except Exception as exc:
        if raw is not None:
            raw.close()
        if not _blocked_handshake(exc):
            return
        _direct_tls_blocked = True
        logger.warning(
            'Telegram resets the TLS handshake when the server name is visible; '
            'requests to %s keep the certificate check',
            _API_HOST,
        )


def _open(request, timeout):
    global _direct_tls_blocked
    _note_direct_route()
    if not _direct_tls_blocked:
        try:
            return urlopen(
                request,
                timeout=timeout,
                context=ssl.create_default_context(cafile=certifi.where()),
            )
        except URLError as exc:
            if not _blocked_handshake(exc):
                raise
            _direct_tls_blocked = True
            logger.warning(
                'Telegram resets the TLS handshake when the server name is visible; '
                'retrying %s with the certificate check unchanged',
                _API_HOST,
            )
    return _decoy_urlopen(request, timeout)


def _decoy_urlopen(request, timeout):
    context = ssl.create_default_context(cafile=certifi.where())
    context.check_hostname = False
    opener = urllib.request.build_opener(_DecoySNIHandler(context=context))
    return opener.open(request, timeout=timeout)


class _DecoySNIHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_DecoySNIConnection, req, context=self._context)


class _DecoySNIConnection(http.client.HTTPSConnection):
    def connect(self):
        http.client.HTTPConnection.connect(self)
        expected = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=_DECOY_SNI)
        if expected != _API_HOST or not _certificate_matches(self.sock.getpeercert(), _API_HOST):
            self.sock.close()
            self.sock = None
            raise ssl.SSLCertVerificationError(f'certificate is not valid for {_API_HOST}')


def _api(method, payload, timeout=5):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return None
    request = Request(
        f'https://{_API_HOST}/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with _open(request, timeout) as response:
            body = json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:500]
        logger.error('Telegram %s returned HTTP %s: %s', method, exc.code, detail)
        return None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.exception('Telegram %s request failed', method)
        return None
    if not isinstance(body, dict) or not body.get('ok'):
        detail = body.get('description', '') if isinstance(body, dict) else ''
        logger.error('Telegram %s rejected the request: %s', method, str(detail)[:300])
        return None
    return body.get('result')


def _chunks(text, limit=MESSAGE_LIMIT):
    remaining = text.strip()
    parts = []
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].rstrip('\n'))
        remaining = remaining[cut:].lstrip('\n')
    return parts


def _html(value):
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _money(value):
    amount = Decimal(value)
    if amount == amount.to_integral_value():
        return f'{amount:.0f} ₽'
    return f'{amount:.2f} ₽'


def _stamp(moment):
    if moment is None:
        return ''
    return timezone.localtime(moment).strftime('%d.%m.%Y %H:%M')
