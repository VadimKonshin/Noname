from django.db import migrations


def add_demo_methods(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    examples = [
        ('Самовывоз (после согласования)', 'Демо: место выдачи необходимо согласовать с магазином.', '0.00', False, 1),
        ('Доставка в пункт выдачи', 'Демо-тариф. Уточните реальную стоимость и транспортную компанию.', '350.00', True, 2),
        ('Курьерская доставка', 'Демо-тариф. Перед продажами проверьте зоны доставки и цены.', '650.00', True, 3),
    ]
    for name, description, price, requires_address, sort_order in examples:
        DeliveryMethod.objects.get_or_create(name=name, defaults={
            'description': description, 'price': price, 'requires_address': requires_address,
            'sort_order': sort_order, 'is_active': True,
        })


def remove_demo_methods(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    DeliveryMethod.objects.filter(name__in=[
        'Самовывоз (после согласования)', 'Доставка в пункт выдачи', 'Курьерская доставка',
    ]).delete()


class Migration(migrations.Migration):
    dependencies = [('shop', '0002_orders_cart_delivery')]
    operations = [migrations.RunPython(add_demo_methods, remove_demo_methods)]
