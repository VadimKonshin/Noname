from decimal import Decimal

from django.db import migrations, models


def show_carriers_hide_pickup(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    DeliveryMethod.objects.filter(name__in=['Доставка по городу', 'Доставка в другой город']).update(is_active=False)
    DeliveryMethod.objects.filter(name='Самовывоз из мастерской').update(code='pickup', is_active=False)
    carriers = [
        {
            'code': 'cdek',
            'name': 'СДЭК',
            'description': 'Фиксированный тариф магазина. Это не расчёт СДЭК по API.',
            'eta': '2–5 дней',
            'price': Decimal('350.00'),
            'requires_address': True,
            'sort_order': 1,
        },
        {
            'code': 'post',
            'name': 'Почта России',
            'description': 'Фиксированный тариф магазина. Это не расчёт Почты России по API.',
            'eta': '4–10 дней',
            'price': Decimal('390.00'),
            'requires_address': True,
            'sort_order': 2,
        },
    ]
    for carrier in carriers:
        DeliveryMethod.objects.get_or_create(
            code=carrier['code'],
            is_active=True,
            defaults={**carrier},
        )


class Migration(migrations.Migration):
    dependencies = [('shop', '0015_unique_account_email')]

    operations = [
        migrations.AddField(
            model_name='product',
            name='offers_cdek',
            field=models.BooleanField(default=True, verbose_name='СДЭК'),
        ),
        migrations.AddField(
            model_name='product',
            name='offers_post',
            field=models.BooleanField(default=True, verbose_name='Почта России'),
        ),
        migrations.AddField(
            model_name='deliverymethod',
            name='code',
            field=models.CharField(
                blank=True,
                choices=[('', 'Другой'), ('cdek', 'СДЭК'), ('post', 'Почта России'), ('pickup', 'Самовывоз')],
                default='',
                max_length=16,
                verbose_name='Код способа',
            ),
        ),
        migrations.RunPython(show_carriers_hide_pickup, migrations.RunPython.noop),
    ]
