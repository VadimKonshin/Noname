"""Rewrite product photos that were stored before display-sized WebP."""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from PIL import UnidentifiedImageError

from shop.images import optimize_stored
from shop.models import Product, ProductImage


class Command(BaseCommand):
    help = 'Уменьшить уже сохранённые фото товаров до размера витрины и записать превью для списков.'

    def handle(self, *args, **options):
        counts = {'rewritten': 0, 'card': 0, 'skipped': 0, 'missing': 0, 'failed': 0}
        targets = []
        for product in Product.objects.exclude(cover='').iterator():
            targets.append(product.cover)
        for image in ProductImage.objects.exclude(image='').iterator():
            targets.append(image.image)
        for field_file in targets:
            label = field_file.name
            try:
                result = optimize_stored(field_file)
            except (ValidationError, UnidentifiedImageError, OSError, ValueError) as exc:
                counts['failed'] += 1
                self.stdout.write(self.style.WARNING(f'{label}: {exc}'))
                continue
            counts[result] = counts.get(result, 0) + 1
        self.stdout.write(self.style.SUCCESS(
            'Готово. Перекодировано: {rewritten}, превью: {card}, без изменений: {skipped}, '
            'нет файла: {missing}, ошибок: {failed}.'.format(**counts)
        ))
