from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.urls import reverse
from django.utils.text import slugify
from .validators import validate_product_image


class Brand(models.Model):
    name = models.CharField('Название бренда', max_length=120, unique=True)
    slug = models.SlugField('URL бренда', max_length=150, unique=True, blank=True, allow_unicode=True)
    description = models.TextField('О бренде', blank=True)
    is_active = models.BooleanField('Показывать бренд', default=True)
    sort_order = models.PositiveSmallIntegerField('Порядок в бегущей строке', default=0)
    is_deleted = models.BooleanField('Удалён менеджером', default=False)
    deleted_at = models.DateTimeField('Дата удаления', null=True, blank=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'

    def __str__(self):
        return self.name

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
    created_at = models.DateTimeField('Заявка создана', auto_now_add=True)

    class Meta:
        ordering = ['brand__sort_order', 'brand__name', 'user__username']
        verbose_name = 'Менеджер бренда'
        verbose_name_plural = 'Менеджеры брендов'

    def __str__(self):
        brand_name = self.brand.name if self.brand else 'бренд ещё не создан'
        return f'{self.user.username} — {brand_name}'


SIZE_ORDER = ('XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL')


class Product(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name='products', verbose_name='Бренд', null=True, blank=True)
    title = models.CharField('Название', max_length=160)
    slug = models.SlugField('Адрес товара', max_length=180, unique=True, blank=True, allow_unicode=True)
    description = models.TextField('Описание')
    price = models.DecimalField('Цена, ₽', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    cover = models.ImageField('Главное фото', upload_to='products/covers/', blank=True, validators=[validate_product_image])
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
        variants = {variant.size: variant for variant in self.variants.all()}
        ladder = list(SIZE_ORDER)
        for size in variants:
            if size not in ladder:
                ladder.append(size)
        options = []
        for size in ladder:
            variant = variants.get(size)
            in_stock = bool(variant and variant.stock_quantity > 0)
            is_preorder = bool(variant and not in_stock and self.allow_preorder)
            options.append({
                'name': size,
                'available': bool(self.is_available and not self.is_deleted and (in_stock or is_preorder)),
                'in_stock': in_stock,
                'is_preorder': is_preorder,
                'stock_quantity': variant.stock_quantity if variant else 0,
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
    image = models.ImageField('Фото', upload_to='products/gallery/', validators=[validate_product_image])
    alt_text = models.CharField('Описание фото', max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        ordering = ['sort_order', 'pk']
        verbose_name = 'Фото товара'
        verbose_name_plural = 'Фотографии товара'

    def __str__(self):
        return f'{self.product.title} — фото {self.pk}'


class DeliveryMethod(models.Model):
    name = models.CharField('Название доставки', max_length=100)
    description = models.CharField('Условия', max_length=220, blank=True)
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


class Order(models.Model):
    NEW = 'new'
    CONFIRMED = 'confirmed'
    PROCESSING = 'processing'
    SHIPPED = 'shipped'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (NEW, 'Новый'), (CONFIRMED, 'Подтверждён'),
        (PROCESSING, 'Собирается'), (SHIPPED, 'Отправлен'),
        (COMPLETED, 'Завершён'), (CANCELLED, 'Отменён'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='shop_orders', verbose_name='Покупатель')
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
    delivery_price = models.DecimalField('Доставка, ₽', max_digits=10, decimal_places=2)
    subtotal = models.DecimalField('Товары, ₽', max_digits=12, decimal_places=2)
    total = models.DecimalField('Итого, ₽', max_digits=12, decimal_places=2)
    status = models.CharField('Статус', max_length=16, choices=STATUS_CHOICES, default=NEW)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'

    def __str__(self):
        return f'Заказ №{self.pk or "—"}'

    def get_absolute_url(self):
        return reverse('order_detail', kwargs={'pk': self.pk})


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

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'

    def __str__(self):
        return f'{self.product_title} / {self.size} × {self.quantity}'
