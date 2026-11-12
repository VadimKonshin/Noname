from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='product', name='is_deleted',
            field=models.BooleanField(default=False, verbose_name='Удалён из магазина'),
        ),
        migrations.AddField(
            model_name='product', name='deleted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Дата удаления'),
        ),
        migrations.CreateModel(
            name='DeliveryMethod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Название доставки')),
                ('description', models.CharField(blank=True, max_length=220, verbose_name='Условия')),
                ('price', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='Цена доставки, ₽')),
                ('requires_address', models.BooleanField(default=True, verbose_name='Нужен адрес получателя')),
                ('is_active', models.BooleanField(default=True, verbose_name='Доступен покупателям')),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')),
            ],
            options={'verbose_name': 'Способ доставки', 'verbose_name_plural': 'Способы доставки', 'ordering': ['sort_order', 'pk']},
        ),
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('checkout_token', models.CharField(editable=False, max_length=80, unique=True, verbose_name='Ключ оформления')),
                ('full_name', models.CharField(max_length=150, verbose_name='Получатель')),
                ('email', models.EmailField(max_length=254, verbose_name='E-mail')),
                ('phone', models.CharField(max_length=40, verbose_name='Телефон')),
                ('city', models.CharField(blank=True, max_length=120, verbose_name='Город')),
                ('address', models.CharField(blank=True, max_length=250, verbose_name='Адрес доставки')),
                ('postal_code', models.CharField(blank=True, max_length=30, verbose_name='Индекс')),
                ('comment', models.TextField(blank=True, verbose_name='Комментарий покупателя')),
                ('delivery_name', models.CharField(max_length=100, verbose_name='Способ доставки на момент заказа')),
                ('delivery_price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Доставка, ₽')),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Товары, ₽')),
                ('total', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Итого, ₽')),
                ('status', models.CharField(choices=[('new', 'Новый'), ('confirmed', 'Подтверждён'), ('processing', 'Собирается'), ('shipped', 'Отправлен'), ('completed', 'Завершён'), ('cancelled', 'Отменён')], default='new', max_length=16, verbose_name='Статус')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменён')),
                ('delivery_method', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='shop.deliverymethod', verbose_name='Способ доставки')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shop_orders', to=settings.AUTH_USER_MODEL, verbose_name='Покупатель')),
            ],
            options={'verbose_name': 'Заказ', 'verbose_name_plural': 'Заказы', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_title', models.CharField(max_length=160, verbose_name='Название на момент заказа')),
                ('size', models.CharField(max_length=40, verbose_name='Размер')),
                ('quantity', models.PositiveSmallIntegerField(verbose_name='Количество')),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Цена за штуку, ₽')),
                ('line_total', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Сумма, ₽')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='shop.order', verbose_name='Заказ')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_items', to='shop.product', verbose_name='Модель')),
            ],
            options={'verbose_name': 'Позиция заказа', 'verbose_name_plural': 'Позиции заказа'},
        ),
    ]
