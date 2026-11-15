"""Order letters. A failed mailbox does not roll back the order."""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

BUYER_SUBJECT = {
    'created': 'принят',
    'shipped': 'отправлен',
    'cancelled': 'отменён',
}


def notify_order_created(order_id):
    transaction.on_commit(lambda: _safe(_deliver_created, order_id))


def notify_order_status(order_id):
    transaction.on_commit(lambda: _safe(_deliver_status, order_id))


def deliver_mail(subject, text_template, html_template, context, recipients, reply_to=None):
    cleaned = []
    for item in recipients:
        email = (item or '').strip()
        if email and '@' in email and email not in cleaned:
            cleaned.append(email)
    if not cleaned:
        return
    text = render_to_string(text_template, context).strip() + '\n'
    html = render_to_string(html_template, context)
    subject = ' '.join(str(subject).split())
    answers = []
    for item in reply_to or []:
        email = (item or '').strip()
        if email and '@' in email and email not in answers:
            answers.append(email)
    failed = False
    for email in cleaned:
        try:
            message = EmailMultiAlternatives(
                subject, text, settings.DEFAULT_FROM_EMAIL, [email], reply_to=answers or None,
            )
            message.attach_alternative(html, 'text/html')
            message.send(fail_silently=False)
        except Exception:
            logger.exception('Mail send failed')
            failed = True
    if failed:
        raise RuntimeError('Mail send failed')


def _safe(func, order_id):
    try:
        func(order_id)
    except Exception:
        logger.exception('Order mail failed')


def _deliver_created(order_id):
    from .access import confirmed_managers, scoped_order_items
    from .models import Order
    from .telegram import order_brands

    order = Order.objects.prefetch_related('items__product__brand').filter(pk=order_id).first()
    if order is None:
        return
    items = list(order.items.all())
    _buyer(order, items, 'created')
    notice = getattr(settings, 'ORDER_NOTICE_EMAIL', '').strip()
    if notice:
        deliver_mail(
            f'NoName: новый заказ №{order.pk}',
            'emails/order_admin.txt',
            'emails/order_admin.html',
            {'order': order, 'items': items},
            [notice],
        )
    for brand in order_brands(order):
        brand_items = list(scoped_order_items(brand, order.items.select_related('product')))
        if not brand_items:
            continue
        subtotal = sum((item.line_total for item in brand_items), Decimal('0'))
        recipients = list(dict.fromkeys(
            email.strip()
            for email in confirmed_managers(brand).values_list('user__email', flat=True)
            if email and email.strip()
        ))
        deliver_mail(
            f'NoName: в заказе №{order.pk} есть позиции {brand.name}',
            'emails/order_brand.txt',
            'emails/order_brand.html',
            {'order': order, 'brand': brand, 'items': brand_items, 'subtotal': subtotal},
            recipients,
        )


def _deliver_status(order_id):
    from .models import Order

    order = Order.objects.prefetch_related('items').filter(pk=order_id).first()
    if order is None or order.status not in (Order.SHIPPED, Order.CANCELLED):
        return
    kind = 'shipped' if order.status == Order.SHIPPED else 'cancelled'
    _buyer(order, list(order.items.all()), kind)


def _buyer(order, items, kind):
    if not order.email:
        return
    deliver_mail(
        f'NoName: заказ №{order.pk} {BUYER_SUBJECT[kind]}',
        'emails/order_buyer.txt',
        'emails/order_buyer.html',
        {'order': order, 'items': items, 'kind': kind},
        [order.email],
    )
