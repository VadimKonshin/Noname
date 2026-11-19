from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0021_order_in_delivery_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='cancelled',
            field=models.BooleanField(default=False, verbose_name='Отменён'),
        ),
    ]
