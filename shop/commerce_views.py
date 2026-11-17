"""Cart, checkout, order history and staff analytics."""
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from .access import (
    brand_manager_required, get_brand_manager, get_scoped_object_or_404,
    product_permission_required, scoped_order_items,
)
from .commerce import (
    CART_SESSION_KEY, DELIVERY_SESSION_KEY, MAX_OPEN_PREORDER_PER_VARIANT, MAX_PER_VARIANT,
    available_delivery_methods, cart_products, get_cart, method_offered_by, one_brand,
    open_preorder_map, pickup_place, put, set_quantity, update_order_status,
)
from .cdek import CdekUnavailable, configured as cdek_configured, parcel_weight, quote_delivery
from .commerce_forms import CheckoutForm, OrderLookupForm, OrderStatusForm
from .models import DeliveryMethod, Order, OrderItem, OrderRead, Product, ProductVariant
from .mail import notify_order_created
from .telegram import cabinet_context, consume_phone_lookup, issue_bind_token, notify_new_order, order_for_phone


CDEK_QUOTE_SESSION = 'cdek_quote'


def _cdek_decision(request, postal_code, pieces, weight, selected_code):
    """Quote again on the server. The first matching view is shown; the next identical one is stored."""
    quote = quote_delivery(postal_code, pieces, weight_grams=weight)
    match = next((point for point in quote.points if point['code'] == selected_code), None)
    held = request.session.get(CDEK_QUOTE_SESSION)
    held = held if isinstance(held, dict) else {}
    same_quote = (
        held.get('postal_code') == quote.postal_code
        and held.get('pieces') == quote.pieces
        and held.get('weight') == quote.weight
        and held.get('price') == str(quote.price)
    )
    confirmed = bool(match) and same_quote and held.get('point') in ('', match['code'])
    request.session[CDEK_QUOTE_SESSION] = {
        'postal_code': quote.postal_code,
        'pieces': quote.pieces,
        'weight': quote.weight,
        'price': str(quote.price),
        'point': match['code'] if match else '',
    }
    request.session.modified = True
    return quote, match, confirmed


def _checkout_response(request, form, current_cart, method, token, quote=None, selected_point=''):
    live = cdek_configured() and method.code == DeliveryMethod.CDEK
    if quote is not None:
        shipping = quote.price
        known = True
    elif live:
        shipping = None
        known = False
    else:
        shipping = method.price
        known = True
    return render(request, 'shop/checkout.html', {
        'form': form,
        'cart': current_cart,
        'selected_method': method,
        'grand_total': current_cart['subtotal'] + (shipping or Decimal('0.00')),
        'shipping_price': shipping,
        'shipping_known': known,
        'checkout_token': token,
        'cdek_live': live,
        'cdek_quote': quote,
        'selected_point': selected_point,
        'pickup_place': pickup_place(cart_products(current_cart)),
    })


def selected_delivery(request, products):
    """Load the session selection, dropping a method this cart can no longer use."""
    method_id = request.session.get(DELIVERY_SESSION_KEY)
    if type(method_id) is not int:
        return None
    method = DeliveryMethod.objects.filter(pk=method_id, is_active=True, price__gte=0).first()
    if not method_offered_by(method, products):
        request.session.pop(DELIVERY_SESSION_KEY, None)
        return None
    return method


@require_GET
def cart(request):
    current = get_cart(request)
    products = cart_products(current)
    single_brand = one_brand(current['lines'])
    delivery_methods = available_delivery_methods(products) if single_brand else []
    selected = selected_delivery(request, products) if single_brand else None
    live_selected = cdek_configured() and selected is not None and selected.code == DeliveryMethod.CDEK
    shipping = Decimal('0.00') if live_selected or selected is None else selected.price
    return render(request, 'shop/cart.html', {
        'cart': current, 'delivery_methods': delivery_methods, 'selected_method': selected,
        'grand_total': current['subtotal'] + shipping,
        'cdek_selected_live': live_selected,
        'single_brand': single_brand,
        'pickup_place': pickup_place(products) if single_brand else '',
    })


