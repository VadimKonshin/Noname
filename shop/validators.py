import io
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


def _encode_clean(image):
    """Save a new file without the original metadata."""
    cleaned = io.BytesIO()
    if image.format == 'JPEG':
        working = image.convert('RGB') if image.mode != 'RGB' else image
        working.save(cleaned, format='JPEG', quality=88, optimize=True)
        return cleaned.getvalue(), '.jpg', 'image/jpeg'
    if image.format == 'PNG':
        working = image if image.mode in ('RGB', 'RGBA', 'P') else image.convert('RGBA')
        working.save(cleaned, format='PNG', optimize=True)
        return cleaned.getvalue(), '.png', 'image/png'
    if image.format == 'WEBP':
        working = image if image.mode in ('RGB', 'RGBA') else image.convert('RGB')
        working.save(cleaned, format='WEBP', quality=88, method=4)
        return cleaned.getvalue(), '.webp', 'image/webp'
    raise ValidationError('Поддерживаются только JPG, PNG и WebP.')


def _replace_upload(uploaded, payload, suffix, content_type):
    original_name = getattr(uploaded, 'name', 'image') or 'image'
    stem = original_name.rsplit('/', 1)[-1].rsplit('.', 1)[0] or 'image'
    uploaded.file = io.BytesIO(payload)
    uploaded.size = len(payload)
    uploaded.name = f'{stem}{suffix}'
    if hasattr(uploaded, 'content_type'):
        uploaded.content_type = content_type
    uploaded.seek(0)


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
        uploaded.seek(0)
        with Image.open(uploaded) as image:
            image.load()
            payload, suffix, cleaned_type = _encode_clean(image)
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError('Загрузите корректное изображение JPG, PNG или WebP.') from exc
    finally:
        uploaded.seek(0)
    _replace_upload(uploaded, payload, suffix, cleaned_type)
