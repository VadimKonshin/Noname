"""Cart, checkout, order history and staff analytics."""
import secrets
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from .commerce import CART_SESSION_KEY, DELIVERY_SESSION_KEY, MAX_PER_VARIANT, get_cart, put, set_quantity
from .commerce_forms import CheckoutForm, OrderStatusForm
from .models import DeliveryMethod, Order, OrderItem, Product


def selected_delivery(request):
    """Load the session selection, rejecting removed or disabled delivery methods."""
    method_id = request.session.get(DELIVERY_SESSION_KEY)
    if type(method_id) is not int:
        return None
    method = DeliveryMethod.objects.filter(pk=method_id, is_active=True, price__gte=0).first()
    if method is None:
        request.session.pop(DELIVERY_SESSION_KEY, None)
    return method


@require_GET
def cart(request):
    current = get_cart(request)
    delivery_methods = list(DeliveryMethod.objects.filter(is_active=True, price__gte=0))
    selected = selected_delivery(request)
    return render(request, 'shop/cart.html', {
        'cart': current, 'delivery_methods': delivery_methods, 'selected_method': selected,
        'grand_total': current['subtotal'] + (selected.price if selected else Decimal('0.00')),
    })


@require_POST
def cart_delivery(request):
    method_id = _integer(request.POST.get('delivery_method'))
    method = DeliveryMethod.objects.filter(pk=method_id, is_active=True, price__gte=0).first() if method_id else None
    if not method:
        messages.error(request, 'Выберите доступный способ доставки.')
        return redirect('cart')
    if not get_cart(request)['lines']:
        messages.error(request, 'Сначала добавьте товар в корзину.')
        return redirect('cart')
    request.session[DELIVERY_SESSION_KEY] = method.pk
    request.session.modified = True
    return redirect('checkout') if request.POST.get('next') == '1' else redirect('cart')


def _integer(raw):
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return None


@require_POST
def cart_add(request, slug):
    product = get_object_or_404(Product, slug=slug, price__gt=0, is_published=True, is_available=True, is_deleted=False)
    size = request.POST.get('size', '').strip()
    quantity = _integer(request.POST.get('quantity', '1'))
    if len(size) > 40 or ':' in size or size not in product.size_list:
        messages.error(request, 'Выберите доступный размер футболки.')
    elif quantity is None or not 1 <= quantity <= MAX_PER_VARIANT:
        messages.error(request, 'Выберите количество от 1 до 10.')
    elif not put(request, product, size, quantity):
        messages.error(request, 'Лимит: 10 штук одного размера и 50 штук в корзине.')
    else:
        messages.success(request, f'«{product.title}», размер {size}, добавлен в корзину.')
        if request.POST.get('go_to_cart') == '1':
            return redirect('cart')
    return redirect(product.get_absolute_url())


