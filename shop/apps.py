from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'
    verbose_name = 'Магазин'

    def ready(self):
        from django.db.models.signals import pre_delete

        from .images import delete_stored_cards
        from .models import Product, ProductImage

        pre_delete.connect(delete_stored_cards, sender=Product, dispatch_uid='shop.product.cards')
        pre_delete.connect(delete_stored_cards, sender=ProductImage, dispatch_uid='shop.productimage.cards')
