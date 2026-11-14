from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0012_brand_messages'),
    ]

    operations = [
        migrations.AddField(
            model_name='brandmanager',
            name='telegram_bind_expires',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Ключ Telegram действует до'),
        ),
        migrations.AddField(
            model_name='brandmanager',
            name='telegram_bind_hash',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Ключ подключения Telegram'),
        ),
        migrations.AddField(
            model_name='brandmanager',
            name='telegram_chat_id',
            field=models.CharField(blank=True, max_length=32, null=True, unique=True, verbose_name='Telegram-чат'),
        ),
    ]
