import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import (
    brand_account_home, brand_manager_required, get_brand_manager, get_scoped_object_or_404,
    product_permission_required, redirect_unverified_manager,
)
from .accounts import (
    activate_without_mailbox, remember_console_link, save_new_account, send_confirmation,
    show_console_mail, user_email_verified, uses_console_mail,
)
from .forms import (
    BrandAuthenticationForm, BrandCreateForm, BrandDeleteForm, BrandMessageForm, BrandProfileForm, BrandReplyForm,
    BrandManagerRegisterForm, EmailOrUsernameAuthenticationForm, ProductForm, ProductSizeForm, RegisterForm,
)
from .commerce import MAX_OPEN_PREORDER_PER_VARIANT, pop_preorder_offer
from .models import Brand, BrandManager, BrandMessage, DeliveryMethod, Product, ProductImage, ProductVariant
from .mail import deliver_mail
from .telegram import notify_brand_contact
from .validators import validate_product_image


PHOTO_MAX_COUNT = 30
logger = logging.getLogger(__name__)


def public_products():
    return Product.objects.public()


def collaboration(request):
    return render(request, 'shop/collaboration.html')


def info_page(request, page):
    if page not in {'delivery', 'returns', 'contacts'}:
        raise Http404
    carriers = {
        method.code: method
        for method in DeliveryMethod.objects.filter(
            is_active=True,
            code__in=(DeliveryMethod.CDEK, DeliveryMethod.POST, DeliveryMethod.PICKUP),
        )
    }
    return render(request, 'shop/info_page.html', {'page': page, 'carriers': carriers})


def public_brand(slug):
    return get_object_or_404(Brand, slug=slug, is_active=True, is_deleted=False)


def message_product(brand, slug):
    slug = (slug or '').strip()[:180]
    if not slug:
        return None
    return public_products().filter(brand=brand, slug=slug).first()


def brand_storefront(request, slug):
    brand = public_brand(slug)
    products = list(public_products().filter(brand=brand).select_related('brand').prefetch_related('images', 'variants'))
    address = (brand.pickup_address or '').strip()
    pickup_open = bool(address) and (brand.offers_pickup or any(product.offers_pickup for product in products))
    return render(request, 'shop/brand_storefront.html', {
        'brand': brand,
        'products': products,
        'piece_word': ru_plural(len(products), 'вещь', 'вещи', 'вещей'),
        'receive_line': receive_line(brand, products),
        'pickup_open': pickup_open,
    })


@ratelimit(group='brand_message', key='ip', rate='8/h', method='POST')
@require_http_methods(['GET', 'POST'])
def brand_message(request, slug):
    brand = public_brand(slug)
    posted_product = request.POST.get('product') if request.method == 'POST' else request.GET.get('product')
    product = message_product(brand, posted_product)
    user = request.user if request.user.is_authenticated else None
    lock_email = bool(user and user.email)
    initial = {}
    if user:
        initial['sender_name'] = (user.get_full_name() or user.username)[:120]
        if lock_email:
            initial['sender_email'] = user.email
    form = BrandMessageForm(
        request.POST if request.method == 'POST' else None,
        initial=initial,
        lock_email=lock_email,
    )
    if request.method == 'POST' and form.is_valid():
        note = BrandMessage.objects.create(
            brand=brand,
            product=product,
            user=user,
            sender_name=form.cleaned_data['sender_name'],
            sender_email=user.email.lower() if lock_email else form.cleaned_data['sender_email'],
            sender_telegram=form.cleaned_data['sender_telegram'],
            body=form.cleaned_data['body'],
        )
        notify_brand_contact(note.pk)
        if note.sender_email:
            messages.success(request, 'Сообщение у менеджера бренда. Если оставили почту, ответ придёт на неё.')
        else:
            messages.success(request, 'Сообщение у менеджера бренда. Ответ придёт в Telegram, который вы написали.')
        return redirect('brand_storefront', slug=brand.slug)
    return render(request, 'shop/brand_message.html', {'brand': brand, 'product': product, 'form': form})


@brand_manager_required
@require_GET
def brand_messages(request):
    brand = request.brand_manager.brand
    if brand is None:
        return redirect('brand_create')
    page = Paginator(
        brand.messages.select_related('product'),
        20,
    ).get_page(request.GET.get('page'))
    return render(request, 'shop/brand_messages.html', {'brand': brand, 'page': page})


