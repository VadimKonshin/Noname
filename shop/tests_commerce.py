"""Run: python manage.py test shop"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.test import TestCase, override_settings
from django.urls import reverse

from .commerce import get_cart
from .models import Brand, BrandManager, DeliveryMethod, Order, OrderItem, Product, ProductVariant

User = get_user_model()


@override_settings(RATELIMIT_ENABLE=False)
class CommerceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user('customer', email='client@example.org', password='PassWithLength#178')
        cls.staff = User.objects.create_user('store_admin', email='admin@example.org', password='PassWithLength#178', is_staff=True, is_superuser=True)
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota')
        cls.shirt = Product.objects.create(brand=cls.brand, title='MOON / Черная футболка', description='Хлопок', price=Decimal('3000.00'), is_published=True, is_available=True)
        ProductVariant.objects.bulk_create([
            ProductVariant(product=cls.shirt, size='M', stock_quantity=10),
            ProductVariant(product=cls.shirt, size='L', stock_quantity=10),
        ])
        cls.delivery = DeliveryMethod.objects.create(name='Пункт выдачи', price=Decimal('350.00'), requires_address=True)
        cls.pickup = DeliveryMethod.objects.create(name='Самовывоз', price=Decimal('0.00'), requires_address=False)

    def _add(self, size='M', quantity=2, order_type='stock'):
        return self.client.post(reverse('cart_add', args=[self.shirt.slug]), {
            'size': size, 'quantity': quantity, 'order_type': order_type, 'go_to_cart': '1',
        })

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

    def test_cannot_add_more_than_stock(self):
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=1)
        self._add(quantity=2)
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_preorder_cannot_be_forced_for_stock_item(self):
        self.shirt.allow_preorder = True
        self.shirt.save(update_fields=['allow_preorder'])
        self._add(order_type='preorder')
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_preorder_requires_manager_permission(self):
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=0)
        self._add(order_type='preorder')
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_product_price_must_be_positive(self):
        self.shirt.price = Decimal('0.00')
        with self.assertRaises(ValidationError):
            self.shirt.full_clean()

    def test_non_positive_price_cannot_be_added(self):
        self.shirt.price = Decimal('0.00')
        self.shirt.save()
        response = self._add()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('pustota_cart', self.client.session)

    def test_delivery_price_cannot_be_negative(self):
        self.delivery.price = Decimal('-1.00')
        with self.assertRaises(ValidationError):
            self.delivery.full_clean()

    def test_negative_delivery_cannot_be_selected(self):
        self.delivery.price = Decimal('-350.00')
        self.delivery.save()
        self._add()
        response = self._choose_delivery()
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn('noname_delivery_id', self.client.session)

    def test_cart_lines_prefetch_brand(self):
        self._add()
        request = HttpRequest()
        request.session = self.client.session
        current = get_cart(request)
        with self.assertNumQueries(0):
            self.assertEqual(current['lines'][0]['product'].brand.name, self.brand.name)

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
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 8)

    def test_preorder_checkout_preserves_mode_and_stock(self):
        self.shirt.allow_preorder = True
        self.shirt.preorder_days = 21
        self.shirt.save(update_fields=['allow_preorder', 'preorder_days'])
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=0)
        self._add(order_type='preorder')
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        response = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertTrue(item.is_preorder)
        self.assertEqual(item.preorder_days, 21)
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 0)

    def test_stock_change_before_checkout_does_not_reduce_order_silently(self):
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=1)
        response = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertRedirects(response, reverse('cart'))
        self.assertEqual(Order.objects.count(), 0)

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
        variant = ProductVariant.objects.get(product=self.shirt, size='M')
        self.assertEqual(variant.stock_quantity, 10)
        self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.CANCELLED})
        variant.refresh_from_db()
        self.assertEqual(variant.stock_quantity, 10)
        self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.NEW})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)

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

    def test_staff_flag_alone_does_not_expose_orders(self):
        order = self._create_order()
        limited_staff = User.objects.create_user('limited_manager', password='AnotherPass#831', is_staff=True)
        self.client.force_login(limited_staff)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard_orders')).status_code, 403)
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)
        response = self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.CANCELLED})
        self.assertEqual(response.status_code, 403)


@override_settings(RATELIMIT_ENABLE=False)
class BrandOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'SecurePass813!'
        cls.customer = User.objects.create_user('brand_customer', password=cls.password)
        cls.other_customer = User.objects.create_user('other_brand_customer', password=cls.password)
        cls.brand = Brand.objects.create(name='ALPHA', slug='alpha')
        cls.other_brand = Brand.objects.create(name='BETA', slug='beta')
        cls.manager_user = User.objects.create_user('alpha_manager', password=cls.password)
        cls.other_manager_user = User.objects.create_user('beta_manager', password=cls.password)
        BrandManager.objects.create(user=cls.manager_user, brand=cls.brand, is_approved=True)
        BrandManager.objects.create(user=cls.other_manager_user, brand=cls.other_brand, is_approved=True)
        cls.shirt = Product.objects.create(brand=cls.brand, title='Alpha shirt', description='Alpha', price=Decimal('1000.00'))
        cls.second_shirt = Product.objects.create(brand=cls.brand, title='Alpha second', description='Alpha 2', price=Decimal('1500.00'))
        cls.other_shirt = Product.objects.create(brand=cls.other_brand, title='Beta shirt', description='Beta', price=Decimal('3000.00'))

        def create_order(token, status, subtotal):
            return Order.objects.create(
                user=cls.customer, checkout_token=token, full_name='Тестовый покупатель',
                email='customer@example.com', phone='+79991234567', delivery_name='Самовывоз',
                delivery_price=Decimal('0.00'), subtotal=subtotal, total=subtotal, status=status,
            )

        cls.mixed_order = create_order('mixed-token', Order.NEW, Decimal('5000.00'))
        OrderItem.objects.create(
            order=cls.mixed_order, product=cls.shirt, product_title=cls.shirt.title,
            brand_name=cls.brand.name, size='M', quantity=2,
            unit_price=Decimal('1000.00'), line_total=Decimal('2000.00'),
        )
        OrderItem.objects.create(
            order=cls.mixed_order, product=cls.other_shirt, product_title=cls.other_shirt.title,
            brand_name=cls.other_brand.name, size='L', quantity=1,
            unit_price=Decimal('3000.00'), line_total=Decimal('3000.00'),
        )
        cls.alpha_order = create_order('alpha-token', Order.COMPLETED, Decimal('1500.00'))
        OrderItem.objects.create(
            order=cls.alpha_order, product=cls.second_shirt, product_title=cls.second_shirt.title,
            brand_name=cls.brand.name, size='S', quantity=1,
            unit_price=Decimal('1500.00'), line_total=Decimal('1500.00'),
        )
        cls.cancelled_order = create_order('cancelled-token', Order.CANCELLED, Decimal('3000.00'))
        OrderItem.objects.create(
            order=cls.cancelled_order, product=cls.shirt, product_title=cls.shirt.title,
            brand_name=cls.brand.name, size='M', quantity=3,
            unit_price=Decimal('1000.00'), line_total=Decimal('3000.00'),
        )
        cls.foreign_order = Order.objects.create(
            user=cls.other_customer, checkout_token='foreign-token', full_name='Другой покупатель',
            email='other@example.com', phone='+79990000000', delivery_name='Самовывоз',
            delivery_price=Decimal('0.00'), subtotal=Decimal('3000.00'), total=Decimal('3000.00'),
        )
        OrderItem.objects.create(
            order=cls.foreign_order, product=cls.other_shirt, product_title=cls.other_shirt.title,
            brand_name=cls.other_brand.name, size='L', quantity=1,
            unit_price=Decimal('3000.00'), line_total=Decimal('3000.00'),
        )

    def test_brand_dashboard_uses_only_own_active_items(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('brand_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['orders_count'], 3)
        self.assertEqual(response.context['new_count'], 1)
        self.assertEqual(response.context['cancelled_count'], 1)
        self.assertEqual(response.context['sold_units'], 3)
        self.assertEqual(response.context['items_total'], Decimal('3500.00'))
        titles = {entry['display_title'] for entry in response.context['sold_models']}
        self.assertEqual(titles, {'Alpha shirt', 'Alpha second'})
        recent = {order.pk: order.brand_total for order in response.context['recent_orders']}
        self.assertEqual(recent[self.mixed_order.pk], Decimal('2000.00'))

    def test_brand_order_history_and_product_filter(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('brand_orders'))
        self.assertEqual(
            {order.pk for order in response.context['page']},
            {self.mixed_order.pk, self.alpha_order.pk, self.cancelled_order.pk},
        )
        response = self.client.get(reverse('brand_orders'), {'product': self.second_shirt.pk})
        self.assertEqual([order.pk for order in response.context['page']], [self.alpha_order.pk])
        response = self.client.get(reverse('brand_orders'), {'status': Order.CANCELLED})
        self.assertEqual([order.pk for order in response.context['page']], [self.cancelled_order.pk])

    def test_mixed_order_detail_hides_other_brand_items_and_total(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(self.mixed_order.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['brand_order_view'])
        self.assertEqual(response.context['brand_subtotal'], Decimal('2000.00'))
        self.assertEqual([item.product_title for item in response.context['order_items']], ['Alpha shirt'])
        self.assertContains(response, 'Alpha shirt')
        self.assertNotContains(response, 'Beta shirt')
        self.assertFalse(response.context['can_change_order'])
        self.assertEqual(self.client.post(reverse('change_order_status', args=[self.mixed_order.pk]), {
            'status': Order.SHIPPED,
        }).status_code, 403)

    def test_manager_cannot_view_order_without_own_items(self):
        self.client.force_login(self.manager_user)
        self.assertEqual(self.client.get(self.foreign_order.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard_orders')).status_code, 403)

    def test_other_manager_sees_own_part_of_mixed_order(self):
        self.client.force_login(self.other_manager_user)
        response = self.client.get(self.mixed_order.get_absolute_url())
        self.assertEqual(response.context['brand_subtotal'], Decimal('3000.00'))
        self.assertEqual([item.product_title for item in response.context['order_items']], ['Beta shirt'])
        self.assertNotContains(response, 'Alpha shirt')

    def test_customer_history_contains_only_own_orders(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('my_orders'))
        self.assertEqual(
            {order.pk for order in response.context['page']},
            {self.mixed_order.pk, self.alpha_order.pk, self.cancelled_order.pk},
        )
        self.client.force_login(self.other_customer)
        response = self.client.get(reverse('my_orders'))
        self.assertEqual([order.pk for order in response.context['page']], [self.foreign_order.pk])
        self.assertEqual(self.client.get(self.alpha_order.get_absolute_url()).status_code, 404)

    def test_customer_cannot_open_brand_cabinet(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('brand_dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('brand_orders')).status_code, 403)