@require_POST
def cart_delivery(request):
    current = get_cart(request)
    if not one_brand(current['lines']):
        messages.error(request, 'В одном заказе — один бренд и одна доставка. Уберите вещи другого бренда.')
        return redirect('cart')
    method_id = _integer(request.POST.get('delivery_method'))
    method = DeliveryMethod.objects.filter(pk=method_id, is_active=True, price__gte=0).first() if method_id else None
    if not method_offered_by(method, cart_products(current)):
        messages.error(request, 'Выберите доступный способ доставки.')
        return redirect('cart')
    if not current['lines']:
        messages.error(request, 'Сначала добавьте товар в корзину.')
        return redirect('cart')
    request.session[DELIVERY_SESSION_KEY] = method.pk
    request.session.pop(CDEK_QUOTE_SESSION, None)
    request.session.modified = True
    return redirect('checkout') if request.POST.get('next') == '1' else redirect('cart')


def _integer(raw):
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return None


@require_POST
def cart_add(request, slug):
    product = get_object_or_404(
        Product.objects.public().filter(price__gt=0, is_available=True).prefetch_related('variants'),
        slug=slug,
    )
    size = request.POST.get('size', '').strip()
    quantity = _integer(request.POST.get('quantity', '1'))
    is_preorder = request.POST.get('order_type') == 'preorder'
    if len(size) > 40 or ':' in size or size not in product.size_list:
        messages.error(request, 'Выберите доступный размер футболки.')
    elif quantity is None or not 1 <= quantity <= MAX_PER_VARIANT:
        messages.error(request, 'Выберите количество от 1 до 10.')
    elif not one_brand(get_cart(request)['lines'] + [{'product': product}]):
        messages.error(request, 'В одном заказе — один бренд и одна доставка. Оформите текущую корзину или уберите вещи другого бренда.')
        return redirect('cart')
    elif not put(request, product, size, quantity, is_preorder):
        messages.error(request, 'Выбранный размер недоступен в этом количестве. Обновите остаток или режим заказа.')
    else:
        mode = 'под заказ' if is_preorder else 'в корзину'
        messages.success(request, f'«{product.title}», размер {size}, добавлен {mode}.')
        if request.POST.get('go_to_cart') == '1':
            return redirect('cart')
    return redirect(product.get_absolute_url())


@require_POST
def cart_update(request):
    product_id = _integer(request.POST.get('product_id'))
    size = request.POST.get('size', '')
    quantity = _integer(request.POST.get('quantity'))
    is_preorder = request.POST.get('order_type') == 'preorder'
    if (product_id is None or product_id < 1 or quantity is None
            or not 0 <= quantity <= MAX_PER_VARIANT
            or not set_quantity(request, product_id, size, quantity, is_preorder)):
        messages.error(request, 'Не удалось изменить количество. Обновите корзину.')
    else:
        messages.success(request, 'Корзина обновлена.')
    return redirect('cart')


