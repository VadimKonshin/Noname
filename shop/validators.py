import warnings

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 8_000
ALLOWED_IMAGE_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
}


def validate_product_image(uploaded):
    if not isinstance(uploaded, UploadedFile):
        if getattr(uploaded, '_committed', True):
            return
        uploaded = uploaded.file
    if uploaded.size > MAX_IMAGE_BYTES:
        raise ValidationError('Размер файла не должен превышать 8 МБ.')
    content_type = getattr(uploaded, 'content_type', None)
    if content_type and content_type not in ALLOWED_IMAGE_TYPES.values():
        raise ValidationError('Поддерживаются только JPG, PNG и WebP.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(uploaded) as image:
                if image.format not in ALLOWED_IMAGE_TYPES:
                    raise ValidationError('Поддерживаются только JPG, PNG и WebP.')
                if image.width > MAX_IMAGE_SIDE or image.height > MAX_IMAGE_SIDE:
                    raise ValidationError('Ширина и высота изображения не должны превышать 8000 пикселей.')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValidationError('Изображение не должно содержать более 24 мегапикселей.')
                image.verify()
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError('Загрузите корректное изображение JPG, PNG или WebP.') from exc
    finally:
        uploaded.seek(0)
