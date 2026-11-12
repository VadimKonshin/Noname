# Initial migration for the clothing store.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160, verbose_name='Название')),
                ('slug', models.SlugField(allow_unicode=True, blank=True, max_length=180, unique=True, verbose_name='Адрес товара')),
                ('description', models.TextField(verbose_name='Описание')),
                ('price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Цена, ₽')),
                ('cover', models.ImageField(blank=True, upload_to='products/covers/', verbose_name='Главное фото')),
                ('sizes', models.CharField(default='S, M, L, XL', max_length=100, verbose_name='Размеры через запятую')),
                ('color', models.CharField(default='Чёрный', max_length=80, verbose_name='Цвет')),
                ('is_available', models.BooleanField(default=True, verbose_name='В наличии / доступен под заказ')),
                ('is_published', models.BooleanField(default=True, verbose_name='Показывать в каталоге')),
                ('is_featured', models.BooleanField(default=False, verbose_name='Показать на главной')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Добавлен')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменён')),
            ],
            options={'verbose_name': 'Товар', 'verbose_name_plural': 'Товары', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='products/gallery/', verbose_name='Фото')),
                ('alt_text', models.CharField(blank=True, max_length=160, verbose_name='Описание фото')),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='shop.product', verbose_name='Товар')),
            ],
            options={'verbose_name': 'Фото товара', 'verbose_name_plural': 'Фотографии товара', 'ordering': ['sort_order', 'pk']},
        ),
    ]
