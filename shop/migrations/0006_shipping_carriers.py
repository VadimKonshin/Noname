from decimal import Decimal
from django.db import migrations


def carrier_examples(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    # Keep original demonstration rows for reference, but deactivate untouched generic options.
    # Do not alter existing customized methods/prices or old orders.
    for name, default_price in [('Доставка в пункт выдачи', Decimal('350.00')),
                                ('Курьерская доставка', Decimal('650.00'))]:
        for method in DeliveryMethod.objects.filter(name=name):
            if method.description.startswith('Демо-тариф.') and method.price == default_price:
                method.is_active = False
                method.save(update_fields=['is_active'])
    for name, description, price, requires_address, position in [
        ('СДЭК — пункт выдачи (демо)', 'Пример тарифа, без подключения к API СДЭК.', '350.00', True, 2),
        ('Почта России (демо)', 'Пример тарифа, без подключения к API Почты России.', '390.00', True, 3),
        ('СДЭК — курьер (демо)', 'Пример тарифа, без подключения к API СДЭК.', '650.00', True, 4),
    ]:
        DeliveryMethod.objects.get_or_create(name=name, defaults={
            'description': description, 'price': price, 'requires_address': requires_address,
            'sort_order': position, 'is_active': True,
        })


class Migration(migrations.Migration):
    dependencies = [('shop', '0005_order_brand_snapshot')]
    operations = [migrations.RunPython(carrier_examples, migrations.RunPython.noop)]
