from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Brand, BrandManager, BrandMessage, DeliveryMethod, Order, Product, ProductVariant, UserProfile


User = get_user_model()


@override_settings(RATELIMIT_ENABLE=False)
class StorePanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            'panel_owner', email='owner@example.org', password='PassWithLength#178',
            is_staff=True, is_superuser=True,
        )
        cls.limited = User.objects.create_user('panel_limited', password='PassWithLength#178', is_staff=True)
        cls.buyer = User.objects.create_user('panel_buyer', email='buyer@example.org', password='PassWithLength#178')
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota', description='Авторские вещи')
        cls.shirt = Product.objects.create(
            brand=cls.brand, title='MOON / Черная футболка', description='Хлопок', price=Decimal('3000.00'),
        )
        ProductVariant.objects.create(product=cls.shirt, size='M', stock_quantity=4)
        cls.note = BrandMessage.objects.create(
            brand=cls.brand, sender_name='Анна', sender_email='anna@example.org', body='Есть ли размер M в чёрном?',
        )
        cls.method = DeliveryMethod.objects.create(name='СДЭК', code='cdek', price=Decimal('350.00'), eta='2–5 дней', is_active=True)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_panel_is_closed_for_everyone_except_the_superuser(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('panel_users')).status_code, 302)
        self.client.force_login(self.limited)
        for name in ('panel_users', 'panel_brands', 'panel_managers', 'panel_messages', 'panel_delivery'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.limited.user_permissions.add(Permission.objects.get(content_type__app_label='shop', codename='view_order'))
        page = self.client.get(reverse('dashboard'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse('panel_users'))
        self.client.force_login(self.staff)
        opened = self.client.get(reverse('dashboard'))
        self.assertContains(opened, reverse('panel_users'))
        self.assertContains(opened, reverse('panel_brands'))
        self.assertContains(opened, '>Менеджеры<')

    def test_superuser_creates_and_hides_a_brand_only_after_a_request(self):
        created = self.client.post(reverse('panel_brand_add'), {
            'name': 'НОЧЬ', 'description': 'Тихий бренд', 'size_chart': 'M — 96 см',
            'offers_cdek': 'on', 'is_active': 'on', 'sort_order': '4',
        })
        brand = Brand.objects.get(name='НОЧЬ')
        self.assertRedirects(created, reverse('panel_brand_edit', args=[brand.pk]))
        owned = Product.objects.create(brand=brand, title='Ночная футболка', description='Хлопок', price=Decimal('3000.00'))
        clash = self.client.post(reverse('panel_brand_add'), {
            'name': 'ночь', 'description': '', 'size_chart': '',
            'offers_courier': 'on', 'is_active': 'on', 'sort_order': '0',
        })
        self.assertEqual(clash.status_code, 200)
        self.assertContains(clash, 'похожим названием')
        self.client.post(reverse('panel_brand_confirm_deletion', args=[brand.pk]))
        brand.refresh_from_db()
        self.assertFalse(brand.is_deleted)
        brand.deletion_requested_at = timezone.now()
        brand.deletion_requested_by = self.buyer
        brand.save(update_fields=['deletion_requested_at', 'deletion_requested_by'])
        self.client.post(reverse('panel_brand_dismiss_deletion', args=[brand.pk]))
        brand.refresh_from_db()
        self.assertIsNone(brand.deletion_requested_at)
        brand.deletion_requested_at = timezone.now()
        brand.save(update_fields=['deletion_requested_at'])
        self.client.post(reverse('panel_brand_confirm_deletion', args=[brand.pk]))
        brand.refresh_from_db()
        owned.refresh_from_db()
        self.shirt.refresh_from_db()
        self.assertTrue(brand.is_deleted)
        self.assertFalse(brand.is_active)
        self.assertTrue(owned.is_deleted)
        self.assertFalse(self.shirt.is_deleted)

    def test_user_form_grants_store_rights_and_keeps_the_last_superuser(self):
        created = self.client.post(reverse('panel_user_add'), {
            'username': 'desk', 'email': 'desk@example.org', 'password': 'PassWithLength#178',
            'is_active': 'on', 'is_staff': 'on', 'email_verified': 'on',
        })
        person = User.objects.get(username='desk')
        self.assertRedirects(created, reverse('panel_user_edit', args=[person.pk]))
        self.assertTrue(person.is_staff)
        self.assertFalse(person.is_superuser)
        self.assertTrue(person.has_perm('shop.change_order'))
        self.assertTrue(person.has_perm('shop.add_product'))
        self.assertIsNotNone(person.profile.email_verified_at)
        duplicate = self.client.post(reverse('panel_user_add'), {
            'username': 'desk_two', 'email': 'Desk@example.org', 'password': 'PassWithLength#178',
            'is_active': 'on',
        })
        self.assertContains(duplicate, 'уже зарегистрирован')
        self.assertFalse(User.objects.filter(username='desk_two').exists())
        locked = self.client.post(reverse('panel_user_edit', args=[self.staff.pk]), {
            'username': self.staff.username, 'email': self.staff.email,
            'is_active': 'on', 'is_staff': 'on',
        })
        self.assertContains(locked, 'нельзя снять')
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_superuser)
        self.assertRedirects(self.client.post(reverse('panel_user_delete', args=[self.staff.pk])), reverse('panel_users'))
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())
        self.client.post(reverse('panel_user_delete', args=[person.pk]))
        self.assertFalse(User.objects.filter(pk=person.pk).exists())

    def test_manager_assignment_and_readonly_message(self):
        assigned = self.client.post(reverse('panel_manager_add'), {
            'user': str(self.buyer.pk), 'brand': str(self.brand.pk), 'is_approved': 'on', 'is_staff': 'on',
        })
        row = BrandManager.objects.get(user=self.buyer)
        self.assertRedirects(assigned, reverse('panel_manager_edit', args=[row.pk]))
        self.assertEqual(row.brand, self.brand)
        self.assertTrue(row.is_approved)
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.is_staff)
        self.assertTrue(self.buyer.has_perm('shop.view_order'))
        page = self.client.get(reverse('panel_message', args=[self.note.pk]))
        self.assertContains(page, 'Есть ли размер M')
        self.assertNotContains(page, 'Отправить ответ')
        self.assertEqual(self.client.post(reverse('panel_message', args=[self.note.pk])).status_code, 405)

    def test_delivery_price_is_edited_in_the_panel(self):
        saved = self.client.post(reverse('panel_delivery_edit', args=[self.method.pk]), {
            'name': 'СДЭК', 'code': 'cdek', 'description': 'Пункт выдачи', 'eta': '2–5 дней',
            'price': '410.00', 'requires_address': 'on', 'pickup_address': '', 'is_active': 'on', 'sort_order': '1',
        })
        self.assertRedirects(saved, reverse('panel_delivery_edit', args=[self.method.pk]))
        self.method.refresh_from_db()
        self.assertEqual(self.method.price, Decimal('410.00'))
        self.assertEqual(self.method.description, 'Пункт выдачи')

    def test_order_search_finds_the_public_number(self):
        order = Order.objects.create(
            full_name='Иван', email='ivan@example.org', phone='+79990001122',
            checkout_token='panel-order', delivery_name='СДЭК', delivery_price=Decimal('350'),
            subtotal=Decimal('3000'), total=Decimal('3350'),
        )
        page = self.client.get(reverse('dashboard_orders'), {'q': order.public_number})
        self.assertContains(page, order.public_number)
        missed = self.client.get(reverse('dashboard_orders'), {'q': 'нет-такого'})
        self.assertNotContains(missed, order.public_number)
        self.assertIsNotNone(UserProfile.objects.get(user=self.buyer))
