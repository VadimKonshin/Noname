import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0016_product_carriers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='brandmessage',
            name='reply_body',
            field=models.TextField(blank=True, verbose_name='Ответ'),
        ),
        migrations.AddField(
            model_name='brandmessage',
            name='replied_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Ответ отправлен'),
        ),
        migrations.AddField(
            model_name='brandmessage',
            name='replied_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='brand_replies',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Кто ответил',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_point_code',
            field=models.CharField(blank=True, max_length=64, verbose_name='Код пункта выдачи'),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_point_address',
            field=models.CharField(blank=True, max_length=250, verbose_name='Адрес пункта выдачи'),
        ),
    ]
