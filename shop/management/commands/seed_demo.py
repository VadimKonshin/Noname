"""Add four obvious demo T-shirts and matching sample pictures (idempotent)."""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from shop.models import Brand, Product, ProductVariant


SAMPLES = [
    {
        'title': 'ECLIPSE / Oversized Tee',
        'description': 'Демонстрационный товар. Чёрная футболка свободного кроя с графическим изображением затмения. Замените описание, цену и фотографии на данные вашего изделия.',
        'price': '3990.00', 'color': 'Чёрный', 'filename': 'eclipse.jpg',
    },
    {
        'title': 'UNIO / Geometry Tee',
        'description': 'Демонстрационный товар. Геометрия пересечений и минималистичная графика. Для реальных продаж заполните состав ткани, посадку, размерную сетку и уход.',
        'price': '4490.00', 'color': 'Графит', 'filename': 'unio.jpg',
    },
    {
        'title': 'RED THREAD / Oversized Tee',
        'description': 'Демонстрационный товар. Вертикальная линия красного цвета как лаконичный графический акцент. Это не производственный макет принта.',
        'price': '4290.00', 'color': 'Чёрный', 'filename': 'thread.jpg',
    },
    {
        'title': 'SIGNAL / Graphic Tee',
        'description': 'Демонстрационный товар. Контрастный символ на плотной футболке в эстетике современного стритвира. Все изображения в демо — иллюстративные мокапы.',
        'price': '3790.00', 'color': 'Чёрный', 'filename': 'signal.jpg',
    },
]


class Command(BaseCommand):
    help = 'Создать 4 демонстрационных футболки с иллюстративными фото.'

    def handle(self, *args, **options):
        brand, _ = Brand.objects.get_or_create(name='ПУСТОТА', defaults={'slug': 'pustota', 'description': 'Независимый бренд авторских принтов.'})
        created_count = 0
        for sample in SAMPLES:
            obj, created = Product.objects.get_or_create(
                title=sample['title'],
                defaults={
                    'brand': brand,
                    'description': sample['description'],
                    'price': sample['price'],
                    'color': sample['color'],
                    'is_featured': True,
                },
            )
            if not created:
                continue
            picture = settings.BASE_DIR / 'static' / 'img' / 'demo' / sample['filename']
            obj.cover.save(sample['filename'], ContentFile(picture.read_bytes()), save=True)
            ProductVariant.objects.bulk_create([
                ProductVariant(product=obj, size=size, stock_quantity=5)
                for size in ('S', 'M', 'L', 'XL')
            ])
            created_count += 1
        self.stdout.write(self.style.SUCCESS(f'Готово. Создано товаров: {created_count}.'))
