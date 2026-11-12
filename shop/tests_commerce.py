"""Run: python manage.py test shop"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Brand, DeliveryMethod, Order, OrderItem, Product

User = get_user_model()


class CommerceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user('customer', email='client@example.org', password='PassWithLength#178')
        cls.staff = User.objects.create_user('store_admin', email='admin@example.org', password='PassWithLength#178', is_staff=True)
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota')
        cls.shirt = Product.objects.create(brand=cls.brand, title='MOON / Черная футболка', description='Хлопок', price=Decimal('3000.00'), sizes='M, L', is_published=True, is_available=True)
        cls.delivery = DeliveryMethod.objects.create(name='Пункт выдачи', price=Decimal('350.00'), requires_address=True)
        cls.pickup = DeliveryMethod.objects.create(name='Самовывоз', price=Decimal('0.00'), requires_address=False)

    def _add(self, size='M', quantity=2):
        return self.client.post(reverse('cart_add', args=[self.shirt.slug]), {'size': size, 'quantity': quantity, 'go_to_cart': '1'})

    def _choose_delivery(self, method=None):
        return self.client.post(reverse('cart_delivery'), {'delivery_method': (method or self.delivery).pk, 'next': '1'})

    def _checkout_payload(self, method=None, **kwargs):
        payload = {
            'checkout_token': self.client.session['checkout_token'],
            'full_name': 'Тестовый покупатель', 'email': 'client@example.org',
            'phone': '+79991234567', 'delivery_method': (method or self.delivery).pk,
            'city': 'Москва', 'address': 'ул. Тестовая, д. 1',
            'postal_code': '123456', 'comment': 'Позвоните перед доставкой',
        }
        payload.update(kwargs)
        return payload

    def _create_order(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        result = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertEqual(result.status_code, 302)
        return Order.objects.get()

    def test_cart_guest_and_total(self):
        self.assertRedirects(self._add(), reverse('cart'))
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'СУММА ТОВАРОВ')
        self.assertEqual(self.client.session['pustota_cart'], {f'{self.shirt.pk}:M': 2})

    def test_invalid_size_not_added(self):
        self._add(size='XXXL')
        self.assertNotIn('pustota_cart', self.client.session)

    def test_invalid_quantity_not_added(self):
        self._add(quantity=10000)
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_cart_update_and_remove(self):
        self._add()
        url = reverse('cart_update')
        self.client.post(url, {'product_id': self.shirt.pk, 'size': 'M', 'quantity': 3})
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['subtotal'], Decimal('9000.00'))
        self.client.post(url, {'product_id': self.shirt.pk, 'size': 'M', 'quantity': 0})
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_cart_mutations_are_post_only(self):
        self.assertEqual(self.client.get(reverse('cart_add', args=[self.shirt.slug])).status_code, 405)
        self.assertEqual(self.client.get(reverse('cart_update')).status_code, 405)

    def test_checkout_without_delivery_redirects_to_cart(self):
        self._add()
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))
        self.assertEqual(len(self.client.session['pustota_cart']), 1)

    def test_guest_checkout_works_without_account(self):
        self._add(size='L')
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        response = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertIsNone(order.user)
        self.assertEqual(order.items.get().size, 'L')
        self.assertEqual(order.items.get().brand_name, 'ПУСТОТА')
        self.assertEqual(order.total, Decimal('6350.00'))
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)


    def test_checkout_creates_snapshot_and_clears_cart(self):
        order = self._create_order()
        self.assertEqual(order.status, Order.NEW)
        self.assertEqual(order.subtotal, Decimal('6000.00'))
        self.assertEqual(order.delivery_price, Decimal('350.00'))
        self.assertEqual(order.total, Decimal('6350.00'))
        self.assertEqual(order.items.count(), 1)
        item = order.items.get()
        self.assertEqual(item.product_title, self.shirt.title)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.line_total, Decimal('6000.00'))
        self.assertNotIn('pustota_cart', self.client.session)
        self.shirt.price = Decimal('8000.00')
        self.shirt.save()
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.total, Decimal('6350.00'))
        self.assertEqual(item.unit_price, Decimal('3000.00'))

    def test_checkout_requires_address_for_delivery(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        self.client.post(reverse('checkout'), self._checkout_payload(city='', address=''))
        self.assertEqual(Order.objects.count(), 0)

    def test_pickup_allows_no_address(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery(self.pickup)
        self.client.get(reverse('checkout'))
        self.client.post(reverse('checkout'), self._checkout_payload(method=self.pickup, city='', address=''))
        self.assertEqual(Order.objects.get().total, Decimal('6000.00'))

    def test_delivery_selection_and_prices_are_server_authoritative(self):
        self._add()
        self._choose_delivery(self.pickup)
        cart = self.client.get(reverse('cart'))
        self.assertEqual(cart.context['selected_method'].pk, self.pickup.pk)
        self.assertEqual(cart.context['grand_total'], Decimal('6000.00'))
        self.client.get(reverse('checkout'))
        payload = self._checkout_payload(delivery_method=self.delivery.pk, total='1')
        self.client.post(reverse('checkout'), payload)
        self.assertEqual(Order.objects.get().delivery_price, Decimal('0.00'))
        self.assertEqual(Order.objects.get().total, Decimal('6000.00'))

    def test_invalid_or_inactive_shipping_cannot_be_selected(self):
        self._add()
        response = self.client.post(reverse('cart_delivery'), {'delivery_method': '99999', 'next': '1'})
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn('noname_delivery_id', self.client.session)
        self.delivery.is_active = False
        self.delivery.save()
        self.client.post(reverse('cart_delivery'), {'delivery_method': self.delivery.pk, 'next': '1'})
        self.assertNotIn('noname_delivery_id', self.client.session)
        self.delivery.is_active = True
        self.delivery.save()

    def test_cannot_forge_price_or_delivery(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        self.client.post(reverse('checkout'), self._checkout_payload(price='0', subtotal='0', total='1'))
        self.assertEqual(Order.objects.get().total, Decimal('6350.00'))

    def test_double_submit_does_not_duplicate(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        data = self._checkout_payload()
        self.client.post(reverse('checkout'), data)
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))  # new session token
        self.client.post(reverse('checkout'), data)  # stale token
        self.assertEqual(Order.objects.count(), 1)

    def test_order_access_private(self):
        order = self._create_order()
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 200)
        stranger = User.objects.create_user('other', password='AnotherPass#831')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard_orders')).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 200)

    def test_staff_order_status_and_stats(self):
        order = self._create_order()
        self.client.force_login(self.staff)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, self.shirt.title)
        self.assertContains(dashboard, 'СТАТИСТИКА ПО БРЕНДАМ')
        self.assertEqual(dashboard.context['orders_total'], Decimal('6350.00'))
        self.assertEqual(self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.SHIPPED}).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SHIPPED)
        self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.CANCELLED})
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.context['orders_total'], Decimal('0.00'))
        self.assertEqual(len(dashboard.context['sold_models']), 0)

    def test_customer_cannot_change_status(self):
        order = self._create_order()
        response = self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.CANCELLED})
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.NEW)

    def test_soft_delete_retains_order_items_and_removes_cart(self):
        order = self._create_order()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('product_delete', args=[self.shirt.slug]))
        self.assertRedirects(response, reverse('dashboard_products'))
        self.shirt.refresh_from_db()
        self.assertTrue(self.shirt.is_deleted)
        self.assertEqual(self.client.get(self.shirt.get_absolute_url()).status_code, 404)
        self.assertEqual(OrderItem.objects.get(order=order).product_id, self.shirt.pk)
        self.assertEqual(self.client.get(reverse('dashboard')).context['sold_models'][0]['units'], 2)
        response = self.client.post(reverse('product_restore', args=[self.shirt.slug]))
        self.assertEqual(response.status_code, 302)
        self.shirt.refresh_from_db()
        self.assertFalse(self.shirt.is_deleted)
        self.assertFalse(self.shirt.is_published)
        self.assertFalse(self.shirt.is_available)

    def test_soft_delete_get_does_not_mutate(self):
        self.client.force_login(self.staff)
        self.client.get(reverse('product_delete', args=[self.shirt.slug]))
        self.shirt.refresh_from_db()
        self.assertFalse(self.shirt.is_deleted)
