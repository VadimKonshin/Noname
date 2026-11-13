from .access import get_brand_manager
from .commerce import cart_count
from .models import Brand


def store_context(request):
    brands = Brand.objects.filter(is_active=True, products__is_published=True,
                                  products__is_deleted=False).distinct().order_by('sort_order', 'name')
    application = get_brand_manager(request.user, approved_only=False)
    return {
        'cart_count': cart_count(request),
        'ticker_brands': brands,
        'brand_manager': application if application and application.is_approved else None,
        'brand_manager_application': application,
    }
