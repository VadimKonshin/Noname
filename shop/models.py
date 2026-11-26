import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse
from django.utils.text import slugify
from .brand_names import brand_name_key
from .images import PreparedImageField
from .validators import validate_product_image


class Brand(models.Model):
    name = models.CharField('Название бренда', max_length=120, unique=True)
    slug = models.SlugField('URL бренда', max_length=150, unique=True, blank=True, allow_unicode=True)
    description = models.TextField('О бренде', blank=True)
    size_chart = models.TextField('Таблица размеров', blank=True)
    offers_cdek = models.BooleanField('СДЭК', default=True)
    offers_courier = models.BooleanField('Курьер', default=True)
    offers_pickup = models.BooleanField('Самовывоз', default=False)
    pickup_address = models.CharField('Адрес самовывоза', max_length=250, blank=True)
    is_active = models.BooleanField('Показывать бренд', default=True)
    sort_order = models.PositiveSmallIntegerField('Порядок в бегущей строке', default=0)
    is_deleted = models.BooleanField('Удалён менеджером', default=False)
    deleted_at = models.DateTimeField('Дата удаления', null=True, blank=True)
    deletion_requested_at = models.DateTimeField('Заявка на удаление', null=True, blank=True)
    deletion_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Кто запросил удаление',
    )

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        key = brand_name_key(self.name)
        if not key:
            return
        clash = next((
            other.name for other in Brand.objects.exclude(pk=self.pk).only('name')
            if brand_name_key(other.name) == key
        ), None)
        if clash:
            raise ValidationError({'name': 'Бренд с таким или очень похожим названием уже есть.'})

    def size_rows(self):
        rows = []
        for raw in (self.size_chart or '').splitlines():
            line = raw.strip()
            if not line:
                continue
            label, detail = _split_size_line(line)
            rows.append({'label': label, 'detail': detail})
        return rows

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name, allow_unicode=True)[:110].strip('-') or 'brand'
            slug = base
            suffix = 2
            while Brand.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{suffix}'
                suffix += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'is_active', 'deleted_at'])
        for product in self.products.filter(is_deleted=False):
            product.soft_delete()


class BrandManager(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='brand_manager', verbose_name='Пользователь')
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name='managers', verbose_name='Бренд', null=True, blank=True)
    is_approved = models.BooleanField('Подтверждён администратором', default=False)
    telegram_chat_id = models.CharField('Telegram-чат', max_length=32, null=True, blank=True, unique=True)
    telegram_bind_hash = models.CharField('Ключ подключения Telegram', max_length=64, blank=True, default='')
    telegram_bind_expires = models.DateTimeField('Ключ Telegram действует до', null=True, blank=True)
    created_at = models.DateTimeField('Заявка создана', auto_now_add=True)

    class Meta:
        ordering = ['brand__sort_order', 'brand__name', 'user__username']
        verbose_name = 'Менеджер бренда'
        verbose_name_plural = 'Менеджеры брендов'

    def __str__(self):
        brand_name = self.brand.name if self.brand else 'бренд ещё не создан'
        return f'{self.user.username} — {brand_name}'


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile', verbose_name='Пользователь')
    email_verified_at = models.DateTimeField('Почта подтверждена', null=True, blank=True)
    pending_email = models.EmailField('Новая почта ждёт подтверждения', blank=True, default='')

    class Meta:
        verbose_name = 'Почта аккаунта'
        verbose_name_plural = 'Почты аккаунтов'

    def __str__(self):
        return self.user.get_username()


