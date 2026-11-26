from decimal import Decimal

from django.db import migrations, models


def post_becomes_courier(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    found = False
    for method in DeliveryMethod.objects.filter(code='post'):
        found = True
        method.code = 'courier'
        method.name = 'Доставка курьером'
        method.description = 'До двери.'
        method.eta = '1–3 дня'
        if method.is_active:
            method.price = Decimal('650.00')
            method.requires_address = True
            method.sort_order = 2
        method.save()
    if not found and not DeliveryMethod.objects.filter(code='courier', is_active=True).exists():
        DeliveryMethod.objects.create(
            code='courier',
            name='Доставка курьером',
            description='До двери.',
            eta='1–3 дня',
            price=Decimal('650.00'),
            requires_address=True,
            is_active=True,
            sort_order=2,
        )


def courier_becomes_post(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    DeliveryMethod.objects.filter(code='courier', name='Доставка курьером').update(
        code='post',
        name='Почта России',
        description='До отделения.',
        eta='4–10 дней',
        price=Decimal('390.00'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0022_orderitem_cancelled'),
    ]

    operations = [
        migrations.RenameField(
            model_name='brand',
            old_name='offers_post',
            new_name='offers_courier',
        ),
        migrations.RenameField(
            model_name='product',
            old_name='offers_post',
            new_name='offers_courier',
        ),
        migrations.AlterField(
            model_name='brand',
            name='offers_courier',
            field=models.BooleanField(default=True, verbose_name='Курьер'),
        ),
        migrations.AlterField(
            model_name='product',
            name='offers_courier',
            field=models.BooleanField(default=True, verbose_name='Курьер'),
        ),
        migrations.AlterField(
            model_name='deliverymethod',
            name='code',
            field=models.CharField(
                blank=True,
                choices=[('', 'Другой'), ('cdek', 'СДЭК'), ('courier', 'Курьер'), ('pickup', 'Самовывоз')],
                default='',
                max_length=16,
                verbose_name='Код способа',
            ),
        ),
        migrations.RunPython(post_becomes_courier, courier_becomes_post),
    ]
