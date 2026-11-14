from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect

from .models import BrandManager


def get_brand_manager(user, approved_only=True):
    if not user.is_authenticated:
        return None
    try:
        manager = user.brand_manager
    except BrandManager.DoesNotExist:
        return None
    if approved_only and not manager.is_approved:
        return None
    if approved_only and manager.brand and manager.brand.is_deleted:
        return None
    return manager


def brand_account_home(user):
    manager = get_brand_manager(user, approved_only=False)
    if manager:
        from .accounts import user_email_verified
        if not user_email_verified(user):
            return 'account_email'
        if not manager.is_approved:
            return 'brand_pending'
        if manager.brand is None:
            return 'brand_create'
        return 'brand_deleted' if manager.brand.is_deleted else 'brand_dashboard'
    if user.is_superuser:
        return 'dashboard'
    return 'home'


def redirect_unverified_manager(request):
    if not request.user.is_authenticated:
        return None
    if not BrandManager.objects.filter(user_id=request.user.pk).exists():
        return None
    from .accounts import user_email_verified
    if user_email_verified(request.user):
        return None
    return redirect('account_email')


def brand_manager_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        blocked = redirect_unverified_manager(request)
        if blocked:
            return blocked
        manager = get_brand_manager(request.user)
        if manager is None:
            raise PermissionDenied
        request.brand_manager = manager
        return view(request, *args, **kwargs)
    return wrapped


def product_permission_required(permission):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            blocked = redirect_unverified_manager(request)
            if blocked:
                return blocked
            manager = get_brand_manager(request.user)
            if manager is not None and not request.user.is_superuser:
                request.brand_manager = manager
            elif request.user.has_perm(permission):
                request.brand_manager = None
            else:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def scoped_products(request, queryset):
    manager = getattr(request, 'brand_manager', None)
    return queryset.filter(brand=manager.brand) if manager else queryset


def scoped_order_items(brand, queryset):
    return queryset.filter(Q(product__brand=brand) | Q(product__isnull=True, brand_name=brand.name))


def get_scoped_object_or_404(request, queryset, **kwargs):
    obj = scoped_products(request, queryset).filter(**kwargs).first()
    if obj is None:
        raise Http404('Объект не найден.')
    return obj
