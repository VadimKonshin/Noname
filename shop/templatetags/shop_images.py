from django import template

from shop.images import listing_url

register = template.Library()


@register.filter
def listing_src(field_file):
    return listing_url(field_file)