@ratelimit(group='brand_reply', key='user', rate='8/h', method='POST')
@brand_manager_required
@require_http_methods(['GET', 'POST'])
def brand_message_detail(request, pk):
    brand = request.brand_manager.brand
    if brand is None:
        return redirect('brand_create')
    note = get_object_or_404(BrandMessage.objects.select_related('product'), pk=pk, brand=brand)
    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=['read_at'])
    form = BrandReplyForm(request.POST if request.method == 'POST' and note.replied_at is None else None)
    if request.method == 'POST' and note.replied_at is None and not note.sender_email:
        messages.error(request, 'Почты в сообщении нет. Напишите покупателю в Telegram, который он оставил.')
    elif request.method == 'POST' and note.replied_at is None and form.is_valid():
        reply = form.cleaned_data['reply']
        reply_to = [request.user.email] if request.user.email else None
        try:
            deliver_mail(
                f'NoName: ответ {brand.name}',
                'emails/brand_reply.txt',
                'emails/brand_reply.html',
                {'brand': brand, 'note': note, 'reply': reply},
                [note.sender_email],
                reply_to=reply_to,
            )
        except RuntimeError:
            messages.error(request, 'Письмо не ушло. Ответ не сохранён, его можно отправить ещё раз.')
        else:
            note.reply_body = reply
            note.replied_at = timezone.now()
            note.replied_by = request.user
            note.save(update_fields=['reply_body', 'replied_at', 'replied_by'])
            messages.success(request, f'Ответ отправлен на {note.sender_email}.')
            return redirect('brand_message_detail', pk=note.pk)
    return render(request, 'shop/brand_message_detail.html', {'brand': brand, 'note': note, 'form': form})


def home(request):
    products = public_products().select_related('brand').prefetch_related('images', 'variants')
    featured = list(products.filter(is_featured=True).order_by('?')[:4])
    if not featured:
        featured = list(products.order_by('?')[:4])
    return render(request, 'shop/home.html', {'featured': featured})