@require_POST
def cart_update(request):
    product_id = _integer(request.POST.get('product_id'))
    size = request.POST.get('size', '')
    quantity = _integer(request.POST.get('quantity'))
    if (product_id is None or product_id < 1 or quantity is None
            or not 0 <= quantity <= MAX_PER_VARIANT or not set_quantity(request, product_id, size, quantity)):
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

    method = selected_delivery(request)
    if not method:
        messages.info(request, 'Сначала выберите доставку или самовывоз в корзине.')
        return redirect('cart')

    token = request.session.get('checkout_token')
    if not token:
        token = secrets.token_urlsafe(32)
        request.session['checkout_token'] = token

    initial = {}
    if request.user.is_authenticated:
        initial = {'email': request.user.email, 'full_name': request.user.get_full_name() or request.user.username}
    form = CheckoutForm(request.POST if request.method == 'POST' else None,
                        initial=initial, delivery_method=method)

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
            try:
                with transaction.atomic():
                    verified_cart = get_cart(request)
                    if not verified_cart['lines']:
                        raise ValueError('Выбранные товары больше недоступны.')
                    # A fresh DB lookup prevents trusting a stale/forged price from the client.
                    current_method = DeliveryMethod.objects.filter(pk=method.pk, is_active=True).first()
                    if not current_method or current_method.price < 0:
                        raise ValueError('Способ доставки недоступен. Выберите другой в корзине.')
                    subtotal = verified_cart['subtotal']
                    order = Order.objects.create(
                        user=request.user if request.user.is_authenticated else None,
                        checkout_token=token,
                        full_name=data['full_name'], email=data['email'], phone=data['phone'],
                        delivery_method=current_method, delivery_name=current_method.name,
                        delivery_price=current_method.price, subtotal=subtotal,
                        total=subtotal + current_method.price,
                        city=data['city'], address=data['address'], postal_code=data['postal_code'],
                        comment=data['comment'],
                    )
                    OrderItem.objects.bulk_create([
                        OrderItem(order=order, product=item['product'],
                                  product_title=item['product'].title,
                                  brand_name=item['product'].brand.name if item['product'].brand else '',
                                  size=item['size'], quantity=item['quantity'],
                                  unit_price=item['unit_price'], line_total=item['line_total'])
                        for item in verified_cart['lines']
                    ])
            except IntegrityError:
                previous = Order.objects.filter(checkout_token=token).first()
                if previous and ((request.user.is_authenticated and previous.user_id == request.user.pk)
                                 or (previous.user_id is None and previous.pk in request.session.get('guest_order_ids', []))):
                    return redirect(previous.get_absolute_url())
                messages.error(request, 'Заказ не был создан. Попробуйте повторить оформление.')
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                if not request.user.is_authenticated:
                    previous_ids = request.session.get('guest_order_ids', [])
                    request.session['guest_order_ids'] = (previous_ids + [order.pk])[-20:]
                request.session.pop(CART_SESSION_KEY, None)
                request.session.pop(DELIVERY_SESSION_KEY, None)
                request.session.pop('checkout_token', None)
                request.session.modified = True
                messages.success(request, f'Заказ №{order.pk} оформлен! Мы свяжемся с вами для подтверждения.')
                return redirect(order.get_absolute_url())

    return render(request, 'shop/checkout.html', {
        'form': form, 'cart': current_cart, 'selected_method': method,
        'grand_total': current_cart['subtotal'] + method.price, 'checkout_token': token,
    })


@login_required
@require_GET
def my_orders(request):
    orders = Order.objects.filter(user=request.user).prefetch_related('items')
    return render(request, 'shop/my_orders.html', {
        'page': Paginator(orders, 10).get_page(request.GET.get('page')),
    })


@require_GET
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    can_view_any_order = request.user.has_perm('shop.view_order')
    can_change_order = request.user.has_perm('shop.change_order')
    owns_order = request.user.is_authenticated and order.user_id == request.user.pk
    owns_guest_order = order.user_id is None and order.pk in request.session.get('guest_order_ids', [])
    if not (can_view_any_order or owns_order or owns_guest_order):
        raise Http404('Заказ не найден.')
    return render(request, 'shop/order_detail.html', {
        'order': order,
        'can_view_any_order': can_view_any_order,
        'can_change_order': can_change_order,
        'status_form': OrderStatusForm(instance=order) if can_change_order else None,
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


@login_required
@permission_required('shop.change_order', raise_exception=True)
@require_POST
def change_order_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    form = OrderStatusForm(request.POST, instance=order)
    if form.is_valid():
        changed = form.save(commit=False)
        changed.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'Статус заказа №{order.pk} обновлён.')
    else:
        messages.error(request, 'Выбран недопустимый статус заказа.')
    return redirect(order.get_absolute_url())


@login_required
@permission_required('shop.view_product', raise_exception=True)
@require_GET
def dashboard_products(request):
    products = Product.objects.all().order_by('is_deleted', '-created_at')
    return render(request, 'shop/dashboard_products.html', {'products': products})


@login_required
@permission_required('shop.delete_product', raise_exception=True)
@require_http_methods(['GET', 'POST'])
def product_delete(request, slug):
    product = get_object_or_404(Product, slug=slug, is_deleted=False)
    if request.method == 'POST':
        product.soft_delete()
        messages.success(request, f'«{product.title}» удалён из каталога. История заказов сохранена.')
        return redirect('dashboard_products')
    return render(request, 'shop/product_delete.html', {'product': product})


@login_required
@permission_required('shop.change_product', raise_exception=True)
@require_POST
def product_restore(request, slug):
    product = get_object_or_404(Product, slug=slug, is_deleted=True)
    product.restore()
    messages.success(request, f'«{product.title}» восстановлен как черновик. Проверьте цену и публикацию.')
    return redirect('product_edit', slug=product.slug)
