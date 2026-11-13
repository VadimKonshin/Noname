from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404

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


def brand_manager_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
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
            if request.user.has_perm(permission):
                request.brand_manager = None
            else:
                manager = get_brand_manager(request.user)
                if manager is None:
                    raise PermissionDenied
                request.brand_manager = manager
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
