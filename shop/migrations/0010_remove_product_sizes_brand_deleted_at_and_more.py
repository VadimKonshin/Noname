import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def create_variants(apps, schema_editor):
    Product = apps.get_model('shop', 'Product')
    ProductVariant = apps.get_model('shop', 'ProductVariant')
    for product in Product.objects.all().iterator():
        sizes = []
        for part in product.sizes.split(','):
            size = part.strip()
            if size and size not in sizes:
                sizes.append(size)
        ProductVariant.objects.bulk_create([
            ProductVariant(product_id=product.pk, size=size, stock_quantity=0)
            for size in sizes
        ])


def restore_sizes(apps, schema_editor):
    Product = apps.get_model('shop', 'Product')
    for product in Product.objects.prefetch_related('variants').iterator():
        product.sizes = ', '.join(variant.size for variant in product.variants.all())
        product.save(update_fields=['sizes'])


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0009_brandmanager'),
    ]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Дата удаления'),
        ),
        migrations.AddField(
            model_name='brand',
            name='is_deleted',
            field=models.BooleanField(default=False, verbose_name='Удалён менеджером'),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='is_preorder',
            field=models.BooleanField(default=False, verbose_name='Предзаказ'),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='preorder_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Срок изготовления на момент заказа'),
        ),
        migrations.AddField(
            model_name='product',
            name='allow_preorder',
            field=models.BooleanField(default=False, verbose_name='Разрешить предзаказ'),
        ),
        migrations.AddField(
            model_name='product',
            name='preorder_days',
            field=models.PositiveSmallIntegerField(default=14, validators=[django.core.validators.MinValueValidator(1)], verbose_name='Срок изготовления, дней'),
        ),
        migrations.AlterField(
            model_name='brandmanager',
            name='brand',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='managers', to='shop.brand', verbose_name='Бренд'),
        ),
        migrations.AlterField(
            model_name='product',
            name='is_available',
            field=models.BooleanField(default=True, verbose_name='Доступен к заказу'),
        ),
        migrations.CreateModel(
            name='ProductVariant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('size', models.CharField(max_length=40, verbose_name='Размер')),
                ('stock_quantity', models.PositiveSmallIntegerField(default=0, verbose_name='Остаток')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='variants', to='shop.product', verbose_name='Товар')),
            ],
            options={
                'verbose_name': 'Размер и остаток',
                'verbose_name_plural': 'Размеры и остатки',
                'ordering': ['size', 'pk'],
                'constraints': [models.UniqueConstraint(fields=('product', 'size'), name='unique_product_size')],
            },
        ),
        migrations.RunPython(create_variants, restore_sizes),
        migrations.RemoveField(
            model_name='product',
            name='sizes',
        ),
    ]
