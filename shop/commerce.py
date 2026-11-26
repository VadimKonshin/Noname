"""Session-backed shopping cart. Prices, stock and preorder modes always come from the database."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from .models import DeliveryMethod, Order, OrderItem, Product, ProductVariant

CART_SESSION_KEY = 'pustota_cart'
DELIVERY_SESSION_KEY = 'noname_delivery_id'
PREORDER_OFFER_KEY = 'preorder_offer'


def cart_products(cart):
    return [line['product'] for line in cart['lines']]


def one_brand(lines):
    """One order is one brand and therefore one delivery."""
    return len({line['product'].brand_id for line in lines}) <= 1


def method_offered_by(method, products):
    """СДЭК and courier need both the brand and every item. Pickup needs the brand address and a yes on the brand or on every item."""
    if method is None or not method.is_active or method.price < 0 or not products:
        return False
    if method.code == DeliveryMethod.CDEK:
        return all(product.offers_cdek and _brand_allows(product, 'offers_cdek') for product in products)
    if method.code == DeliveryMethod.COURIER:
        return all(product.offers_courier and _brand_allows(product, 'offers_courier') for product in products)
    if method.code == DeliveryMethod.PICKUP:
        return all(_product_can_pickup(product) for product in products)
    return True


def _brand_allows(product, flag):
    brand = product.brand
    if brand is None:
        return True
    return bool(getattr(brand, flag))


def _product_can_pickup(product):
    brand = product.brand
    if brand is None or not (brand.pickup_address or '').strip():
        return False
    return bool(brand.offers_pickup or product.offers_pickup)


def pickup_place(products):
    if not products or not one_brand([{'product': product} for product in products]):
        return ''
    brand = products[0].brand
    if brand is None:
        return ''
    return (brand.pickup_address or '').strip()


def available_delivery_methods(products):
    return [
        method for method in DeliveryMethod.objects.filter(is_active=True, price__gte=0)
        if method_offered_by(method, products)
    ]
MAX_PER_VARIANT = 10
MAX_TOTAL_ITEMS = 50
MAX_OPEN_PREORDER_PER_VARIANT = 10
PREORDER_SUFFIX = ':preorder'


def open_preorder_map(product_ids):
    """Units already accepted for open preorders, keyed by product and size."""
    if not product_ids:
        return {}
    rows = (
        OrderItem.objects.filter(product_id__in=product_ids, is_preorder=True)
        .exclude(order__status=Order.CANCELLED)
        .values('product_id', 'size')
        .annotate(total=Sum('quantity'))
    )
    return {(row['product_id'], row['size']): row['total'] or 0 for row in rows}


def preorder_room(product_id, size, taken=None):
    if taken is None:
        taken = open_preorder_map([product_id])
    return MAX_OPEN_PREORDER_PER_VARIANT - taken.get((product_id, size), 0)


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
    products = Product.objects.public().filter(
        pk__in=identifiers,
        price__gt=0,
        is_available=True,
    ).select_related('brand').prefetch_related('images', 'variants')
    available = {product.pk: product for product in products}
    preorder_taken = open_preorder_map(available)
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
            if not product.allow_preorder:
                continue
            room = preorder_room(product.pk, size, preorder_taken)
            if room <= 0:
                continue
            qty = min(raw_qty, room)
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
    if not one_brand(cart['lines'] + [{'product': product}]):
        return False
    variant = next((item for item in product.variants.all() if item.size == size), None)
    if variant is None:
        return False
    if is_preorder:
        if not product.allow_preorder:
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
    if is_preorder and new_quantity > preorder_room(product.pk, size):
        return False
    if not is_preorder and new_quantity > variant.stock_quantity:
        return False
    raw[key] = new_quantity
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True


def _session_cart(request):
    raw = request.session.get(CART_SESSION_KEY, {})
    return raw if isinstance(raw, dict) else {}


def _stored_qty(raw, key):
    value = raw.get(key)
    return value if type(value) is int else 0


def preorder_fits(request, product, size, quantity):
    """True when swapping this size to a preorder of `quantity` stays inside the cart cap."""
    raw = _session_cart(request)
    previous = _stored_qty(raw, _key(product.pk, size, False)) + _stored_qty(raw, _key(product.pk, size, True))
    count = sum(value for value in raw.values() if type(value) is int)
    return count - previous + quantity <= MAX_TOTAL_ITEMS


def preorder_offer_for(request, product, size, quantity, is_preorder, adding):
    """
    A stock request that asks for more than the shelf.
    ('offer', asked, stock) when a preorder of that many still fits.
    ('room', None, None) when the open-preorder cap blocks it.
    (None, None, None) when this is not that case.
    """
    if is_preorder or not product.allow_preorder or type(quantity) is not int or not 1 <= quantity <= MAX_PER_VARIANT:
        return None, None, None
    variant = next((item for item in product.variants.all() if item.size == size), None)
    if variant is None:
        return None, None, None
    existing = 0
    if adding:
        existing = _stored_qty(_session_cart(request), _key(product.pk, size, False))
    asked = existing + quantity if adding else quantity
    if asked <= variant.stock_quantity or asked > MAX_PER_VARIANT:
        return None, None, None
    if asked > preorder_room(product.pk, size):
        return 'room', None, None
    if not preorder_fits(request, product, size, asked):
        return None, None, None
    return 'offer', asked, variant.stock_quantity


def remember_preorder_offer(request, product, size, quantity, stock, next_page):
    request.session[PREORDER_OFFER_KEY] = {
        'product_id': product.pk,
        'size': size,
        'quantity': quantity,
        'stock': stock,
        'title': product.title,
        'next': next_page,
    }
    request.session.modified = True


def pop_preorder_offer(request, product_id=None):
    offer = request.session.get(PREORDER_OFFER_KEY)
    if not isinstance(offer, dict):
        return None
    if product_id is not None and offer.get('product_id') != product_id:
        return None
    request.session.pop(PREORDER_OFFER_KEY, None)
    request.session.modified = True
    quantity = offer.get('quantity')
    stock = offer.get('stock')
    size = offer.get('size')
    title = offer.get('title')
    if (type(quantity) is not int or type(stock) is not int or not isinstance(size, str)
            or not isinstance(title, str) or not 1 <= quantity <= MAX_PER_VARIANT
            or offer.get('next') not in ('product', 'cart')):
        return None
    return offer


def replace_with_preorder(request, product, size, quantity):
    """Drop the in-stock line for this size and keep the asked quantity as a preorder."""
    if not product.allow_preorder or type(quantity) is not int or not 1 <= quantity <= MAX_PER_VARIANT:
        return False
    if not size or len(size) > 40 or ':' in size or size not in product.size_list:
        return False
    if next((item for item in product.variants.all() if item.size == size), None) is None:
        return False
    if quantity > preorder_room(product.pk, size):
        return False
    cart = get_cart(request)
    if not one_brand(cart['lines'] + [{'product': product}]):
        return False
    if not preorder_fits(request, product, size, quantity):
        return False
    raw = _session_cart(request).copy()
    raw.pop(_key(product.pk, size, False), None)
    raw[_key(product.pk, size, True)] = quantity
    request.session[CART_SESSION_KEY] = raw
    request.session.pop(PREORDER_OFFER_KEY, None)
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
    if qty and is_preorder and qty > preorder_room(product_id, size):
        return False
    if qty == 0:
        raw.pop(key, None)
    else:
        raw[key] = qty
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True


def update_order_status(order_id, status, tracking_number=None):
    with transaction.atomic():
        order = Order.objects.select_for_update().prefetch_related('items__product').get(pk=order_id)
        if status not in dict(Order.STATUS_CHOICES):
            raise ValidationError('Выбран недопустимый статус заказа.')
        if order.status == Order.CANCELLED and status != Order.CANCELLED:
            raise ValidationError('Отменённый заказ нельзя открыть повторно.')
        previous_tracking = order.tracking_number
        tracking = previous_tracking if tracking_number is None else tracking_number
        status_changed = order.status != status
        tracking_changed = tracking != previous_tracking
        if not status_changed and not tracking_changed:
            return order
        if status_changed and status == Order.CANCELLED:
            for item in order.items.all():
                _restock_item(item)
            order.items.filter(cancelled=False).update(cancelled=True)
        order.status = status
        order.tracking_number = tracking
        order.save(update_fields=['status', 'tracking_number', 'updated_at'])
        tell_buyer = (status_changed and status in (Order.SHIPPED, Order.CANCELLED)) or (
            tracking_changed and status == Order.SHIPPED
        )
        if tell_buyer:
            from .mail import notify_order_status
            from .telegram import notify_status_change
            notify_order_status(order.pk)
            notify_status_change(order.pk)
    return order


def _restock_item(item):
    if item.cancelled or item.is_preorder or item.product_id is None:
        return
    ProductVariant.objects.filter(
        product_id=item.product_id,
        size=item.size,
    ).update(stock_quantity=F('stock_quantity') + item.quantity)


def cancel_order_item(order_id, item_id):
    """Cancel one line. The order itself is cancelled only when nothing is left."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == Order.CANCELLED:
            raise ValidationError('Отменённый заказ нельзя открыть повторно.')
        item = OrderItem.objects.select_for_update().get(pk=item_id, order_id=order.pk)
        if item.cancelled:
            raise ValidationError('Эта позиция уже отменена.')
        _restock_item(item)
        item.cancelled = True
        item.save(update_fields=['cancelled'])
        if order.items.filter(cancelled=False).exists():
            return order
        order.status = Order.CANCELLED
        order.save(update_fields=['status', 'updated_at'])
        from .mail import notify_order_status
        from .telegram import notify_status_change
        notify_order_status(order.pk)
        notify_status_change(order.pk)
    return order
