from decimal import Decimal

from django.db import migrations, models


def open_pickup_for_brands(apps, schema_editor):
    DeliveryMethod = apps.get_model('shop', 'DeliveryMethod')
    DeliveryMethod.objects.filter(code='cdek', description__startswith='Фиксированный').update(
        description='До пункта выдачи.',
    )
    DeliveryMethod.objects.filter(code='post', description__startswith='Фиксированный').update(
        description='До отделения.',
    )
    pickup = DeliveryMethod.objects.filter(code='pickup').order_by('pk').first()
    if pickup is None:
        DeliveryMethod.objects.create(
            code='pickup',
            name='Самовывоз из мастерской',
            description='Забрать у бренда.',
            eta='когда вещь готова',
            price=Decimal('0.00'),
            requires_address=False,
            is_active=True,
            sort_order=3,
        )
        return
    pickup.is_active = True
    pickup.requires_address = False
    pickup.price = Decimal('0.00')
    pickup.description = 'Забрать у бренда.'
    if not pickup.eta:
        pickup.eta = 'когда вещь готова'
    pickup.save(update_fields=['is_active', 'requires_address', 'price', 'description', 'eta'])


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0017_message_reply_and_cdek_point'),
    ]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='offers_cdek',
            field=models.BooleanField(default=True, verbose_name='СДЭК'),
        ),
        migrations.AddField(
            model_name='brand',
            name='offers_post',
            field=models.BooleanField(default=True, verbose_name='Почта России'),
        ),
        migrations.AddField(
            model_name='brand',
            name='offers_pickup',
            field=models.BooleanField(default=False, verbose_name='Самовывоз'),
        ),
        migrations.AddField(
            model_name='brand',
            name='pickup_address',
            field=models.CharField(blank=True, max_length=250, verbose_name='Адрес самовывоза'),
        ),
        migrations.AddField(
            model_name='product',
            name='offers_pickup',
            field=models.BooleanField(default=False, verbose_name='Самовывоз'),
        ),
        migrations.RunPython(open_pickup_for_brands, migrations.RunPython.noop),
    ]
