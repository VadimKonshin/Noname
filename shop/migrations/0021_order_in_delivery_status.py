from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0020_public_number_and_message_telegram'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'Новый'),
                    ('confirmed', 'Подтверждён'),
                    ('processing', 'Собирается'),
                    ('in_delivery', 'В службе доставки'),
                    ('shipped', 'Отправлен'),
                    ('completed', 'Завершён'),
                    ('cancelled', 'Отменён'),
                ],
                default='new',
                max_length=16,
                verbose_name='Статус',
            ),
        ),
    ]
