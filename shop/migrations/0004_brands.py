from django.db import migrations, models
import django.db.models.deletion


def assign_existing(apps, schema_editor):
    Brand = apps.get_model('shop', 'Brand')
    Product = apps.get_model('shop', 'Product')
    if Product.objects.filter(brand__isnull=True).exists():
        brand, _ = Brand.objects.get_or_create(name='ПУСТОТА', defaults={
            'slug': 'pustota', 'description': 'Независимый бренд одежды и авторских принтов.',
        })
        Product.objects.filter(brand__isnull=True).update(brand=brand)


class Migration(migrations.Migration):
    dependencies = [('shop', '0003_delivery_examples')]
    operations = [
        migrations.CreateModel(name='Brand', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=120, unique=True, verbose_name='Название бренда')),
            ('slug', models.SlugField(allow_unicode=True, blank=True, max_length=150, unique=True, verbose_name='URL бренда')),
            ('description', models.TextField(blank=True, verbose_name='О бренде')),
            ('is_active', models.BooleanField(default=True, verbose_name='Показывать бренд')),
            ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок в бегущей строке')),
        ], options={'verbose_name': 'Бренд', 'verbose_name_plural': 'Бренды', 'ordering': ['sort_order', 'name']}),
        migrations.AddField(model_name='product', name='brand', field=models.ForeignKey(
            to='shop.brand', on_delete=django.db.models.deletion.PROTECT, related_name='products',
            verbose_name='Бренд', null=True, blank=True,
        )),
        migrations.RunPython(assign_existing, migrations.RunPython.noop),
    ]
