from .commerce import cart_count
from .models import Brand


def store_context(request):
    brands = Brand.objects.filter(is_active=True, products__is_published=True,
                                  products__is_deleted=False).distinct().order_by('sort_order', 'name')
    return {'cart_count': cart_count(request), 'ticker_brands': brands}