@ratelimit(group='checkout', key='ip', rate='10/h', method='POST')
@require_http_methods(['GET', 'POST'])
def checkout(request):
    current_cart = get_cart(request)
    if not current_cart['lines']:
        messages.error(request, 'Добавьте товары в корзину перед оформлением.')
        return redirect('cart')
    if not one_brand(current_cart['lines']):
        messages.error(request, 'В одном заказе — один бренд и одна доставка. Уберите вещи другого бренда.')
        return redirect('cart')

    method = selected_delivery(request, cart_products(current_cart))
    if not method:
        messages.info(request, 'Сначала выберите доставку в корзине.')
        return redirect('cart')

    token = request.session.get('checkout_token')
    if not token:
        token = secrets.token_urlsafe(32)
        request.session['checkout_token'] = token

    initial = {}
    if request.user.is_authenticated:
        initial = {'email': request.user.email, 'full_name': request.user.get_full_name() or request.user.username}
    live_cdek = cdek_configured() and method.code == DeliveryMethod.CDEK
    form = CheckoutForm(
        request.POST if request.method == 'POST' else None,
        initial=initial, delivery_method=method, cdek_live=live_cdek,
    )

    if request.method == 'POST' and form.is_valid():
        submitted_token = request.POST.get('checkout_token', '')
        if not submitted_token or not secrets.compare_digest(submitted_token, token):
            existing = Order.objects.filter(checkout_token=submitted_token).first() if submitted_token else None
            guest_ids = request.session.get('guest_order_ids', [])
            if existing and ((request.user.is_authenticated and existing.user_id == request.user.pk)
                             or (existing.user_id is None and existing.pk in guest_ids)):
                return redirect(existing.get_absolute_url())
            messages.error(request, 'Форма устарела. Обновите страницу и повторите оформление.')
        else:
            data = form.cleaned_data
            quote = None
            match = None
            if live_cdek:
                selected_code = (request.POST.get('delivery_point') or '').strip()[:64]
                pieces = sum(line['quantity'] for line in current_cart['lines'])
                try:
                    quote, match, confirmed = _cdek_decision(
                        request, data['postal_code'], pieces, parcel_weight(current_cart['lines']), selected_code,
                    )
                except CdekUnavailable:
                    messages.error(
                        request,
                        'СДЭК не ответил. Заказ не создан, цена не стала нулевой. Можно выбрать Почту России или повторить расчёт.',
                    )
                    return _checkout_response(request, form, current_cart, method, token)
                if not confirmed:
                    if selected_code and match is None:
                        messages.error(request, 'Этот пункт выдачи не пришёл от СДЭК. Выберите пункт из списка.')
                    elif match is None:
                        messages.info(request, f'СДЭК посчитал доставку: {quote.price} ₽. Выберите пункт и отправьте заказ ещё раз.')
                    else:
                        messages.info(request, f'Стоимость СДЭК: {quote.price} ₽. Отправьте заказ ещё раз, чтобы подтвердить пункт.')
                    return _checkout_response(
                        request, form, current_cart, method, token,
                        quote=quote, selected_point=match['code'] if match else '',
                    )
            try:
                with transaction.atomic():
                    verified_cart = get_cart(request)
                    if not verified_cart['lines']:
                        raise ValueError('Выбранные товары больше недоступны.')
                    submitted_lines = {line['key']: line['quantity'] for line in current_cart['lines']}
                    verified_lines = {line['key']: line['quantity'] for line in verified_cart['lines']}
                    if verified_lines != submitted_lines:
                        raise ValueError('Остатки или доступность предзаказа изменились. Проверьте корзину перед оформлением.')
                    current_method = DeliveryMethod.objects.filter(pk=method.pk, is_active=True).first()
                    if not one_brand(verified_cart['lines']):
                        raise ValueError('В одном заказе — один бренд и одна доставка.')
                    if not method_offered_by(current_method, cart_products(verified_cart)):
                        raise ValueError('Способ доставки недоступен. Выберите другой в корзине.')
                    if quote is not None:
                        if (
                            sum(item['quantity'] for item in verified_cart['lines']) != quote.pieces
                            or parcel_weight(verified_cart['lines']) != quote.weight
                        ):
                            raise ValueError('Корзина изменилась. Рассчитайте доставку ещё раз.')
                        agreed = quote.price
                        eta = quote.eta or current_method.eta
                        point_code = match['code']
                        point_address = match['address']
                    else:
                        agreed = current_method.price
                        eta = current_method.eta
                        point_code = ''
                        point_address = ''
                    if current_method.code == DeliveryMethod.PICKUP:
                        place = pickup_place(cart_products(verified_cart))
                        if not place:
                            raise ValueError('Самовывоз сейчас недоступен: у бренда нет адреса.')
                    else:
                        place = current_method.pickup_address
                    address = (data['address'] or '').strip() or point_address
                    preorder_lines = [item for item in verified_cart['lines'] if item['is_preorder']]
                    if preorder_lines:
                        variant_ids = sorted({item['variant'].pk for item in preorder_lines})
                        list(ProductVariant.objects.select_for_update().filter(pk__in=variant_ids).order_by('pk'))
                        taken = open_preorder_map({item['product'].pk for item in preorder_lines})
                        for item in preorder_lines:
                            already = taken.get((item['product'].pk, item['size']), 0)
                            if already + item['quantity'] > MAX_OPEN_PREORDER_PER_VARIANT:
                                raise ValueError(
                                    f'Предзаказ «{item["product"].title}», размер {item["size"]}, уже набран. Уменьшите количество.'
                                )
                    for item in verified_cart['lines']:
                        # Предзаказ не списывает склад, пока менеджер его не согласует.
                        if item['is_preorder']:
                            continue
                        reserved = ProductVariant.objects.filter(
                            pk=item['variant'].pk,
                            stock_quantity__gte=item['quantity'],
                        ).update(stock_quantity=F('stock_quantity') - item['quantity'])
                        if reserved != 1:
                            raise ValueError(f'Недостаточно товара «{item["product"].title}», размер {item["size"]}.')
                    subtotal = verified_cart['subtotal']
                    order = Order.objects.create(
                        user=request.user if request.user.is_authenticated else None,
                        checkout_token=token,
                        full_name=data['full_name'], email=data['email'], phone=data['phone'],
                        delivery_method=current_method, delivery_name=current_method.name,
                        delivery_eta=eta, pickup_address=place,
                        delivery_point_code=point_code, delivery_point_address=point_address,
                        delivery_price=agreed, subtotal=subtotal,
                        total=subtotal + agreed,
                        city=data['city'], address=address, postal_code=data['postal_code'],
                        comment=data['comment'],
                    )
                    OrderItem.objects.bulk_create([
                        OrderItem(order=order, product=item['product'],
                                  product_title=item['product'].title,
                                  brand_name=item['product'].brand.name if item['product'].brand else '',
                                  size=item['size'], quantity=item['quantity'],
                                  unit_price=item['unit_price'], line_total=item['line_total'],
                                  is_preorder=item['is_preorder'], preorder_days=item['preorder_days'])
                        for item in verified_cart['lines']
                    ])
                    notify_new_order(order.pk)
                    notify_order_created(order.pk)
            except IntegrityError:
                previous = Order.objects.filter(checkout_token=token).first()
                if previous and ((request.user.is_authenticated and previous.user_id == request.user.pk)
                                 or (previous.user_id is None and previous.pk in request.session.get('guest_order_ids', []))):
                    return redirect(previous.get_absolute_url())
                messages.error(request, 'Заказ не был создан. Попробуйте повторить оформление.')
            except ValueError as exc:
                messages.error(request, str(exc))
                request.session.pop(CDEK_QUOTE_SESSION, None)
            else:
                if not request.user.is_authenticated:
                    previous_ids = request.session.get('guest_order_ids', [])
                    request.session['guest_order_ids'] = (previous_ids + [order.pk])[-20:]
                request.session.pop(CART_SESSION_KEY, None)
                request.session.pop(DELIVERY_SESSION_KEY, None)
                request.session.pop('checkout_token', None)
                request.session.pop(CDEK_QUOTE_SESSION, None)
                request.session.modified = True
                messages.success(request, f'Заказ №{order.public_number} принят. Оплата онлайн не списывалась: мы свяжемся по телефону или почте, чтобы подтвердить его. Сохраните номер.')
                return redirect(order.get_absolute_url())

    return _checkout_response(request, form, current_cart, method, token)


