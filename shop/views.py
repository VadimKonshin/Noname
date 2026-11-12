from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .forms import ProductForm, RegisterForm
from .models import Brand, Product, ProductImage


GALLERY_MAX_COUNT = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def home(request):
    featured = list(Product.objects.filter(is_published=True, is_deleted=False, is_featured=True).select_related('brand').prefetch_related('images')[:4])
    if not featured:
        featured = list(Product.objects.filter(is_published=True, is_deleted=False).select_related('brand').prefetch_related('images')[:4])
    return render(request, 'shop/home.html', {'featured': featured})


def catalog(request):
    products = Product.objects.filter(is_published=True, is_deleted=False).select_related('brand').prefetch_related('images')
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
    return render(request, 'shop/catalog.html', {'page': page, 'query': query, 'sort': sort, 'total': page.paginator.count, 'selected_brand': selected_brand, 'brands': Brand.objects.filter(is_active=True).order_by('sort_order', 'name')})


def product_detail(request, slug):
    product = get_object_or_404(Product.objects.select_related('brand').prefetch_related('images'), slug=slug, is_published=True, is_deleted=False)
    return render(request, 'shop/product_detail.html', {'product': product})


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


def validate_gallery(files, max_new=GALLERY_MAX_COUNT):
    if len(files) > max_new:
        raise ValidationError(f'Можно добавить ещё не более {max_new} фотографий (всего максимум {GALLERY_MAX_COUNT}).')
    for uploaded in files:
        if uploaded.size > MAX_IMAGE_BYTES:
            raise ValidationError(f'Файл «{uploaded.name}» превышает 8 МБ.')
        try:
            forms.ImageField().clean(uploaded)
        except forms.ValidationError as exc:
            raise ValidationError(f'«{uploaded.name}»: загрузите корректный JPEG, PNG или WebP.') from exc
        # ImageField validation reads from the file; reset before saving it.
        uploaded.seek(0)
        if (getattr(uploaded, 'image', None) and uploaded.image.format not in ('JPEG', 'PNG', 'WEBP')) or uploaded.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
            raise ValidationError(f'«{uploaded.name}»: поддерживаются только JPG, PNG и WebP.')


def staff_only(request):
    if not request.user.is_staff:
        raise PermissionDenied('Добавление и редактирование товаров доступно только сотрудникам магазина.')


def save_product(request, product=None):
    form = ProductForm(
        request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None,
        instance=product,
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
        if form.is_valid():
            instance = form.save(commit=False)
            if instance.is_deleted:
                instance.is_published = False
                instance.is_available = False
                instance.is_featured = False
            instance.save()
            for image in gallery:
                ProductImage.objects.create(product=instance, image=image)
            if product:
                deleted_ids = request.POST.getlist('delete_images')
                instance.images.filter(pk__in=deleted_ids).delete()
            messages.success(request, 'Товар сохранён.')
            return redirect('product_detail', slug=instance.slug) if instance.is_published else redirect('product_edit', slug=instance.slug)
    return render(request, 'shop/product_form.html', {'form': form, 'product': product, 'max_gallery': GALLERY_MAX_COUNT})


@login_required
@require_http_methods(['GET', 'POST'])
def product_create(request):
    staff_only(request)
    return save_product(request)


@login_required
@require_http_methods(['GET', 'POST'])
def product_edit(request, slug):
    staff_only(request)
    product = get_object_or_404(Product.objects.prefetch_related('images'), slug=slug)
    return save_product(request, product)
