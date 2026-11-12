"""Session-backed shopping cart. Prices and sizes always come from the database."""
from decimal import Decimal
from .models import Product

CART_SESSION_KEY = 'pustota_cart'  # Keep old sessions compatible across NoName upgrade.
DELIVERY_SESSION_KEY = 'noname_delivery_id'
MAX_PER_VARIANT = 10
MAX_TOTAL_ITEMS = 50


def _key(product_id, size):
    return f'{product_id}:{size}'


def get_cart(request):
    """Return validated live cart lines; discard deleted/unavailable/stale variants."""
    raw = request.session.get(CART_SESSION_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    identifiers = set()
    for key in raw:
        if not isinstance(key, str) or ':' not in key:
            continue
        prefix, _ = key.split(':', 1)
        if prefix.isdecimal():
            identifiers.add(int(prefix))
    products = Product.objects.filter(
        pk__in=identifiers, price__gt=0, is_published=True, is_available=True, is_deleted=False,
    ).select_related('brand').prefetch_related('images')
    available = {p.pk: p for p in products}
    cleaned = {}
    lines = []
    subtotal = Decimal('0.00')
    count = 0
    for key, qty in raw.items():
        if not isinstance(key, str) or ':' not in key:
            continue
        prefix, size = key.split(':', 1)
        if not prefix.isdecimal() or type(qty) is not int or not 1 <= qty <= MAX_PER_VARIANT:
            continue
        product = available.get(int(prefix))
        if not product or len(size) > 40 or ':' in size or size not in product.size_list or count + qty > MAX_TOTAL_ITEMS:
            continue
        canonical_key = _key(product.pk, size)
        if canonical_key in cleaned:
            continue
        amount = product.price * qty
        cleaned[canonical_key] = qty
        lines.append({
            'key': canonical_key, 'product': product, 'size': size,
            'quantity': qty, 'unit_price': product.price, 'line_total': amount,
        })
        count += qty
        subtotal += amount
    if raw != cleaned:
        request.session[CART_SESSION_KEY] = cleaned
        request.session.modified = True
    return {'lines': lines, 'subtotal': subtotal, 'count': count}


def cart_count(request):
    return get_cart(request)['count']


def put(request, product, size, qty):
    cart = get_cart(request)
    raw = request.session.get(CART_SESSION_KEY, {}).copy()
    key = _key(product.pk, size)
    previous = raw.get(key, 0)
    if qty + previous > MAX_PER_VARIANT or cart['count'] + qty > MAX_TOTAL_ITEMS:
        return False
    raw[key] = previous + qty
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True


def set_quantity(request, product_id, size, qty):
    get_cart(request)
    raw = request.session.get(CART_SESSION_KEY, {}).copy()
    key = _key(product_id, size)
    if key not in raw:
        return False
    existing = raw[key]
    count = sum(raw.values())
    if qty > MAX_PER_VARIANT or count - existing + qty > MAX_TOTAL_ITEMS:
        return False
    if qty == 0:
        raw.pop(key, None)
    else:
        raw[key] = qty
    request.session[CART_SESSION_KEY] = raw
    request.session.modified = True
    return True
