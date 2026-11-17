import secrets

from django.db import migrations, models


def fill_public_numbers(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    used = set(
        Order.objects.exclude(public_number='').values_list('public_number', flat=True)
    )
    for order in Order.objects.filter(public_number='').iterator():
        while True:
            number = f'{secrets.randbelow(90_000_000) + 10_000_000}'
            if number not in used:
                used.add(number)
                order.public_number = number
                order.save(update_fields=['public_number'])
                break


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0019_product_weight_order_track_and_reads'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='public_number',
            field=models.CharField(blank=True, default='', max_length=8, verbose_name='Номер для покупателя'),
        ),
        migrations.AddField(
            model_name='brandmessage',
            name='sender_telegram',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Telegram'),
        ),
        migrations.AlterField(
            model_name='brandmessage',
            name='sender_telegram',
            field=models.CharField(blank=True, max_length=64, verbose_name='Telegram'),
        ),
        migrations.AlterField(
            model_name='brandmessage',
            name='sender_email',
            field=models.EmailField(blank=True, max_length=254, verbose_name='Почта'),
        ),
        migrations.RunPython(fill_public_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='public_number',
            field=models.CharField(editable=False, max_length=8, unique=True, verbose_name='Номер для покупателя'),
        ),
    ]
