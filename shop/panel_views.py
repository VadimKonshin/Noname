"""Store console for the superuser. Same records as Django admin, in the site's own pages."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .commerce_views import PANEL_CODENAMES
from .models import Brand, BrandManager, BrandMessage, DeliveryMethod, UserProfile
from .panel_forms import PanelBrandForm, PanelDeliveryForm, PanelManagerForm, PanelUserForm


User = get_user_model()


def superuser_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def _clip(request, key, limit=80):
    return (request.GET.get(key) or '').strip()[:limit]


def _panel_permissions():
    return list(Permission.objects.filter(content_type__app_label='shop', codename__in=PANEL_CODENAMES))


def _sync_staff_permissions(user):
    permissions = _panel_permissions()
    if user.is_superuser or not permissions:
        return
    if user.is_staff:
        user.user_permissions.add(*permissions)
    else:
        user.user_permissions.remove(*permissions)


def _save_user(form):
    data = form.cleaned_data
    user = form.instance or User()
    user.username = data['username']
    user.email = data['email']
    user.first_name = data['first_name']
    user.last_name = data['last_name']
    user.is_active = bool(data['is_active'])
    user.is_staff = bool(data['is_staff'])
    user.is_superuser = bool(data['is_superuser'])
    if data['password']:
        user.set_password(data['password'])
    try:
        with transaction.atomic():
            user.save()
            profile, _created = UserProfile.objects.get_or_create(user=user)
            if data['email_verified']:
                if profile.email_verified_at is None:
                    profile.email_verified_at = timezone.now()
            else:
                profile.email_verified_at = None
            profile.pending_email = ''
            profile.save(update_fields=['email_verified_at', 'pending_email'])
            _sync_staff_permissions(user)
    except IntegrityError as exc:
        if 'email' not in str(exc).lower():
            raise
        form.add_error('email', 'Этот e-mail уже зарегистрирован.')
        return None
    return user


@superuser_required
@require_http_methods(['GET'])
def brands(request):
    rows = Brand.objects.order_by('sort_order', 'name')
    state = _clip(request, 'state', 20)
    if state == 'active':
        rows = rows.filter(is_active=True, is_deleted=False)
    elif state == 'hidden':
        rows = rows.filter(Q(is_active=False) | Q(is_deleted=True))
    elif state == 'deletion':
        rows = rows.filter(is_deleted=False, deletion_requested_at__isnull=False)
    else:
        state = ''
    query = _clip(request, 'q')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(slug__icontains=query) | Q(description__icontains=query))
    return render(request, 'shop/panel_brands.html', {
        'page': Paginator(rows, 20).get_page(request.GET.get('page')),
        'q': query,
        'state': state,
    })


@superuser_required
@require_http_methods(['GET', 'POST'])
def brand_edit(request, pk=None):
    brand = get_object_or_404(Brand, pk=pk) if pk else None
    form = PanelBrandForm(request.POST or None, instance=brand)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Бренд «{saved.name}» сохранён.')
        return redirect('panel_brand_edit', pk=saved.pk)
    return render(request, 'shop/panel_brand_form.html', {'form': form, 'brand': brand})


@superuser_required
@require_POST
def brand_confirm_deletion(request, pk):
    brand = get_object_or_404(Brand, pk=pk)
    if brand.is_deleted or brand.deletion_requested_at is None:
        messages.warning(request, 'Заявки на удаление нет. Витрина не изменена.')
    else:
        brand.soft_delete()
        messages.success(request, f'«{brand.name}» скрыт. Заказы сохранены.')
    return redirect('panel_brands')


@superuser_required
@require_POST
def brand_dismiss_deletion(request, pk):
    brand = get_object_or_404(Brand, pk=pk)
    updated = Brand.objects.filter(pk=brand.pk, is_deleted=False, deletion_requested_at__isnull=False).update(
        deletion_requested_at=None, deletion_requested_by=None,
    )
    if updated:
        messages.success(request, f'Заявка на удаление «{brand.name}» отклонена.')
    else:
        messages.warning(request, 'Отклонять нечего: заявки нет.')
    return redirect('panel_brands')


@superuser_required
@require_http_methods(['GET'])
def users(request):
    rows = User.objects.select_related('profile', 'brand_manager__brand').order_by('-date_joined')
    role = _clip(request, 'role', 20)
    if role == 'staff':
        rows = rows.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif role == 'managers':
        rows = rows.filter(brand_manager__isnull=False)
    elif role == 'customers':
        rows = rows.filter(is_staff=False, is_superuser=False, brand_manager__isnull=True)
    else:
        role = ''
    query = _clip(request, 'q')
    if query:
        rows = rows.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    for person in page.object_list:
        try:
            person.email_verified = person.profile.email_verified_at is not None
        except UserProfile.DoesNotExist:
            person.email_verified = True
    return render(request, 'shop/panel_users.html', {'page': page, 'q': query, 'role': role})


@superuser_required
@require_http_methods(['GET', 'POST'])
def user_edit(request, pk=None):
    person = get_object_or_404(User.objects.select_related('profile', 'brand_manager__brand'), pk=pk) if pk else None
    form = PanelUserForm(request.POST or None, instance=person, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        saved = _save_user(form)
        if saved is not None:
            messages.success(request, f'Аккаунт «{saved.username}» сохранён.')
            return redirect('panel_user_edit', pk=saved.pk)
    manager = getattr(person, 'brand_manager', None) if person else None
    return render(request, 'shop/panel_user_form.html', {'form': form, 'person': person, 'manager': manager})


@superuser_required
@require_http_methods(['GET', 'POST'])
def user_delete(request, pk):
    person = get_object_or_404(User, pk=pk)
    if person.pk == request.user.pk:
        messages.error(request, 'Свой аккаунт отсюда не удалить.')
        return redirect('panel_users')
    if person.is_superuser and not User.objects.filter(is_superuser=True).exclude(pk=person.pk).exists():
        messages.error(request, 'Это последний суперпользователь. Сначала создайте другого.')
        return redirect('panel_users')
    if request.method == 'POST':
        name = person.username
        person.delete()
        messages.success(request, f'Аккаунт «{name}» удалён. Бренды и заказы остались.')
        return redirect('panel_users')
    return render(request, 'shop/panel_user_delete.html', {'person': person})


@superuser_required
@require_http_methods(['GET'])
def managers(request):
    rows = BrandManager.objects.select_related('user__profile', 'brand').order_by('is_approved', '-created_at')
    query = _clip(request, 'q')
    if query:
        rows = rows.filter(
            Q(user__username__icontains=query) | Q(user__email__icontains=query) | Q(brand__name__icontains=query),
        )
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    for row in page.object_list:
        try:
            row.email_verified = row.user.profile.email_verified_at is not None
        except UserProfile.DoesNotExist:
            row.email_verified = False
    return render(request, 'shop/panel_managers.html', {'page': page, 'q': query})


@superuser_required
@require_http_methods(['GET', 'POST'])
def manager_edit(request, pk=None):
    manager = get_object_or_404(BrandManager.objects.select_related('user', 'brand'), pk=pk) if pk else None
    form = PanelManagerForm(request.POST or None, instance=manager)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        row = manager or BrandManager(user=data['user'])
        row.brand = data['brand']
        row.is_approved = bool(data['is_approved'])
        row.save()
        account = row.user
        if not account.is_superuser and 'is_staff' in data:
            grant = bool(data['is_staff'])
            if account.is_staff != grant:
                account.is_staff = grant
                account.save(update_fields=['is_staff'])
            _sync_staff_permissions(account)
        messages.success(request, f'Менеджер «{row.user.username}» сохранён.')
        return redirect('panel_manager_edit', pk=row.pk)
    return render(request, 'shop/panel_manager_form.html', {'form': form, 'manager': manager})


@superuser_required
@require_http_methods(['GET'])
def messages_list(request):
    rows = BrandMessage.objects.select_related('brand', 'product').order_by('-created_at')
    query = _clip(request, 'q')
    if query:
        rows = rows.filter(
            Q(sender_name__icontains=query) | Q(sender_email__icontains=query)
            | Q(sender_telegram__icontains=query) | Q(body__icontains=query) | Q(brand__name__icontains=query),
        )
    return render(request, 'shop/panel_messages.html', {
        'page': Paginator(rows, 20).get_page(request.GET.get('page')),
        'q': query,
    })


@superuser_required
@require_http_methods(['GET'])
def message_detail(request, pk):
    note = get_object_or_404(BrandMessage.objects.select_related('brand', 'product', 'user', 'replied_by'), pk=pk)
    return render(request, 'shop/panel_message.html', {'note': note})


@superuser_required
@require_http_methods(['GET'])
def delivery_methods(request):
    rows = DeliveryMethod.objects.order_by('sort_order', 'pk')
    query = _clip(request, 'q')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(code__icontains=query))
    return render(request, 'shop/panel_delivery.html', {
        'page': Paginator(rows, 20).get_page(request.GET.get('page')),
        'q': query,
    })


@superuser_required
@require_http_methods(['GET', 'POST'])
def delivery_edit(request, pk=None):
    method = get_object_or_404(DeliveryMethod, pk=pk) if pk else None
    form = PanelDeliveryForm(request.POST or None, instance=method)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        messages.success(request, f'Способ «{saved.name}» сохранён.')
        return redirect('panel_delivery_edit', pk=saved.pk)
    return render(request, 'shop/panel_delivery_form.html', {'form': form, 'method': method})


@superuser_required
@require_http_methods(['GET', 'POST'])
def delivery_delete(request, pk):
    method = get_object_or_404(DeliveryMethod, pk=pk)
    if request.method == 'POST':
        name = method.name
        method.delete()
        messages.success(request, f'«{name}» убран из способов доставки. Уже оформленные заказы сохранили свою цену и название.')
        return redirect('panel_delivery')
    return render(request, 'shop/panel_delivery_delete.html', {'method': method})
