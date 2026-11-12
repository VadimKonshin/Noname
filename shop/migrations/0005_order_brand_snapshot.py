from django.db import migrations, models


def populate_brand_names(apps, schema_editor):
    OrderItem = apps.get_model('shop', 'OrderItem')
    for item in OrderItem.objects.select_related('product__brand').all().iterator():
        brand = item.product.brand if item.product_id and item.product.brand_id else None
        if brand:
            OrderItem.objects.filter(pk=item.pk).update(brand_name=brand.name)


class Migration(migrations.Migration):
    dependencies = [('shop', '0004_brands')]
    operations = [
        migrations.AddField(model_name='orderitem', name='brand_name', field=models.CharField(max_length=120, blank=True, verbose_name='Бренд на момент заказа')),
        migrations.RunPython(populate_brand_names, migrations.RunPython.noop),
    ]
