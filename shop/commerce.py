"""Session-backed shopping cart. Prices, stock and preorder modes always come from the database."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from .models import Order, Product, ProductVariant

CART_SESSION_KEY = 'pustota_cart'
DELIVERY_SESSION_KEY = 'noname_delivery_id'
MAX_PER_VARIANT = 10
MAX_TOTAL_ITEMS = 50
PREORDER_SUFFIX = ':preorder'


def _key(product_id, size, is_preorder=False):
    key = f'{product_id}:{size}'
    return f'{key}{PREORDER_SUFFIX}' if is_preorder else key


def _parse_key(key):
    if not isinstance(key, str) or ':' not in key:
        return None
    prefix, size = key.split(':', 1)
    if not prefix.isdecimal():
        return None
    is_preorder = size.endswith(PREORDER_SUFFIX)
    if is_preorder:
        size = size[:-len(PREORDER_SUFFIX)]
    if not size or len(size) > 40 or ':' in size:
        return None
    return int(prefix), size, is_preorder


def get_cart(request):
    """Return validated live cart lines; discard deleted, unavailable and stale variants."""
    raw = request.session.get(CART_SESSION_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    identifiers = {parsed[0] for key in raw if (parsed := _parse_key(key))}
    products = Product.objects.filter(
        Q(brand__isnull=True) | Q(brand__is_deleted=False),
        pk__in=identifiers,
        price__gt=0,
        is_published=True,
        is_available=True,
        is_deleted=False,
    ).select_related('brand').prefetch_related('images', 'variants')
    available = {product.pk: product for product in products}
    cleaned = {}
    lines = []
    subtotal = Decimal('0.00')
    count = 0
    for key, raw_qty in raw.items():
        parsed = _parse_key(key)
        if not parsed or type(raw_qty) is not int or not 1 <= raw_qty <= MAX_PER_VARIANT:
            continue
        product_id, size, is_preorder = parsed
        product = available.get(product_id)
        if not product:
            continue
        variant = next((item for item in product.variants.all() if item.size == size), None)
        if not variant:
            continue
        if is_preorder:
            if not product.allow_preorder or variant.stock_quantity != 0:
                continue
            qty = raw_qty
        else:
            if variant.stock_quantity <= 0 or raw_qty > variant.stock_quantity:
                continue
            qty = raw_qty
        if count + qty > MAX_TOTAL_ITEMS:
            continue
        canonical_key = _key(product.pk, size, is_preorder)
        if canonical_key in cleaned:
            continue
        amount = product.price * qty
        cleaned[canonical_key] = qty
        lines.append({
            'key': canonical_key,
            'product': product,
            'variant': variant,
            'size': size,
            'quantity': qty,
            'unit_price': product.price,
            'line_total': amount,
            'is_preorder': is_preorder,
            'preorder_days': product.preorder_days if is_preorder else None,
        })
        count += qty
        subtotal += amount
    if raw != cleaned:
        request.session[CART_SESSION_KEY] = cleaned
        request.session.modified = True
    return {'lines': lines, 'subtotal': subtotal, 'count': count}


def cart_count(request):
    return get_cart(request)['count']


def put(request, product, size, qty, is_preorder=False):
    cart = get_cart(request)
    variant = next((item for item in product.variants.all() if item.size == size), None)
    if variant is None:
        return False
    if is_preorder:
        if not product.allow_preorder or variant.stock_quantity != 0:
            return False
    elif variant.stock_quantity <= 0:
        return False
    raw = request.session.get(CART_SESSION_KEY, {}).copy()
    key = _key(product.pk, size, is_preorder)
    opposite_key = _key(product.pk, size, not is_preorder)
    if opposite_key in raw:
        return False
    previous = raw.get(key, 0)
    new_quantity = qty + previous
    if new_quantity > MAX_PER_VARIANT or cart['count'] + qty > MAX_TOTAL_ITEMS:
        return False
    if not is_preorder and new_quantity > variant.stock_quantity:
        return False
    raw[key] = new_quantity
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True


def set_quantity(request, product_id, size, qty, is_preorder=False):
    cart = get_cart(request)
    raw = request.session.get(CART_SESSION_KEY, {}).copy()
    key = _key(product_id, size, is_preorder)
    if key not in raw:
        return False
    existing = raw[key]
    count = sum(raw.values())
    if qty > MAX_PER_VARIANT or count - existing + qty > MAX_TOTAL_ITEMS:
        return False
    if qty and not is_preorder:
        line = next((item for item in cart['lines'] if item['key'] == key), None)
        if line is None or qty > line['variant'].stock_quantity:
            return False
    if qty == 0:
        raw.pop(key, None)
    else:
        raw[key] = qty
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True


def update_order_status(order_id, status):
    with transaction.atomic():
        order = Order.objects.select_for_update().prefetch_related('items__product').get(pk=order_id)
        if status not in dict(Order.STATUS_CHOICES):
            raise ValidationError('Выбран недопустимый статус заказа.')
        if order.status == Order.CANCELLED and status != Order.CANCELLED:
            raise ValidationError('Отменённый заказ нельзя открыть повторно.')
        if status == Order.CANCELLED and order.status != Order.CANCELLED:
            for item in order.items.all():
                if item.is_preorder or item.product is None:
                    continue
                variant, _ = ProductVariant.objects.get_or_create(
                    product=item.product,
                    size=item.size,
                    defaults={'stock_quantity': 0},
                )
                ProductVariant.objects.filter(pk=variant.pk).update(
                    stock_quantity=F('stock_quantity') + item.quantity,
                )
        order.status = status
        order.save(update_fields=['status', 'updated_at'])
    return order
