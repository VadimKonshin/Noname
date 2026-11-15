from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html

from .commerce import update_order_status
from .forms import StoreUserChangeForm
from .commerce_forms import OrderStatusForm
from .models import Brand, BrandManager, BrandMessage, DeliveryMethod, Order, OrderItem, Product, ProductImage, ProductVariant, UserProfile


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.action(description='Скрыть и удалить из каталога (сохранить историю)')
def archive_products(modeladmin, request, queryset):
    count = 0
    for product in queryset.filter(is_deleted=False):
        product.soft_delete()
        count += 1
    modeladmin.message_user(request, f'Удалено из каталога: {count}', messages.SUCCESS)


class DeletionRequestFilter(admin.SimpleListFilter):
    title = 'заявка на удаление'
    parameter_name = 'deletion_request'

    def lookups(self, request, model_admin):
        return (('yes', 'Ждёт подтверждения'),)

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(is_deleted=False, deletion_requested_at__isnull=False)
        return queryset


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'is_deleted', 'deletion_requested_at', 'sort_order', 'slug')
    list_editable = ('is_active', 'sort_order')
    list_filter = ('is_active', 'is_deleted', DeletionRequestFilter)
    search_fields = ('name', 'description')
    readonly_fields = ('slug', 'deleted_at', 'is_deleted', 'deletion_requested_at', 'deletion_requested_by')
    actions = ('confirm_brand_deletion', 'dismiss_brand_deletion')

    @admin.action(description='Подтвердить удаление выбранных брендов')
    def confirm_brand_deletion(self, request, queryset):
        count = 0
        for brand in queryset.filter(is_deleted=False, deletion_requested_at__isnull=False):
            brand.soft_delete()
            count += 1
        if count:
            self.message_user(request, f'Брендов скрыто: {count}. Заказы сохранены.', messages.SUCCESS)
        else:
            self.message_user(request, 'Среди выбранных нет заявки на удаление. Витрина не изменена.', messages.WARNING)

    @admin.action(description='Отклонить заявку на удаление')
    def dismiss_brand_deletion(self, request, queryset):
        count = queryset.filter(is_deleted=False, deletion_requested_at__isnull=False).update(
            deletion_requested_at=None, deletion_requested_by=None,
        )
        self.message_user(request, f'Заявок отклонено: {count}.', messages.SUCCESS)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BrandMessage)
class BrandMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'brand', 'sender_name', 'sender_email', 'product', 'read_at')
    list_filter = ('brand',)
    search_fields = ('sender_name', 'sender_email', 'body', 'brand__name')
    readonly_fields = (
        'brand', 'product', 'user', 'sender_name', 'sender_email', 'body', 'created_at', 'read_at',
        'reply_body', 'replied_at', 'replied_by',
    )
    list_select_related = ('brand', 'product')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BrandManager)
class BrandManagerAdmin(admin.ModelAdmin):
    list_display = ('account_type', 'user', 'user_email', 'brand_label', 'is_approved', 'email_verified', 'telegram_on', 'created_at')
    list_editable = ('is_approved',)
    list_filter = ('is_approved', 'brand')
    search_fields = ('user__username', 'user__email', 'brand__name')
    readonly_fields = ('created_at', 'telegram_chat_id', 'telegram_bind_expires')
    exclude = ('telegram_bind_hash',)
    list_select_related = ('user__profile', 'brand')

    @admin.display(boolean=True, description='Почта подтверждена')
    def email_verified(self, obj):
        try:
            return obj.user.profile.email_verified_at is not None
        except UserProfile.DoesNotExist:
            return True

    @admin.display(description='Тип аккаунта')
    def account_type(self, obj):
        return 'Менеджер'

    @admin.display(description='E-mail')
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description='Бренд')
    def brand_label(self, obj):
        return obj.brand or 'Ещё не создан'

    @admin.display(description='Telegram', boolean=True)
    def telegram_on(self, obj):
        return bool(obj.telegram_chat_id)


class ManagerAccountFilter(admin.SimpleListFilter):
    title = 'регистрация менеджера'
    parameter_name = 'manager_account'

    def lookups(self, request, model_admin):
        return (('yes', 'Менеджеры'), ('no', 'Остальные пользователи'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(brand_manager__isnull=False)
        if self.value() == 'no':
            return queryset.filter(brand_manager__isnull=True)
        return queryset


admin.site.unregister(User)


@admin.register(User)
class StoreUserAdmin(UserAdmin):
    form = StoreUserChangeForm
    list_display = (*UserAdmin.list_display, 'manager_account', 'email_verified')
    list_filter = (*UserAdmin.list_filter, ManagerAccountFilter)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('profile')

    @admin.display(boolean=True, description='Менеджер')
    def manager_account(self, obj):
        return BrandManager.objects.filter(user=obj).exists()

    @admin.display(boolean=True, description='Почта подтверждена')
    def email_verified(self, obj):
        try:
            return obj.profile.email_verified_at is not None
        except UserProfile.DoesNotExist:
            return True


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('title', 'brand', 'price', 'is_deleted', 'is_published', 'is_available', 'allow_preorder', 'is_featured', 'created_at')
    list_filter = ('brand', 'is_deleted', 'is_published', 'is_available', 'allow_preorder', 'is_featured')
    search_fields = ('title', 'description')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'deleted_at', 'is_deleted')
    actions = [archive_products]
    inlines = [ProductVariantInline, ProductImageInline]

    def has_delete_permission(self, request, obj=None):
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
    list_display = ('name', 'price', 'eta', 'is_active', 'requires_address', 'sort_order')
    list_editable = ('price', 'is_active', 'requires_address', 'sort_order')
    list_filter = ('is_active', 'requires_address')
    search_fields = ('name', 'description', 'pickup_address')
    fields = ('name', 'code', 'description', 'eta', 'price', 'requires_address', 'pickup_address', 'is_active', 'sort_order')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ('brand_name', 'product_title', 'size', 'quantity', 'unit_price', 'line_total', 'is_preorder', 'preorder_days')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'full_name', 'phone', 'delivery_name', 'total', 'status', 'open_store_order')
    list_filter = ('status', 'created_at', 'delivery_method', 'items__is_preorder')
    search_fields = ('full_name', 'email', 'phone')
    list_select_related = ('user', 'delivery_method')
    readonly_fields = (
        'user', 'checkout_token', 'full_name', 'email', 'phone', 'city', 'address',
        'postal_code', 'comment', 'delivery_method', 'delivery_name', 'delivery_eta', 'pickup_address',
        'delivery_point_code', 'delivery_point_address', 'delivery_price',
        'subtotal', 'total', 'created_at', 'updated_at',
    )
    fields = ('status', 'created_at', 'updated_at', 'full_name', 'phone', 'user', 'delivery_method', 'delivery_name', 'delivery_eta', 'pickup_address', 'delivery_point_code', 'delivery_point_address', 'city', 'address', 'postal_code', 'comment', 'subtotal', 'delivery_price', 'total', 'checkout_token')
    inlines = [OrderItemInline]
    form = OrderStatusForm

    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data:
            update_order_status(obj.pk, obj.status)
        else:
            super().save_model(request, obj, form, change)

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
