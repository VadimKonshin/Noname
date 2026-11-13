from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit
from .access import brand_manager_required, get_brand_manager, get_scoped_object_or_404, product_permission_required
from .forms import (
    BrandAuthenticationForm, BrandCreateForm, BrandDeleteForm, BrandProfileForm,
    BrandManagerRegisterForm, ProductForm, ProductVariantFormSet, RegisterForm,
)
from .commerce import MAX_OPEN_PREORDER_PER_VARIANT
from .models import Brand, BrandManager, Product, ProductImage
from .validators import validate_product_image


GALLERY_MAX_COUNT = 8


def public_products():
    return Product.objects.public()


def info_page(request, page):
    if page not in {'delivery', 'returns', 'contacts'}:
        raise Http404
    return render(request, 'shop/info_page.html', {'page': page})


def brand_storefront(request, slug):
    brand = get_object_or_404(Brand, slug=slug, is_active=True, is_deleted=False)
    products = public_products().filter(brand=brand).select_related('brand').prefetch_related('images', 'variants')
    return render(request, 'shop/brand_storefront.html', {'brand': brand, 'products': products})


def home(request):
    featured = list(public_products().filter(is_featured=True).select_related('brand').prefetch_related('images', 'variants')[:4])
    if not featured:
        featured = list(public_products().select_related('brand').prefetch_related('images', 'variants')[:4])
    return render(request, 'shop/home.html', {'featured': featured})


def catalog(request):
    products = public_products().select_related('brand').prefetch_related('images', 'variants')
    selected_brand = request.GET.get('brand', '').strip()[:150]
    if selected_brand:
        products = products.filter(brand__slug=selected_brand, brand__is_active=True)
    query = request.GET.get('q', '').strip()[:120]
    if query:
        products = products.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(color__icontains=query) | Q(brand__name__icontains=query))
    sort = request.GET.get('sort', 'new')
    orders = {'new': '-created_at', 'price_low': 'price', 'price_high': '-price', 'name': 'title'}
    products = products.order_by(orders.get(sort, '-created_at'), '-pk')
    page = Paginator(products, 9).get_page(request.GET.get('page'))
    return render(request, 'shop/catalog.html', {'page': page, 'query': query, 'sort': sort, 'total': page.paginator.count, 'selected_brand': selected_brand, 'brands': Brand.objects.filter(is_active=True, is_deleted=False).order_by('sort_order', 'name')})


def product_detail(request, slug):
    product = get_object_or_404(public_products().select_related('brand').prefetch_related('images', 'variants'), slug=slug)
    manager = get_brand_manager(request.user)
    manages_brand = manager and product.brand_id == manager.brand_id
    return render(request, 'shop/product_detail.html', {
        'product': product,
        'can_change_product': request.user.has_perm('shop.change_product') or manages_brand,
        'can_delete_product': request.user.has_perm('shop.delete_product') or manages_brand,
    })


def ratelimited_error(request, exception=None):
    return render(request, '429.html', status=429)


@ratelimit(group='register', key='ip', rate='5/h', method='POST')
@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = RegisterForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Аккаунт создан. Добро пожаловать!')
        return redirect('home')
    return render(request, 'accounts/register.html', {'form': form})


def brand_account_home(user):
    manager = get_brand_manager(user, approved_only=False)
    if manager:
        if not manager.is_approved:
            return 'brand_pending'
        if manager.brand is None:
            return 'brand_create'
        return 'brand_deleted' if manager.brand.is_deleted else 'brand_dashboard'
    if user.is_superuser:
        return 'dashboard'
    return 'home'


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
        user = form.save()
        login(request, user)
        messages.success(request, 'Заявка менеджера создана. Доступ откроется после подтверждения администратором.')
        return redirect('brand_pending')
    return render(request, 'accounts/brand_register.html', {'form': form})


@require_GET
def brand_pending(request):
    if not request.user.is_authenticated:
        return redirect(f'{reverse_lazy("brand_login")}?next={request.path}')
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
        messages.success(request, 'Описание бренда сохранено. Название по-прежнему меняет администратор.')
        return redirect('brand_dashboard')
    return render(request, 'shop/brand_edit.html', {'brand': brand, 'form': form})


@brand_manager_required
@require_http_methods(['GET', 'POST'])
def brand_delete(request):
    brand = request.brand_manager.brand
    form = BrandDeleteForm(request.POST if request.method == 'POST' else None, user=request.user, brand=brand)
    if request.method == 'POST' and form.is_valid():
        brand.soft_delete()
        messages.success(request, f'Бренд «{brand.name}» удалён. История заказов сохранена.')
        return redirect('brand_deleted')
    return render(request, 'shop/brand_delete.html', {'brand': brand, 'form': form})


def validate_gallery(files, max_new=GALLERY_MAX_COUNT):
    if len(files) > max_new:
        raise ValidationError(f'Можно добавить ещё не более {max_new} фотографий (всего максимум {GALLERY_MAX_COUNT}).')
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
    variant_formset = ProductVariantFormSet(
        request.POST if request.method == 'POST' else None,
        instance=form.instance,
        prefix='variants',
    )
    if request.method == 'POST':
        gallery = request.FILES.getlist('gallery_images')
        current_count = product.images.count() if product else 0
        to_delete = product.images.filter(pk__in=request.POST.getlist('delete_images')).count() if product else 0
        remaining_slots = max(0, GALLERY_MAX_COUNT - current_count + to_delete)
        try:
            validate_gallery(gallery, remaining_slots)
        except ValidationError as exc:
            form.add_error(None, exc)
        if form.is_valid() and variant_formset.is_valid():
            with transaction.atomic():
                instance = form.save(commit=False)
                if manager:
                    instance.brand = manager.brand
                if instance.is_deleted:
                    instance.is_published = False
                    instance.is_available = False
                    instance.is_featured = False
                instance.save()
                variant_formset.instance = instance
                variant_formset.save()
                for image in gallery:
                    ProductImage.objects.create(product=instance, image=image)
                if product:
                    deleted_ids = request.POST.getlist('delete_images')
                    instance.images.filter(pk__in=deleted_ids).delete()
            messages.success(request, 'Товар сохранён.')
            return redirect('product_detail', slug=instance.slug) if instance.is_published else redirect('product_edit', slug=instance.slug)
    return render(request, 'shop/product_form.html', {
        'form': form, 'variant_formset': variant_formset, 'product': product, 'max_gallery': GALLERY_MAX_COUNT,
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