def catalog(request):
    products = public_products().select_related('brand').prefetch_related('images', 'variants')
    brands = Brand.objects.filter(
        is_active=True, is_deleted=False, products__is_published=True, products__is_deleted=False,
    ).distinct().order_by('sort_order', 'name')
    selected_brand = request.GET.get('brand', '').strip()[:150]
    selected = brands.filter(slug=selected_brand).first() if selected_brand else None
    if selected:
        products = products.filter(brand=selected)
    elif selected_brand:
        products = products.none()
    query = request.GET.get('q', '').strip()[:120]
    if query:
        products = products.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(color__icontains=query) | Q(brand__name__icontains=query))
    sort = request.GET.get('sort', 'new')
    if sort == 'stock':
        in_stock = ProductVariant.objects.filter(product_id=OuterRef('pk'), stock_quantity__gt=0)
        products = products.annotate(variant_in_stock=Exists(in_stock)).annotate(
            stock_rank=Case(
                When(Q(variant_in_stock=True) | Q(allow_preorder=True), then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).order_by('stock_rank', '-created_at', '-pk')
    else:
        orders = {'new': '-created_at', 'price_low': 'price', 'price_high': '-price', 'name': 'title'}
        products = products.order_by(orders.get(sort, '-created_at'), '-pk')
    page = Paginator(products, 9).get_page(request.GET.get('page'))
    total = page.paginator.count
    if selected:
        catalog_line = f'{total} {ru_plural(total, "вещь", "вещи", "вещей")} · {selected.name}'
    else:
        marks = brands.count()
        catalog_line = (
            f'{total} {ru_plural(total, "вещь", "вещи", "вещей")} · '
            f'{marks} {ru_plural(marks, "бренд", "бренда", "брендов")} на площадке'
        )
    return render(request, 'shop/catalog.html', {
        'page': page, 'query': query, 'sort': sort, 'total': total,
        'selected_brand': selected.slug if selected else '',
        'brands': brands, 'catalog_line': catalog_line,
    })


def product_detail(request, slug):
    product = get_object_or_404(public_products().select_related('brand').prefetch_related('images', 'variants'), slug=slug)
    manager = get_brand_manager(request.user)
    manages_brand = manager and product.brand_id == manager.brand_id
    related = []
    if product.brand_id:
        related = list(
            public_products().filter(brand_id=product.brand_id).exclude(pk=product.pk)
            .select_related('brand').prefetch_related('images', 'variants')[:3]
        )
    photo_count = (1 if product.cover else 0) + len(product.images.all())
    return render(request, 'shop/product_detail.html', {
        'product': product,
        'many_photos': photo_count > 1,
        'related': related,
        'can_change_product': request.user.has_perm('shop.change_product') or manages_brand,
        'can_delete_product': request.user.has_perm('shop.delete_product') or manages_brand,
        'preorder_offer': pop_preorder_offer(request, product.pk),
    })


def ru_plural(number, one, few, many):
    number = abs(int(number))
    if 11 <= number % 100 <= 14:
        return many
    if number % 10 == 1:
        return one
    if 2 <= number % 10 <= 4:
        return few
    return many


def receive_line(brand, products):
    ways = []
    if brand.offers_cdek:
        ways.append('СДЭК')
    if brand.offers_post:
        ways.append('Почта России')
    address = (brand.pickup_address or '').strip()
    if address and (brand.offers_pickup or any(product.offers_pickup for product in products)):
        ways.append('самовывоз')
    if not ways:
        return 'Бренд ещё выбирает, как отдать вещь'
    return ' · '.join(ways)


def ratelimited_error(request, exception=None):
    return render(request, '429.html', status=429)


@ratelimit(group='register', key='ip', rate='5/h', method='POST')
@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = RegisterForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        user = save_new_account(form)
        if user is not None:
            return open_new_account(request, user, 'Аккаунт создан. Подтвердите почту по ссылке из письма.')
    return render(request, 'accounts/register.html', {'form': form})


class StoreLoginView(auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = EmailOrUsernameAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not user_email_verified(self.request.user):
            messages.info(self.request, 'Почта ещё не подтверждена. Откройте ссылку из письма или отправьте её ещё раз.')
        return response


def open_new_account(request, user, success_message):
    login(request, user)
    if uses_console_mail() and not settings.DEBUG:
        activate_without_mailbox(user)
        messages.success(request, 'Аккаунт создан.')
        return redirect(brand_account_home(user))
    try:
        link = send_confirmation(user)
    except Exception:
        logger.exception('Confirmation mail failed')
        messages.warning(request, 'Аккаунт создан, но письмо не отправилось. Запросите ссылку ещё раз.')
    else:
        remember_console_link(request, link)
        if show_console_mail():
            success_message += ' Почтовый сервер не подключён: письмо напечатано в консоли сервера и показано на этой странице.'
        messages.success(request, success_message)
    return redirect('account_email')


class BrandLoginView(auth_views.LoginView):
    template_name = 'accounts/brand_login.html'
    authentication_form = BrandAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy(brand_account_home(self.request.user))


@ratelimit(group='brand_register', key='ip', rate='5/h', method='POST')
@require_http_methods(['GET', 'POST'])
def brand_register(request):
    if request.user.is_authenticated:
        return redirect(brand_account_home(request.user))
    form = BrandManagerRegisterForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        user = save_new_account(form)
        if user is not None:
            return open_new_account(
            request, user,
            'Заявка создана. Подтвердите почту: без этого кабинет не откроется, даже когда администратор одобрит заявку.',
        )
    return render(request, 'accounts/brand_register.html', {'form': form})


@require_GET
def brand_pending(request):
    if not request.user.is_authenticated:
        return redirect(f'{reverse_lazy("brand_login")}?next={request.path}')
    blocked = redirect_unverified_manager(request)
    if blocked:
        return blocked
    manager = get_brand_manager(request.user, approved_only=False)
    if manager and manager.is_approved:
        return redirect(brand_account_home(request.user))
    if manager is None:
        if request.user.is_superuser:
            return redirect('dashboard')
        raise PermissionDenied
    return render(request, 'accounts/brand_pending.html', {'manager': manager})


@require_http_methods(['GET', 'POST'])
def brand_create(request):
    if not request.user.is_authenticated:
        return redirect(f'{reverse_lazy("brand_login")}?next={request.path}')
    blocked = redirect_unverified_manager(request)
    if blocked:
        return blocked
    manager = get_brand_manager(request.user)
    if manager is None:
        raise PermissionDenied
    if manager.brand_id:
        return redirect(brand_account_home(request.user))
    form = BrandCreateForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked_manager = BrandManager.objects.select_for_update().get(pk=manager.pk)
            if locked_manager.brand_id:
                return redirect('brand_deleted' if locked_manager.brand.is_deleted else 'brand_dashboard')
            brand = form.save()
            locked_manager.brand = brand
            locked_manager.save(update_fields=['brand'])
        messages.success(request, f'Бренд «{brand.name}» создан. Теперь можно добавить товары.')
        return redirect('brand_dashboard')
    return render(request, 'accounts/brand_create.html', {'form': form})


@require_GET
def brand_deleted(request):
    if not request.user.is_authenticated:
        return redirect(f'{reverse_lazy("brand_login")}?next={request.path}')
    manager = get_brand_manager(request.user, approved_only=False)
    if manager is None or manager.brand is None or not manager.brand.is_deleted:
        return redirect(brand_account_home(request.user))
    return render(request, 'accounts/brand_deleted.html', {'manager': manager})


@brand_manager_required
@require_http_methods(['GET', 'POST'])
def brand_edit(request):
    brand = request.brand_manager.brand
    form = BrandProfileForm(request.POST if request.method == 'POST' else None, instance=brand)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Настройки бренда сохранены. Название по-прежнему меняет администратор.')
        return redirect('brand_dashboard')
    return render(request, 'shop/brand_edit.html', {'brand': brand, 'form': form})


@brand_manager_required
@require_http_methods(['GET', 'POST'])
def brand_delete(request):
    brand = request.brand_manager.brand
    if brand.deletion_requested_at:
        messages.info(request, f'Заявка на удаление «{brand.name}» уже ждёт администратора. Бренд по-прежнему виден покупателям.')
        return redirect('brand_dashboard')
    form = BrandDeleteForm(request.POST if request.method == 'POST' else None, user=request.user, brand=brand)
    if request.method == 'POST' and form.is_valid():
        brand.deletion_requested_at = timezone.now()
        brand.deletion_requested_by = request.user
        brand.save(update_fields=['deletion_requested_at', 'deletion_requested_by'])
        messages.success(request, f'Заявка на удаление «{brand.name}» отправлена. Бренд останется на витрине, пока администратор её не подтвердит.')
        return redirect('brand_dashboard')
    return render(request, 'shop/brand_delete.html', {'brand': brand, 'form': form})


def _uploaded_photos(request):
    photos = request.FILES.getlist('product_photos')
    if photos:
        return photos
    return request.FILES.getlist('gallery_images')


def _check_photos(files):
    for uploaded in files:
        try:
            validate_product_image(uploaded)
        except ValidationError as exc:
            raise ValidationError(f'«{uploaded.name}»: {exc.message}') from exc


def save_product(request, product=None):
    manager = getattr(request, 'brand_manager', None)
    form = ProductForm(
        request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None,
        instance=product,
        managed_brand=manager.brand if manager else None,
    )
    size_form = ProductSizeForm(
        request.POST if request.method == 'POST' else None,
        product=product,
    )
    if request.method == 'POST':
        photos = _uploaded_photos(request)
        posted_cover = request.FILES.get('cover')
        has_cover = bool(product and product.cover)
        clearing_cover = bool(has_cover and request.POST.get('cover-clear') and not posted_cover)
        cover_from_batch = None
        gallery_files = list(photos)
        if photos and not posted_cover and (not has_cover or clearing_cover):
            cover_from_batch = photos[0]
            gallery_files = photos[1:]
        delete_ids = request.POST.getlist('delete_images') if product else []
        current_count = product.images.count() if product else 0
        to_delete = product.images.filter(pk__in=delete_ids).count() if product else 0
        will_have_cover = bool(posted_cover or cover_from_batch or (has_cover and not clearing_cover))
        gallery_after = current_count - to_delete + len(gallery_files)
        try:
            if gallery_after + (1 if will_have_cover else 0) > PHOTO_MAX_COUNT:
                raise ValidationError(f'На одной вещи можно сохранить до {PHOTO_MAX_COUNT} фотографий.')
            _check_photos(([cover_from_batch] if cover_from_batch else []) + gallery_files)
        except ValidationError as exc:
            form.add_error(None, exc)
        if form.is_valid() and size_form.is_valid():
            with transaction.atomic():
                instance = form.save(commit=False)
                if cover_from_batch is not None:
                    instance.cover = cover_from_batch
                if manager:
                    instance.brand = manager.brand
                if instance.is_deleted:
                    instance.is_published = False
                    instance.is_available = False
                    instance.is_featured = False
                instance.save()
                size_form.save(instance)
                last_order = instance.images.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0
                for offset, image in enumerate(gallery_files, start=1):
                    ProductImage.objects.create(product=instance, image=image, sort_order=last_order + offset)
                if product:
                    instance.images.filter(pk__in=delete_ids).delete()
            messages.success(request, 'Товар сохранён.')
            return redirect('product_detail', slug=instance.slug) if instance.is_published else redirect('product_edit', slug=instance.slug)
    return render(request, 'shop/product_form.html', {
        'form': form, 'size_form': size_form, 'product': product,
        'managed_brand': manager.brand if manager else None,
        'preorder_cap': MAX_OPEN_PREORDER_PER_VARIANT,
    })


@product_permission_required('shop.add_product')
@require_http_methods(['GET', 'POST'])
def product_create(request):
    return save_product(request)


@product_permission_required('shop.change_product')
@require_http_methods(['GET', 'POST'])
def product_edit(request, slug):
    product = get_scoped_object_or_404(request, Product.objects.prefetch_related('images', 'variants'), slug=slug)
    return save_product(request, product)


@brand_manager_required
@require_GET
def brand_products(request):
    products = Product.objects.filter(brand=request.brand_manager.brand).order_by('is_deleted', '-created_at').prefetch_related('variants')
    return render(request, 'shop/dashboard_products.html', {
        'products': products,
        'brand_cabinet': True,
        'can_change_products': True,
        'can_delete_products': True,
    })