@ratelimit(group='order_lookup', key='ip', rate='8/h', method='POST')
@require_http_methods(['GET', 'POST'])
def order_lookup(request):
    form = OrderLookupForm(request.POST if request.method == 'POST' else None)
    order = None
    outcome = ''
    if request.method == 'POST' and form.is_valid():
        if consume_phone_lookup('web', form.cleaned_data['phone']):
            outcome = 'limited'
        else:
            order = order_for_phone(form.cleaned_data['order_id'], form.cleaned_data['phone'])
            outcome = 'found' if order else 'missing'
    return render(request, 'shop/order_lookup.html', {'form': form, 'order': order, 'outcome': outcome})


@login_required
@require_GET
def my_orders(request):
    orders = Order.objects.filter(user=request.user).prefetch_related('items')
    return render(request, 'shop/my_orders.html', {
        'page': Paginator(orders, 10).get_page(request.GET.get('page')),
    })


def attach_brand_items(orders, brand):
    items = scoped_order_items(brand, OrderItem.objects.select_related('product'))
    return orders.prefetch_related(Prefetch('items', queryset=items, to_attr='brand_items'))


def purchase_bars(active_items, days=14):
    """Daily totals for this brand only. Heights are SVG units, not inline CSS."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    totals = {}
    rows = (
        active_items.filter(order__created_at__date__gte=start)
        .annotate(day=TruncDate('order__created_at'))
        .values('day')
        .annotate(amount=Sum('line_total'))
    )
    for row in rows:
        day = row['day']
        if isinstance(day, datetime):
            day = day.date()
        elif isinstance(day, str):
            day = date.fromisoformat(day[:10])
        totals[day] = row['amount'] or Decimal('0')
    peak = max(totals.values(), default=Decimal('0'))
    bars = []
    slot = 44
    for index in range(days):
        day = start + timedelta(days=index)
        amount = totals.get(day, Decimal('0'))
        height = 0 if not peak or amount == 0 else max(6, int((amount / peak) * 120))
        x = 8 + index * slot
        bars.append({
            'x': x,
            'y': 148 - height,
            'h': height,
            'label': day.strftime('%d'),
            'label_x': x + 2,
            'amount': amount,
        })
    return bars


def set_brand_totals(orders):
    for order in orders:
        order.brand_total = sum((item.line_total for item in order.brand_items), Decimal('0.00'))
    return orders


@require_GET
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    can_view_any_order = request.user.has_perm('shop.view_order')
    owns_order = request.user.is_authenticated and order.user_id == request.user.pk
    owns_guest_order = order.user_id is None and order.pk in request.session.get('guest_order_ids', [])
    manager = get_brand_manager(request.user)
    brand_items = list(scoped_order_items(manager.brand, order.items)) if manager else []
    can_view_brand_order = bool(brand_items)
    brand_order_view = can_view_brand_order and not (can_view_any_order or owns_order or owns_guest_order)
    if not (can_view_any_order or owns_order or owns_guest_order or can_view_brand_order):
        raise Http404('Заказ не найден.')
    buyer_view = (owns_order or owns_guest_order) and not can_view_any_order and not can_view_brand_order
    if buyer_view:
        return render(request, 'shop/order_thanks.html', {'order': order})
    can_change_order = request.user.has_perm('shop.change_order') and not brand_order_view
    if request.user.is_authenticated and not owns_order and not owns_guest_order and (can_view_any_order or can_view_brand_order):
        OrderRead.objects.update_or_create(order=order, user=request.user)
    return render(request, 'shop/order_detail.html', {
        'order': order,
        'order_items': brand_items if brand_order_view else order.items.all(),
        'brand_order_view': brand_order_view,
        'brand_subtotal': sum((item.line_total for item in brand_items), Decimal('0.00')),
        'can_view_any_order': can_view_any_order,
        'can_change_order': can_change_order,
        'status_form': OrderStatusForm(instance=order) if can_change_order else None,
        'order_reads': order.reads.select_related('user') if can_view_any_order else None,
    })


@login_required
@permission_required('shop.view_order', raise_exception=True)
@require_GET
def dashboard(request):
    active_orders = Order.objects.exclude(status=Order.CANCELLED)
    status_counts = {entry['status']: entry['count'] for entry in Order.objects.values('status').annotate(count=Count('pk'))}
    totals = active_orders.aggregate(total=Sum('total'), subtotal=Sum('subtotal'))
    # The values include unfulfilled orders, i.e. NOT verified payments/revenue.
    sales = (OrderItem.objects.exclude(order__status=Order.CANCELLED)
             .annotate(display_title=Coalesce('product__title', 'product_title'))
             .values('product_id', 'display_title', 'product__slug', 'product__is_deleted')
             .annotate(units=Sum('quantity'), value=Sum('line_total'), order_count=Count('order_id', distinct=True))
             .order_by('-units', '-value'))
    brand_sales = (OrderItem.objects.exclude(order__status=Order.CANCELLED)
                   .values('brand_name').annotate(units=Sum('quantity'),
                                                  value=Sum('line_total'),
                                                  orders=Count('order_id', distinct=True))
                   .order_by('-value'))
    recent = Order.objects.select_related('user').order_by('-created_at')[:8]
    return render(request, 'shop/dashboard.html', {
        'orders_count': Order.objects.count(),
        'active_orders_count': active_orders.count(),
        'orders_total': totals['total'] or Decimal('0.00'),
        'items_total': totals['subtotal'] or Decimal('0.00'),
        'status_counts': status_counts,
        'sold_models': sales,
        'brand_sales': brand_sales,
        'recent_orders': recent,
        'cancelled_count': status_counts.get(Order.CANCELLED, 0),
        'new_count': status_counts.get(Order.NEW, 0),
    })


@brand_manager_required
@require_POST
def brand_telegram_connect(request):
    manager = request.brand_manager
    if manager.brand_id is None or manager.brand.is_deleted:
        messages.error(request, 'Сначала создайте бренд, затем подключайте Telegram.')
        return redirect('brand_create')
    request.session['telegram_bind_token'] = issue_bind_token(manager)
    messages.success(request, 'Ссылка действует 30 минут. Откройте её в Telegram с телефона менеджера.')
    return redirect('brand_dashboard')


@brand_manager_required
@require_POST
def brand_telegram_disconnect(request):
    manager = request.brand_manager
    manager.telegram_chat_id = None
    manager.telegram_bind_hash = ''
    manager.telegram_bind_expires = None
    manager.save(update_fields=['telegram_chat_id', 'telegram_bind_hash', 'telegram_bind_expires'])
    request.session.pop('telegram_bind_token', None)
    messages.success(request, 'Telegram отключён. Новые заказы в этот чат больше не приходят.')
    return redirect('brand_dashboard')


@brand_manager_required
@require_GET
def brand_dashboard(request):
    brand = request.brand_manager.brand
    items = scoped_order_items(brand, OrderItem.objects.all())
    orders = Order.objects.filter(items__in=items).distinct()
    active_orders = orders.exclude(status=Order.CANCELLED)
    active_items = items.exclude(order__status=Order.CANCELLED)
    totals = active_items.aggregate(units=Sum('quantity'), value=Sum('line_total'))
    status_counts = {
        entry['status']: entry['count']
        for entry in orders.values('status').annotate(count=Count('pk', distinct=True))
    }
    sales = (active_items.annotate(display_title=Coalesce('product__title', 'product_title'))
             .values('product_id', 'display_title', 'product__slug', 'product__is_deleted')
             .annotate(units=Sum('quantity'), value=Sum('line_total'), order_count=Count('order_id', distinct=True))
             .order_by('-units', '-value'))
    recent = attach_brand_items(orders.select_related('user').order_by('-created_at'), brand)[:8]
    return render(request, 'shop/brand_dashboard.html', {
        **cabinet_context(request.brand_manager, request.session.get('telegram_bind_token')),
        'brand': brand,
        'orders_count': orders.count(),
        'active_orders_count': active_orders.count(),
        'sold_units': totals['units'] or 0,
        'items_total': totals['value'] or Decimal('0.00'),
        'status_counts': status_counts,
        'sold_models': sales,
        'recent_orders': set_brand_totals(recent),
        'cancelled_count': status_counts.get(Order.CANCELLED, 0),
        'new_count': status_counts.get(Order.NEW, 0),
        'purchase_bars': purchase_bars(active_items),
    })


@login_required
@permission_required('shop.view_order', raise_exception=True)
@require_GET
def dashboard_orders(request):
    status = request.GET.get('status', '')
    orders = Order.objects.select_related('user').order_by('-created_at')
    if status in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status)
    else:
        status = ''
    return render(request, 'shop/dashboard_orders.html', {
        'page': Paginator(orders, 20).get_page(request.GET.get('page')),
        'statuses': Order.STATUS_CHOICES, 'selected_status': status,
    })


@brand_manager_required
@require_GET
def brand_orders(request):
    brand = request.brand_manager.brand
    items = scoped_order_items(brand, OrderItem.objects.all())
    orders = Order.objects.filter(items__in=items).distinct().select_related('user').order_by('-created_at')
    status = request.GET.get('status', '')
    if status in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status)
    else:
        status = ''
    products = Product.objects.filter(brand=brand).order_by('title')
    product_id = _integer(request.GET.get('product'))
    selected_product = products.filter(pk=product_id).first() if product_id else None
    if selected_product:
        orders = orders.filter(items__product=selected_product)
    else:
        product_id = ''
    orders = attach_brand_items(orders, brand)
    page = Paginator(orders, 20).get_page(request.GET.get('page'))
    set_brand_totals(page)
    return render(request, 'shop/brand_orders.html', {
        'brand': brand,
        'page': page,
        'statuses': Order.STATUS_CHOICES,
        'selected_status': status,
        'products': products,
        'selected_product_id': selected_product.pk if selected_product else '',
    })


@login_required
@permission_required('shop.change_order', raise_exception=True)
@require_POST
def change_order_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    form = OrderStatusForm(request.POST, instance=order)
    if form.is_valid():
        tracking = form.cleaned_data['tracking_number'] if 'tracking_number' in request.POST else None
        try:
            update_order_status(order.pk, form.cleaned_data['status'], tracking_number=tracking)
        except ValidationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f'Статус заказа №{order.public_number} обновлён.')
    else:
        messages.error(request, ' '.join(error for errors in form.errors.values() for error in errors))
    return redirect(order.get_absolute_url())


@login_required
@permission_required('shop.view_product', raise_exception=True)
@require_GET
def dashboard_products(request):
    products = Product.objects.all().order_by('is_deleted', '-created_at').prefetch_related('variants')
    return render(request, 'shop/dashboard_products.html', {
        'products': products,
        'can_change_products': request.user.has_perm('shop.change_product'),
        'can_delete_products': request.user.has_perm('shop.delete_product'),
    })


@product_permission_required('shop.delete_product')
@require_http_methods(['GET', 'POST'])
def product_delete(request, slug):
    product = get_scoped_object_or_404(request, Product.objects, slug=slug, is_deleted=False)
    is_brand_manager = bool(getattr(request, 'brand_manager', None))
    if request.method == 'POST':
        product.soft_delete()
        messages.success(request, f'«{product.title}» удалён из каталога. История заказов сохранена.')
        return redirect('brand_products' if is_brand_manager else 'dashboard_products')
    return render(request, 'shop/product_delete.html', {
        'product': product, 'brand_cabinet': is_brand_manager,
    })


@product_permission_required('shop.change_product')
@require_POST
def product_restore(request, slug):
    product = get_scoped_object_or_404(request, Product.objects, slug=slug, is_deleted=True)
    product.restore()
    messages.success(request, f'«{product.title}» восстановлен как черновик. Проверьте цену и публикацию.')
    return redirect('product_edit', slug=product.slug)
