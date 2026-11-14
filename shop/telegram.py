"""Telegram bot: personal manager alerts and buyer order-status checks."""
import hashlib
import json
import logging
import re
import ssl
import threading
import time
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
_POLLER_STARTED = False


def news_url():
    url = getattr(settings, 'TELEGRAM_NEWS_URL', '').strip()
    if url.startswith(('https://t.me/', 'https://telegram.me/')) and len(url) <= 300 and ' ' not in url:
        return url
    return ''


def public_bot_url():
    username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username or ''):
        return f'https://t.me/{username}'
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
            link = f'https://t.me/{username}?start={token}'
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
    from .models import BrandManager

    manager = (BrandManager.objects.select_related('brand')
               .filter(telegram_bind_hash=_digest(code), telegram_bind_expires__gt=timezone.now(), is_approved=True)
               .first())
    if manager is None or manager.brand_id is None or manager.brand.is_deleted:
        return 'Ссылка устарела или кабинет ещё не подтверждён. Откройте кабинет бренда и получите новую.'
    chat = str(chat_id)
    BrandManager.objects.filter(telegram_chat_id=chat).exclude(pk=manager.pk).update(telegram_chat_id=None)
    manager.telegram_chat_id = chat
    manager.telegram_bind_hash = ''
    manager.telegram_bind_expires = None
    manager.save(update_fields=['telegram_chat_id', 'telegram_bind_hash', 'telegram_bind_expires'])
    return f'Готово. Заказы и сообщения бренда «{manager.brand.name}» будут приходить в этот чат.'


def order_status(chat_id, order_id, phone):
    from .models import Order

    if _limited(chat_id):
        return 'Слишком много проверок. Попробуйте через час.'
    order = Order.objects.prefetch_related('items').filter(pk=order_id).first()
    if order is None or not _phones_match(order.phone, phone):
        return 'Заказ с таким номером и телефоном не найден.'
    Order.objects.filter(pk=order.pk).update(buyer_telegram_chat_id=str(chat_id))
    lines = [
        f'<b>Заказ №{order.pk}</b>',
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
        '',
        'Если статус станет «отправлен» или «отменён», бот напишет в этот чат.',
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
    from .models import BrandManager

    return list(
        BrandManager.objects.filter(brand=brand, is_approved=True, telegram_chat_id__isnull=False)
        .exclude(telegram_chat_id='')
        .values_list('telegram_chat_id', flat=True)
    )


def _brand_order_text(order, brand, items):
    lines = [
        f'<b>Новый заказ №{order.pk}</b>',
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
        f'<b>Заказ №{order.pk}</b>',
        f'Статус: {_html(order.get_status_display())}',
        '',
    ]
    for item in order.items.all():
        lines.append(f'• {_html(item.product_title)}, размер {_html(item.size)}, {item.quantity} шт.')
    if order.delivery_name:
        lines.extend(['', f'Доставка: {_html(order.delivery_name)}'])
    return '\n'.join(lines)


def _brand_status_text(order, brand, items):
    lines = [
        f'<b>Статус заказа №{order.pk} изменился</b>',
        f'Бренд: {_html(brand.name)}',
        f'Статус: {_html(order.get_status_display())}',
        '',
        '<b>Ваши позиции</b>',
    ]
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
        f'Почта: {_html(note.sender_email)}',
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
        'Покупатель может проверить статус. Напишите номер заказа и телефон из заказа:\n'
        '15 +79991234567\n\n'
        'После успешной проверки бот сам напишет в этот чат, когда заказ отправят или отменят.'
    )


def _status_query(text):
    cleaned = re.sub(r'^/status(?:@\w+)?\s*', '', text.strip(), count=1, flags=re.I)
    match = re.fullmatch(r'(\d{1,10})\s+(\+?\d[\d\s()\-]{8,40})', cleaned)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _phones_match(stored, given):
    left = re.sub(r'\D', '', stored or '')
    right = re.sub(r'\D', '', given or '')
    return len(left) >= 10 and len(right) >= 10 and left[-10:] == right[-10:]


def _limited(chat_id):
    key = f'telegram-status-{chat_id}'
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)
        return False
    if count == 1:
        cache.touch(key, 3600)
    return count > 8


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


def _api(method, payload, timeout=5):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return None
    request = Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where())) as response:
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