class EmailToken(models.Model):
    CONFIRM = 'confirm'
    RESET = 'reset'
    CHANGE = 'change'
    PURPOSE_CHOICES = [
        (CONFIRM, 'Подтверждение почты'),
        (RESET, 'Новый пароль'),
        (CHANGE, 'Смена почты'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_tokens', verbose_name='Пользователь')
    purpose = models.CharField('Зачем ссылка', max_length=16, choices=PURPOSE_CHOICES)
    token_hash = models.CharField('Хеш ссылки', max_length=64, unique=True)
    email = models.EmailField('Почта в ссылке')
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    expires_at = models.DateTimeField('Действует до')
    used_at = models.DateTimeField('Использована', null=True, blank=True)

    class Meta:
        verbose_name = 'Ссылка из письма'
        verbose_name_plural = 'Ссылки из писем'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id} — {self.purpose}'


SIZE_ORDER = ('XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL')


def _split_size_line(line):
    for separator in (' — ', ' – ', ' - ', ':', '—', '–'):
        if separator in line:
            label, detail = line.split(separator, 1)
            label, detail = label.strip(), detail.strip()
            if label and detail:
                return label, detail
    return line, ''


class ProductQuerySet(models.QuerySet):
    def public(self):
        return self.filter(
            Q(brand__isnull=True) | Q(brand__is_deleted=False, brand__is_active=True),
            is_published=True,
            is_deleted=False,
        )


class Product(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name='products', verbose_name='Бренд', null=True, blank=True)
    title = models.CharField('Название', max_length=160)
    slug = models.SlugField('Адрес товара', max_length=180, unique=True, blank=True, allow_unicode=True)
    description = models.TextField('Описание')
    composition = models.CharField('Состав', max_length=200, blank=True)
    fit = models.CharField('Посадка', max_length=120, blank=True)
    care = models.TextField('Уход', blank=True)
    weight_grams = models.PositiveIntegerField(
        'Вес, г', default=500, validators=[MinValueValidator(100), MaxValueValidator(30000)],
    )
    offers_cdek = models.BooleanField('СДЭК', default=True)
    offers_courier = models.BooleanField('Курьер', default=True)
    offers_pickup = models.BooleanField('Самовывоз', default=False)
    price = models.DecimalField('Цена, ₽', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    cover = PreparedImageField('Главное фото', upload_to='products/covers/', blank=True, validators=[validate_product_image])
    color = models.CharField('Цвет', max_length=80, default='Чёрный')
    is_available = models.BooleanField('Доступен к заказу', default=True)
    allow_preorder = models.BooleanField('Разрешить предзаказ', default=False)
    preorder_days = models.PositiveSmallIntegerField('Срок изготовления, дней', default=14, validators=[MinValueValidator(1)])
    is_published = models.BooleanField('Показывать в каталоге', default=True)
    is_featured = models.BooleanField('Показать на главной', default=False)
    is_deleted = models.BooleanField('Удалён из магазина', default=False)
    deleted_at = models.DateTimeField('Дата удаления', null=True, blank=True)
    created_at = models.DateTimeField('Добавлен', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title, allow_unicode=True)[:145].strip('-') or 'futbolka'
            candidate = base
            suffix = 2
            while Product.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f'{base}-{suffix}'
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.is_published = False
        self.is_available = False
        self.is_featured = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'is_published', 'is_available', 'is_featured', 'deleted_at', 'updated_at'])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])

    def get_absolute_url(self):
        return reverse('product_detail', kwargs={'slug': self.slug})

    @property
    def size_list(self):
        return [variant.size for variant in self.variants.all()]

    @property
    def total_stock(self):
        return sum(variant.stock_quantity for variant in self.variants.all())

    @property
    def all_size_options(self):
        def sort_key(variant):
            try:
                return (0, SIZE_ORDER.index(variant.size))
            except ValueError:
                return (1, variant.size.casefold())

        options = []
        for variant in sorted(self.variants.all(), key=sort_key):
            in_stock = variant.stock_quantity > 0
            is_preorder = not in_stock and self.allow_preorder
            options.append({
                'name': variant.size,
                'available': bool(self.is_available and not self.is_deleted and (in_stock or is_preorder)),
                'in_stock': in_stock,
                'is_preorder': is_preorder,
                'stock_quantity': variant.stock_quantity,
            })
        return options

    @property
    def photo(self):
        if self.cover:
            return self.cover
        first = next(iter(self.images.all()), None)
        return first.image if first else None


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants', verbose_name='Товар')
    size = models.CharField('Размер', max_length=40)
    stock_quantity = models.PositiveSmallIntegerField('Остаток', default=0)

    class Meta:
        ordering = ['size', 'pk']
        constraints = [models.UniqueConstraint(fields=('product', 'size'), name='unique_product_size')]
        verbose_name = 'Размер и остаток'
        verbose_name_plural = 'Размеры и остатки'

    def __str__(self):
        return f'{self.product.title} / {self.size}: {self.stock_quantity}'


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images', verbose_name='Товар')
    image = PreparedImageField('Фото', upload_to='products/gallery/', validators=[validate_product_image])
    alt_text = models.CharField('Описание фото', max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        ordering = ['sort_order', 'pk']
        verbose_name = 'Фото товара'
        verbose_name_plural = 'Фотографии товара'

    def __str__(self):
        return f'{self.product.title} — фото {self.pk}'


class DeliveryMethod(models.Model):
    CDEK = 'cdek'
    COURIER = 'courier'
    PICKUP = 'pickup'
    CODE_CHOICES = [
        ('', 'Другой'),
        (CDEK, 'СДЭК'),
        (COURIER, 'Курьер'),
        (PICKUP, 'Самовывоз'),
    ]
    name = models.CharField('Название доставки', max_length=100)
    code = models.CharField('Код способа', max_length=16, blank=True, default='', choices=CODE_CHOICES)
    description = models.CharField('Условия', max_length=220, blank=True)
    eta = models.CharField('Срок', max_length=80, blank=True)
    pickup_address = models.CharField('Адрес самовывоза', max_length=250, blank=True)
    price = models.DecimalField('Цена доставки, ₽', max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(Decimal('0.00'))])
    requires_address = models.BooleanField('Нужен адрес получателя', default=True)
    is_active = models.BooleanField('Доступен покупателям', default=True)
    sort_order = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        ordering = ['sort_order', 'pk']
        verbose_name = 'Способ доставки'
        verbose_name_plural = 'Способы доставки'

    def __str__(self):
        return f'{self.name} — {self.price} ₽'


