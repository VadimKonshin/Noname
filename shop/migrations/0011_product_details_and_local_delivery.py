from decimal import Decimal

from django.db import migrations, models


def replace_demo_delivery(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    demo_names = [
        'СДЭК — пункт выдачи (демо)',
        'Почта России (демо)',
        'СДЭК — курьер (демо)',
    ]
    DeliveryMethod.objects.filter(name__in=demo_names).update(is_active=False)
    DeliveryMethod.objects.filter(description__startswith='Демо').update(is_active=False)
    zones = [
        {
            'name': 'Самовывоз из мастерской',
            'description': 'Фиксированный самовывоз магазина. Адрес ниже можно заменить в админке.',
            'eta': 'в согласованный день',
            'pickup_address': 'Адрес мастерской указывается в админке, в способе доставки',
            'price': Decimal('0.00'),
            'requires_address': False,
            'sort_order': 1,
        },
        {
            'name': 'Доставка по городу',
            'description': 'Фиксированный тариф магазина внутри города. Это не расчёт перевозчика.',
            'eta': '1–3 дня',
            'pickup_address': '',
            'price': Decimal('350.00'),
            'requires_address': True,
            'sort_order': 2,
        },
        {
            'name': 'Доставка в другой город',
            'description': 'Фиксированный тариф магазина за пределы города. Это не расчёт перевозчика.',
            'eta': '3–8 дней',
            'pickup_address': '',
            'price': Decimal('590.00'),
            'requires_address': True,
            'sort_order': 3,
        },
    ]
    for zone in zones:
        DeliveryMethod.objects.get_or_create(name=zone['name'], defaults={**zone, 'is_active': True})


class Migration(migrations.Migration):
    dependencies = [('shop', '0010_remove_product_sizes_brand_deleted_at_and_more')]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='size_chart',
            field=models.TextField(blank=True, verbose_name='Таблица размеров'),
        ),
        migrations.AddField(
            model_name='product',
            name='care',
            field=models.TextField(blank=True, verbose_name='Уход'),
        ),
        migrations.AddField(
            model_name='product',
            name='composition',
            field=models.CharField(blank=True, max_length=200, verbose_name='Состав'),
        ),
        migrations.AddField(
            model_name='product',
            name='fit',
            field=models.CharField(blank=True, max_length=120, verbose_name='Посадка'),
        ),
        migrations.AddField(
            model_name='deliverymethod',
            name='eta',
            field=models.CharField(blank=True, max_length=80, verbose_name='Срок'),
        ),
        migrations.AddField(
            model_name='deliverymethod',
            name='pickup_address',
            field=models.CharField(blank=True, max_length=250, verbose_name='Адрес самовывоза'),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_eta',
            field=models.CharField(blank=True, max_length=80, verbose_name='Срок на момент заказа'),
        ),
        migrations.AddField(
            model_name='order',
            name='pickup_address',
            field=models.CharField(blank=True, max_length=250, verbose_name='Адрес самовывоза на момент заказа'),
        ),
        migrations.RunPython(replace_demo_delivery, migrations.RunPython.noop),
    ]
