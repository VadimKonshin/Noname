"""Store product photos at the size the page actually shows.

A new upload is turned upright, stripped of camera metadata, limited to the
product-page size and saved as WebP. Lists, the cart and gallery thumbnails use
a smaller sibling when the stored photo is larger than a catalog cell.
"""
import io
import posixpath
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models.fields.files import ImageFieldFile
from django.db.models import ImageField
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 8_000
DISPLAY_MAX_SIDE = 1_600
CARD_MAX_SIDE = 720
DISPLAY_QUALITY = 82
CARD_QUALITY = 76
ALLOWED_IMAGE_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
}


def card_storage_name(name):
    stem, _ext = posixpath.splitext(name)
    return f'{stem}.card.webp'


def listing_url(field_file):
    """URL of the catalog-sized sibling, or the stored photo when it is already small."""
    name = getattr(field_file, 'name', None)
    if not name:
        return ''
    storage = field_file.storage
    card_name = card_storage_name(name)
    if storage.exists(card_name):
        return storage.url(card_name)
    return field_file.url


def delete_card(storage, name):
    if not storage or not name:
        return
    card_name = card_storage_name(name)
    if storage.exists(card_name):
        storage.delete(card_name)


def delete_stored_cards(sender, instance, **kwargs):
    for field_name in ('cover', 'image'):
        field_file = getattr(instance, field_name, None)
        name = getattr(field_file, 'name', None)
        storage = getattr(field_file, 'storage', None)
        if name and storage:
            delete_card(storage, name)


def prepare_upload(uploaded):
    """Replace an upload with a display-sized WebP. Raises ValidationError when it is not a photo."""
    if getattr(uploaded, '_noname_prepared', False):
        return
    size = _byte_size(uploaded)
    if size > MAX_IMAGE_BYTES:
        raise ValidationError('Размер файла не должен превышать 8 МБ.')
    content_type = getattr(uploaded, 'content_type', None)
    if content_type and content_type not in ALLOWED_IMAGE_TYPES.values():
        raise ValidationError('Поддерживаются только JPG, PNG и WebP.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            uploaded.seek(0)
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
            working = _fit(_normalize(image), DISPLAY_MAX_SIDE)
            payload = _encode_webp(working, DISPLAY_QUALITY)
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError('Загрузите корректное изображение JPG, PNG или WebP.') from exc
    finally:
        uploaded.seek(0)
    _replace_upload(uploaded, payload)
    uploaded._noname_prepared = True


def write_card(field_file):
    """Write the catalog sibling, or remove it when the stored photo is already small enough."""
    name = getattr(field_file, 'name', None)
    if not name:
        return
    storage = field_file.storage
    with storage.open(name, 'rb') as source:
        with Image.open(source) as image:
            image.load()
            if max(image.size) <= CARD_MAX_SIDE:
                delete_card(storage, name)
                return
            fitted = image.copy()
    fitted.thumbnail((CARD_MAX_SIDE, CARD_MAX_SIDE), Image.Resampling.LANCZOS)
    card_name = card_storage_name(name)
    # A replaced photo can share this sibling name; drop the stale file before saving.
    if storage.exists(card_name):
        storage.delete(card_name)
    storage.save(card_name, ContentFile(_encode_webp(fitted, CARD_QUALITY)))


def optimize_stored(field_file):
    """Bring one stored photo in line with new uploads. Returns what changed."""
    name = getattr(field_file, 'name', None)
    if not name:
        return 'skipped'
    storage = field_file.storage
    if not storage.exists(name):
        return 'missing'
    with storage.open(name, 'rb') as source:
        with Image.open(source) as image:
            image.load()
            too_big = max(image.size) > DISPLAY_MAX_SIDE
            not_webp = image.format != 'WEBP'
            needs_card = max(image.size) > CARD_MAX_SIDE and not storage.exists(card_storage_name(name))
    if not too_big and not not_webp and not needs_card:
        return 'skipped'
    if not too_big and not not_webp:
        write_card(field_file)
        return 'card'
    with storage.open(name, 'rb') as source:
        raw = source.read()
    upload = ContentFile(raw, name=posixpath.basename(name))
    field_file.save(upload.name, upload, save=True)
    if field_file.name != name and storage.exists(name):
        storage.delete(name)
    return 'rewritten'


class PreparedImageFieldFile(ImageFieldFile):
    def save(self, name, content, save=True):
        if not getattr(content, 'name', None):
            content.name = name
        previous = self._previous_stored_name()
        prepare_upload(content)
        super().save(posixpath.basename(content.name or name), content, save=save)
        # Drop the old sibling before writing the new one: both names can share a stem.
        if previous and previous != self.name:
            delete_card(self.storage, previous)
        write_card(self)

    def delete(self, save=True):
        if not self:
            return
        name = self.name
        storage = self.storage
        super().delete(save=save)
        delete_card(storage, name)

    def _previous_stored_name(self):
        instance = self.instance
        if not getattr(instance, 'pk', None):
            return ''
        return type(instance).objects.filter(pk=instance.pk).values_list(self.field.attname, flat=True).first() or ''


class PreparedImageField(ImageField):
    attr_class = PreparedImageFieldFile

    def deconstruct(self):
        # The column stays an ImageField. Preparation happens in Python, so migrations do not change.
        name, _path, args, kwargs = super().deconstruct()
        return name, 'django.db.models.ImageField', args, kwargs


def _byte_size(uploaded):
    size = getattr(uploaded, 'size', None)
    if isinstance(size, int):
        return size
    position = uploaded.tell()
    uploaded.seek(0, io.SEEK_END)
    size = uploaded.tell()
    uploaded.seek(position)
    return size


def _normalize(image):
    image = ImageOps.exif_transpose(image)
    if image.mode == 'P':
        image = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
    elif image.mode in ('LA', 'PA'):
        image = image.convert('RGBA')
    elif image.mode != 'RGB' and image.mode != 'RGBA':
        image = image.convert('RGB')
    if image.mode == 'RGBA' and image.getchannel('A').getextrema() == (255, 255):
        image = image.convert('RGB')
    return image


def _fit(image, max_side):
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def _encode_webp(image, quality):
    cleaned = io.BytesIO()
    image.save(cleaned, format='WEBP', quality=quality, method=6)
    return cleaned.getvalue()


def _replace_upload(uploaded, payload):
    original_name = getattr(uploaded, 'name', None) or 'image'
    stem = posixpath.basename(original_name).rsplit('.', 1)[0] or 'image'
    uploaded.file = io.BytesIO(payload)
    uploaded.size = len(payload)
    uploaded.name = f'{stem}.webp'
    if hasattr(uploaded, 'content_type'):
        uploaded.content_type = 'image/webp'
    uploaded.seek(0)