def allocate_public_number(exclude_pk=None):
    """Eight digits, never the sequential row id, so a guessed «заказ 3» opens nothing."""
    qs = Order.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for _ in range(12):
        number = f'{secrets.randbelow(90_000_000) + 10_000_000}'
        if not qs.filter(public_number=number).exists():
            return number
    raise ValidationError('Не удалось выдать номер заказа. Повторите оформление.')


class Order(models.Model):
    NEW = 'new'
    CONFIRMED = 'confirmed'
    PROCESSING = 'processing'
    IN_DELIVERY = 'in_delivery'
    SHIPPED = 'shipped'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (NEW, 'Новый'), (CONFIRMED, 'Подтверждён'),
        (PROCESSING, 'Собирается'), (IN_DELIVERY, 'В службе доставки'),
        (SHIPPED, 'Отправлен'),
        (COMPLETED, 'Завершён'), (CANCELLED, 'Отменён'),
    ]
    MANAGER_STATUS_CHOICES = [
        (PROCESSING, 'Собирается'),
        (IN_DELIVERY, 'В службе доставки'),
        (SHIPPED, 'Отправлен'),
        (CANCELLED, 'Отменён'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='shop_orders', verbose_name='Покупатель')
    public_number = models.CharField('Номер для покупателя', max_length=8, unique=True, editable=False)
    checkout_token = models.CharField('Ключ оформления', max_length=80, unique=True, editable=False)
    full_name = models.CharField('Получатель', max_length=150)
    email = models.EmailField('E-mail')
    phone = models.CharField('Телефон', max_length=40)
    city = models.CharField('Город', max_length=120, blank=True)
    address = models.CharField('Адрес доставки', max_length=250, blank=True)
    postal_code = models.CharField('Индекс', max_length=30, blank=True)
    comment = models.TextField('Комментарий покупателя', blank=True)
    delivery_method = models.ForeignKey(DeliveryMethod, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Способ доставки')
    delivery_name = models.CharField('Способ доставки на момент заказа', max_length=100)
    delivery_eta = models.CharField('Срок на момент заказа', max_length=80, blank=True)
    pickup_address = models.CharField('Адрес самовывоза на момент заказа', max_length=250, blank=True)
    delivery_point_code = models.CharField('Код пункта выдачи', max_length=64, blank=True)
    delivery_point_address = models.CharField('Адрес пункта выдачи', max_length=250, blank=True)
    delivery_price = models.DecimalField('Доставка, ₽', max_digits=10, decimal_places=2)
    subtotal = models.DecimalField('Товары, ₽', max_digits=12, decimal_places=2)
    total = models.DecimalField('Итого, ₽', max_digits=12, decimal_places=2)
    status = models.CharField('Статус', max_length=16, choices=STATUS_CHOICES, default=NEW)
    tracking_number = models.CharField('Трек', max_length=40, blank=True, default='')
    buyer_telegram_chat_id = models.CharField('Telegram покупателя', max_length=32, blank=True, default='')
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'

    def save(self, *args, **kwargs):
        if not self.public_number:
            self.public_number = allocate_public_number(exclude_pk=self.pk)
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {'public_number'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Заказ №{self.public_number or "—"}'

    def get_absolute_url(self):
        return reverse('order_detail', kwargs={'pk': self.pk})


class OrderRead(models.Model):
    """Last time a manager or store admin opened someone else's order card."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='reads', verbose_name='Заказ')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='order_reads', verbose_name='Кто открыл')
    opened_at = models.DateTimeField('Открыта', auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('order', 'user'), name='unique_order_reader')]
        ordering = ['-opened_at']
        verbose_name = 'Просмотр заказа'
        verbose_name_plural = 'Просмотры заказа'

    def __str__(self):
        return f'Заказ №{self.order_id} — {self.user_id}'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items', verbose_name='Модель')
    product_title = models.CharField('Название на момент заказа', max_length=160)
    brand_name = models.CharField('Бренд на момент заказа', max_length=120, blank=True)
    size = models.CharField('Размер', max_length=40)
    quantity = models.PositiveSmallIntegerField('Количество')
    unit_price = models.DecimalField('Цена за штуку, ₽', max_digits=10, decimal_places=2)
    line_total = models.DecimalField('Сумма, ₽', max_digits=12, decimal_places=2)
    is_preorder = models.BooleanField('Предзаказ', default=False)
    preorder_days = models.PositiveSmallIntegerField('Срок изготовления на момент заказа', null=True, blank=True)
    cancelled = models.BooleanField('Отменён', default=False)

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'

    def __str__(self):
        return f'{self.product_title} / {self.size} × {self.quantity}'


class BrandMessage(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name='messages', verbose_name='Бренд')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages', verbose_name='Вещь')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='brand_messages', verbose_name='Аккаунт')
    sender_name = models.CharField('Имя', max_length=120)
    sender_email = models.EmailField('Почта', blank=True)
    sender_telegram = models.CharField('Telegram', max_length=64, blank=True)
    body = models.TextField('Сообщение')
    reply_body = models.TextField('Ответ', blank=True)
    replied_at = models.DateTimeField('Ответ отправлен', null=True, blank=True)
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='brand_replies', verbose_name='Кто ответил',
    )
    created_at = models.DateTimeField('Отправлено', auto_now_add=True)
    read_at = models.DateTimeField('Прочитано', null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Сообщение бренду'
        verbose_name_plural = 'Сообщения брендам'

    def __str__(self):
        return f'{self.sender_name} → {self.brand}'
