from .access import get_brand_manager
from .commerce import cart_count
from .models import Brand


def store_context(request):
    brands = Brand.objects.filter(is_active=True, is_deleted=False, products__is_published=True,
                                  products__is_deleted=False).distinct().order_by('sort_order', 'name')
    application = get_brand_manager(request.user, approved_only=False)
    active_manager = application if application and application.is_approved and application.brand and not application.brand.is_deleted else None
    return {
        'cart_count': cart_count(request),
        'ticker_brands': brands,
        'brand_manager': active_manager,
        'brand_manager_application': application,
    }
