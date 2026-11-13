from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .models import Brand, BrandManager, DeliveryMethod, Order, OrderItem, Product, ProductImage


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.action(description='Скрыть и удалить из каталога (сохранить историю)')
def archive_products(modeladmin, request, queryset):
    count = 0
    for product in queryset.filter(is_deleted=False):
        product.soft_delete()
        count += 1
    modeladmin.message_user(request, f'Удалено из каталога: {count}', messages.SUCCESS)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'sort_order', 'slug')
    list_editable = ('is_active', 'sort_order')
    search_fields = ('name', 'description')
    readonly_fields = ('slug',)


@admin.register(BrandManager)
class BrandManagerAdmin(admin.ModelAdmin):
    list_display = ('user', 'brand', 'is_approved', 'created_at')
    list_editable = ('is_approved',)
    list_filter = ('is_approved', 'brand')
    search_fields = ('user__username', 'user__email', 'brand__name')
    readonly_fields = ('created_at',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('title', 'brand', 'price', 'is_deleted', 'is_published', 'is_available', 'is_featured', 'created_at')
    list_filter = ('brand', 'is_deleted', 'is_published', 'is_available', 'is_featured')
    search_fields = ('title', 'description')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'deleted_at', 'is_deleted')
    actions = [archive_products]
    inlines = [ProductImageInline]

    def has_delete_permission(self, request, obj=None):
        # Preserve order analytics and old product references; use archive action instead.
        return False

    def save_model(self, request, obj, form, change):
        if obj.is_deleted:
            obj.is_published = False
            obj.is_available = False
            obj.is_featured = False
        super().save_model(request, obj, form, change)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'sort_order', 'alt_text')
    list_filter = ('product',)


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'is_active', 'requires_address', 'sort_order')
    list_editable = ('price', 'is_active', 'sort_order')
    list_filter = ('is_active', 'requires_address')
    search_fields = ('name',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ('brand_name', 'product_title', 'size', 'quantity', 'unit_price', 'line_total')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'full_name', 'phone', 'delivery_name', 'total', 'status', 'open_store_order')
    list_filter = ('status', 'created_at', 'delivery_method')
    search_fields = ('full_name', 'email', 'phone')
    list_select_related = ('user', 'delivery_method')
    readonly_fields = (
        'user', 'checkout_token', 'full_name', 'email', 'phone', 'city', 'address',
        'postal_code', 'comment', 'delivery_method', 'delivery_name', 'delivery_price',
        'subtotal', 'total', 'created_at', 'updated_at',
    )
    fields = ('status', 'created_at', 'updated_at', 'full_name', 'email', 'phone', 'user', 'delivery_method', 'delivery_name', 'city', 'address', 'postal_code', 'comment', 'subtotal', 'delivery_price', 'total', 'checkout_token')
    inlines = [OrderItemInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Карточка')
    def open_store_order(self, obj):
        return format_html('<a href="{}">Открыть ↗</a>', reverse('order_detail', args=[obj.pk]))


admin.site.site_header = 'NoName — управление маркетплейсом'
admin.site.site_title = 'NoName'
admin.site.index_title = 'Бренды, заказы, товары и доставка'
