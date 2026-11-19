"""Run: python manage.py test shop"""
import json
from decimal import Decimal
from unittest.mock import patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.test import TestCase, override_settings
from django.urls import reverse

from .commerce import get_cart
from .models import Brand, BrandManager, DeliveryMethod, Order, OrderItem, OrderRead, Product, ProductVariant

User = get_user_model()

CDEK_SETTINGS = {
    'CDEK_CLIENT_ID': 'shop-client',
    'CDEK_CLIENT_SECRET': 'cdek-secret-not-for-pages',
    'CDEK_FROM_POSTAL_CODE': '101000',
    'CDEK_API_URL': 'https://api.cdek.ru/v2',
    'CDEK_TARIFF_CODE': 136,
    'CDEK_ITEM_WEIGHT_GRAMS': 500,
}


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
        cls.free = DeliveryMethod.objects.create(name='Без адреса', price=Decimal('0.00'), requires_address=False)
        cls.pickup = DeliveryMethod.objects.create(
            name='Самовывоз', price=Decimal('0.00'), requires_address=False, code=DeliveryMethod.PICKUP,
        )

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

    def _other_brand_shirt(self):
        brand = Brand.objects.create(name='ВТОРОЙ', slug='vtoroy')
        shirt = Product.objects.create(
            brand=brand, title='Вторая футболка', description='Хлопок', price=Decimal('2500.00'),
            is_published=True, is_available=True,
        )
        ProductVariant.objects.create(product=shirt, size='M', stock_quantity=4)
        return shirt

    def test_cart_keeps_one_brand_and_one_delivery(self):
        self._add()
        other = self._other_brand_shirt()
        response = self.client.post(reverse('cart_add', args=[other.slug]), {
            'size': 'M', 'quantity': 1, 'order_type': 'stock',
        }, follow=True)
        self.assertContains(response, 'один бренд и одна доставка')
        self.assertEqual(response.context['cart']['count'], 2)
        self.assertTrue(response.context['single_brand'])
        self.assertContains(response, 'name="delivery_method"')
        same = Product.objects.create(
            brand=self.brand, title='Вторая вещь бренда', description='Хлопок', price=Decimal('1000.00'),
            is_published=True, is_available=True,
        )
        ProductVariant.objects.create(product=same, size='M', stock_quantity=4)
        added = self.client.post(reverse('cart_add', args=[same.slug]), {
            'size': 'M', 'quantity': 1, 'order_type': 'stock', 'go_to_cart': '1',
        }, follow=True)
        self.assertEqual(added.context['cart']['count'], 3)
        self.assertContains(added, 'name="delivery_method"')

    def test_mixed_cart_hides_delivery_until_one_brand_remains(self):
        other = self._other_brand_shirt()
        session = self.client.session
        session['pustota_cart'] = {f'{self.shirt.pk}:M': 1, f'{other.pk}:M': 1}
        session.save()
        response = self.client.get(reverse('cart'))
        self.assertFalse(response.context['single_brand'])
        self.assertContains(response, 'Удалите лишние вещи')
        self.assertNotContains(response, 'name="delivery_method"')

    def test_checkout_refuses_a_cart_that_already_mixes_brands(self):
        other = self._other_brand_shirt()
        session = self.client.session
        session['pustota_cart'] = {f'{self.shirt.pk}:M': 1, f'{other.pk}:M': 1}
        session.save()
        response = self.client.get(reverse('checkout'), follow=True)
        self.assertContains(response, 'один бренд и одна доставка')
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.client.post(reverse('cart_delivery'), {
            'delivery_method': self.delivery.pk, 'next': '1',
        }).status_code, 302)
        self.assertEqual(Order.objects.count(), 0)

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

    def test_over_stock_shows_preorder_plate_and_does_not_touch_stock(self):
        self.shirt.allow_preorder = True
        self.shirt.save(update_fields=['allow_preorder'])
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=1)
        response = self.client.post(reverse('cart_add', args=[self.shirt.slug]), {
            'size': 'M', 'quantity': 4, 'order_type': 'stock', 'go_to_cart': '1',
        }, follow=True)
        self.assertContains(response, 'data-server-offer="1"')
        self.assertContains(response, 'В наличии нет такого количества товара')
        self.assertContains(response, 'Можно оставить предзаказ на')
        self.assertContains(response, 'name="quantity" value="4"')
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 1)
        taken = self.client.post(reverse('cart_take_preorder'), {
            'product_id': self.shirt.pk, 'size': 'M', 'quantity': 4, 'next': 'product',
        }, follow=True)
        self.assertContains(taken, 'предзаказ на 4 шт')
        self.assertNotContains(taken, 'склад не списан')
        cart = self.client.get(reverse('cart')).context['cart']
        self.assertEqual(cart['count'], 4)
        self.assertTrue(cart['lines'][0]['is_preorder'])
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 1)

    def test_cart_quantity_over_stock_offers_the_same_preorder(self):
        self.shirt.allow_preorder = True
        self.shirt.save(update_fields=['allow_preorder'])
        self._add(quantity=1)
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=1)
        response = self.client.post(reverse('cart_update'), {
            'product_id': self.shirt.pk, 'size': 'M', 'quantity': 4, 'order_type': 'stock',
        }, follow=True)
        self.assertContains(response, 'data-server-offer="1"')
        self.assertContains(response, 'name="quantity" value="4"')
        self.assertEqual(response.context['cart']['count'], 1)
        self.client.post(reverse('cart_take_preorder'), {
            'product_id': self.shirt.pk, 'size': 'M', 'quantity': 4, 'next': 'cart',
        })
        cart = self.client.get(reverse('cart')).context['cart']
        self.assertEqual(cart['count'], 4)
        self.assertTrue(cart['lines'][0]['is_preorder'])
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 1)

    def test_over_stock_without_preorder_permission_stays_an_error(self):
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=1)
        response = self.client.post(reverse('cart_add', args=[self.shirt.slug]), {
            'size': 'M', 'quantity': 4, 'order_type': 'stock', 'go_to_cart': '1',
        }, follow=True)
        self.assertContains(response, 'Выбранный размер недоступен в этом количестве. Свяжитесь с продавцом для предзаказа.')
        self.assertNotContains(response, 'Вы можете оставить предзаказ.')
        self.assertNotContains(response, 'data-server-offer')
        self.assertEqual(self.client.get(reverse('cart')).context['cart']['count'], 0)

    def test_preorder_is_not_limited_by_remaining_stock(self):
        self.shirt.allow_preorder = True
        self.shirt.save(update_fields=['allow_preorder'])
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=2)
        self._add(quantity=4, order_type='preorder')
        cart = self.client.get(reverse('cart')).context['cart']
        self.assertEqual(cart['count'], 4)
        self.assertTrue(cart['lines'][0]['is_preorder'])
        self.assertEqual(ProductVariant.objects.get(product=self.shirt, size='M').stock_quantity, 2)
        self.client.post(reverse('cart_update'), {'product_id': self.shirt.pk, 'size': 'M', 'quantity': 0, 'order_type': 'preorder'})
        self._add(quantity=4, order_type='stock')
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
        self.assertRegex(order.public_number, r'^[1-9]\d{7}$')
        self.assertNotEqual(order.public_number, str(order.pk))
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

    def test_method_without_address_allows_empty_city(self):
        self.client.force_login(self.member)
        self._add()
        self._choose_delivery(self.free)
        self.client.get(reverse('checkout'))
        self.client.post(reverse('checkout'), self._checkout_payload(method=self.free, city='', address=''))
        self.assertEqual(Order.objects.get().total, Decimal('6000.00'))

    def test_pickup_appears_from_the_brand_or_from_the_product(self):
        Brand.objects.filter(pk=self.brand.pk).update(offers_pickup=True, pickup_address='двор марки, 2')
        self._add(quantity=1)
        cart = self.client.get(reverse('cart'))
        self.assertIn(self.pickup.pk, [method.pk for method in cart.context['delivery_methods']])
        self.assertContains(cart, 'двор марки, 2')
        self._choose_delivery(self.pickup)
        self.client.get(reverse('checkout'))
        result = self.client.post(reverse('checkout'), self._checkout_payload(method=self.pickup, city='', address=''))
        self.assertEqual(result.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.pickup_address, 'двор марки, 2')
        self.assertEqual(order.delivery_price, Decimal('0.00'))

        Order.objects.all().delete()
        self.client.session.flush()
        Brand.objects.filter(pk=self.brand.pk).update(offers_pickup=False, pickup_address='переулок, 1')
        Product.objects.filter(pk=self.shirt.pk).update(offers_pickup=True)
        self._add(quantity=1)
        cart = self.client.get(reverse('cart'))
        self.assertIn(self.pickup.pk, [method.pk for method in cart.context['delivery_methods']])

    def test_brand_switch_hides_a_carrier_the_product_still_offers(self):
        post = DeliveryMethod.objects.create(
            name='Почта тест', code=DeliveryMethod.POST, price=Decimal('390.00'), requires_address=True,
        )
        Brand.objects.filter(pk=self.brand.pk).update(offers_post=False)
        self._add(quantity=1)
        cart = self.client.get(reverse('cart'))
        self.assertNotIn(post.pk, [method.pk for method in cart.context['delivery_methods']])
        response = self._choose_delivery(post)
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn('noname_delivery_id', self.client.session)

    def test_pickup_cannot_be_chosen(self):
        self._add()
        cart = self.client.get(reverse('cart'))
        self.assertNotIn(self.pickup.pk, [method.pk for method in cart.context['delivery_methods']])
        response = self._choose_delivery(self.pickup)
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn('noname_delivery_id', self.client.session)

    def test_cart_hides_carrier_the_product_does_not_offer(self):
        cdek = DeliveryMethod.objects.create(
            name='СДЭК тест', code=DeliveryMethod.CDEK, price=Decimal('350.00'), requires_address=True,
        )
        self.shirt.offers_cdek = False
        self.shirt.save(update_fields=['offers_cdek'])
        self._add()
        response = self._choose_delivery(cdek)
        self.assertRedirects(response, reverse('cart'))
        self.assertNotIn('noname_delivery_id', self.client.session)
        self.shirt.offers_cdek = True
        self.shirt.offers_post = False
        self.shirt.save(update_fields=['offers_cdek', 'offers_post'])
        self._choose_delivery(cdek)
        self.assertEqual(self.client.session['noname_delivery_id'], cdek.pk)

    def test_delivery_selection_and_prices_are_server_authoritative(self):
        self._add()
        self._choose_delivery(self.free)
        cart = self.client.get(reverse('cart'))
        self.assertEqual(cart.context['selected_method'].pk, self.free.pk)
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

    def test_order_lookup_hides_contacts_and_does_not_bind_a_chat(self):
        order = self._create_order()
        order.tracking_number = 'RA123456789RU'
        order.save(update_fields=['tracking_number'])
        self.client.logout()
        missing = self.client.post(reverse('order_lookup'), {'order_id': order.public_number, 'phone': '+79990000000'})
        unknown_number = '10000000' if order.public_number != '10000000' else '10000001'
        unknown = self.client.post(reverse('order_lookup'), {'order_id': unknown_number, 'phone': '+79991234567'})
        sequential = self.client.post(reverse('order_lookup'), {'order_id': order.pk, 'phone': '+79991234567'})
        found = self.client.post(reverse('order_lookup'), {'order_id': order.public_number, 'phone': '8 999 123-45-67'})
        for page in (missing, unknown, sequential):
            self.assertNotContains(page, self.shirt.title)
            self.assertNotContains(page, 'Тестовый покупатель')
            self.assertNotContains(page, 'ул. Тестовая')
            self.assertNotContains(page, 'client@example.org')
        self.assertContains(missing, 'не найден')
        self.assertContains(unknown, 'не найден')
        self.assertNotContains(found, 'Чужой телефон')
        self.assertNotContains(found, 'здесь не показываются')
        self.assertContains(found, 'Новый')
        self.assertContains(found, self.shirt.title)
        self.assertContains(found, 'RA123456789RU')
        self.assertNotContains(found, 'Тестовый покупатель')
        self.assertNotContains(found, 'ул. Тестовая')
        self.assertNotContains(found, 'client@example.org')
        order.refresh_from_db()
        self.assertEqual(order.buyer_telegram_chat_id, '')

    def test_order_lookup_phone_budget_is_shared_across_clients(self):
        cache.clear()
        order = self._create_order()
        self.client.logout()
        with patch('shop.telegram.PHONE_STATUS_LIMIT', 1):
            first = self.client.post(reverse('order_lookup'), {'order_id': order.public_number, 'phone': '+79991234567'})
            second = self.client.post(reverse('order_lookup'), {'order_id': order.public_number, 'phone': '+7 (999) 123-45-67'})
        self.assertContains(first, self.shirt.title)
        self.assertContains(second, 'Слишком много проверок')
        self.assertNotContains(second, self.shirt.title)

    def test_tracking_number_is_saved_and_a_bad_one_is_kept_out(self):
        order = self._create_order()
        self.client.force_login(self.staff)
        self.client.post(reverse('change_order_status', args=[order.pk]), {
            'status': Order.SHIPPED, 'tracking_number': 'RA123456789RU',
        })
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SHIPPED)
        self.assertEqual(order.tracking_number, 'RA123456789RU')
        self.assertContains(self.client.get(order.get_absolute_url()), 'RA123456789RU')
        self.client.post(reverse('change_order_status', args=[order.pk]), {
            'status': Order.SHIPPED, 'tracking_number': '<script>',
        })
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, 'RA123456789RU')
        self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.COMPLETED})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETED)
        self.assertEqual(order.tracking_number, 'RA123456789RU')

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

    def test_dashboard_splits_registered_and_guest_buyers(self):
        from datetime import timedelta
        from django.utils import timezone
        old = User.objects.create_user('old_client', email='old@example.org', password='PassWithLength#178')
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=30))
        User.objects.create_user('new_client', email='new@example.org', password='PassWithLength#178')
        manager_user = User.objects.create_user('brand_person', email='brand@example.org', password='PassWithLength#178')
        BrandManager.objects.create(user=manager_user, brand=self.brand, is_approved=True)
        panel = User.objects.create_user('panel_only', password='PassWithLength#178', is_staff=True)
        panel.user_permissions.add(Permission.objects.get(content_type__app_label='shop', codename='view_order'))
        self._create_order()
        Order.objects.create(
            user=old, checkout_token='old-buyer', full_name='Старый', email='old@example.org',
            phone='+79990000001', delivery_name='Самовывоз', delivery_price=Decimal('0'),
            subtotal=Decimal('1000'), total=Decimal('1000'),
        )
        Order.objects.create(
            user=old, checkout_token='old-buyer-again', full_name='Старый', email='old@example.org',
            phone='+79990000001', delivery_name='Самовывоз', delivery_price=Decimal('0'),
            subtotal=Decimal('1000'), total=Decimal('1000'),
        )
        for token, email in (
            ('guest-a', 'Guest@Example.org'),
            ('guest-b', 'guest@example.org'),
            ('guest-c', 'other@example.org'),
        ):
            Order.objects.create(
                checkout_token=token, full_name='Гость', email=email, phone='+79990000002',
                delivery_name='Самовывоз', delivery_price=Decimal('0'),
                subtotal=Decimal('1000'), total=Decimal('1000'),
            )
        self.client.force_login(panel)
        hidden = self.client.get(reverse('dashboard'))
        self.assertIsNone(hidden.context['client_stats'])
        self.assertNotContains(hidden, 'ВСЕГО КЛИЕНТОВ')
        self.assertNotContains(hidden, 'БЕЗ РЕГИСТРАЦИИ')
        self.client.force_login(self.staff)
        page = self.client.get(reverse('dashboard'))
        stats = page.context['client_stats']
        self.assertEqual(stats['client_count'], 3)
        self.assertEqual(stats['new_clients_week'], 2)
        self.assertEqual(stats['registered_buyers'], 2)
        self.assertEqual(stats['guest_buyers'], 2)
        self.assertContains(page, 'ВСЕГО КЛИЕНТОВ')
        self.assertContains(page, 'НОВЫЕ ЗА НЕДЕЛЮ')
        self.assertContains(page, 'БЕЗ РЕГИСТРАЦИИ')

    def test_staff_can_mark_order_in_delivery(self):
        order = self._create_order()
        self.client.force_login(self.staff)
        page = self.client.get(order.get_absolute_url())
        self.assertContains(page, 'В службе доставки')
        self.client.post(reverse('change_order_status', args=[order.pk]), {
            'status': Order.IN_DELIVERY, 'tracking_number': '',
        })
        order.refresh_from_db()
        self.assertEqual(order.status, Order.IN_DELIVERY)

    def test_superuser_decides_manager_application_and_store_admin_rights(self):
        applicant = User.objects.create_user(
            'applicant_manager', email='apply@example.org', password='PassWithLength#178',
        )
        application = BrandManager.objects.create(user=applicant, is_approved=False)
        self.client.force_login(self.staff)
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'ЗАЯВКИ МЕНЕДЖЕРОВ')
        self.assertContains(page, 'applicant_manager')
        self.assertContains(page, 'Права администратора')
        self.client.post(reverse('dashboard_managers'), {
            'approved': str(application.pk),
            'staff': str(application.pk),
        })
        application.refresh_from_db()
        applicant = User.objects.get(pk=applicant.pk)
        self.assertTrue(application.is_approved)
        self.assertTrue(applicant.is_staff)
        self.assertFalse(applicant.is_superuser)
        self.assertTrue(applicant.has_perm('shop.change_order'))
        self.assertTrue(applicant.has_perm('shop.add_product'))
        self.client.post(reverse('dashboard_managers'), {})
        application.refresh_from_db()
        applicant = User.objects.get(pk=applicant.pk)
        self.assertFalse(application.is_approved)
        self.assertFalse(applicant.is_staff)
        self.assertFalse(applicant.has_perm('shop.change_order'))

    def test_store_admin_flag_is_not_granted_by_a_non_superuser(self):
        applicant = User.objects.create_user('second_applicant', password='PassWithLength#178')
        application = BrandManager.objects.create(user=applicant, is_approved=False)
        viewer = User.objects.create_user('order_viewer', password='PassWithLength#178', is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(content_type__app_label='shop', codename='view_order'))
        self.client.force_login(viewer)
        page = self.client.get(reverse('dashboard'))
        self.assertNotContains(page, 'ЗАЯВКИ МЕНЕДЖЕРОВ')
        self.assertEqual(self.client.post(reverse('dashboard_managers'), {
            'approved': str(application.pk), 'staff': str(application.pk),
        }).status_code, 403)
        application.refresh_from_db()
        applicant.refresh_from_db()
        self.assertFalse(application.is_approved)
        self.assertFalse(applicant.is_staff)

    def test_manager_widget_does_not_change_a_superuser(self):
        own = BrandManager.objects.create(user=self.staff, is_approved=True)
        self.client.force_login(self.staff)
        self.client.post(reverse('dashboard_managers'), {'approved': str(own.pk)})
        self.staff.refresh_from_db()
        own.refresh_from_db()
        self.assertTrue(own.is_approved)
        self.assertTrue(self.staff.is_superuser)
        self.assertTrue(self.staff.is_staff)

    def test_cancel_does_not_recreate_deleted_variant(self):
        order = self._create_order()
        ProductVariant.objects.filter(product=self.shirt, size='M').delete()
        self.client.force_login(self.staff)
        self.client.post(reverse('change_order_status', args=[order.pk]), {'status': Order.CANCELLED})
        self.assertFalse(ProductVariant.objects.filter(product=self.shirt, size='M').exists())

    def test_preorder_quantity_is_capped_across_orders(self):
        self.shirt.allow_preorder = True
        self.shirt.save(update_fields=['allow_preorder'])
        ProductVariant.objects.filter(product=self.shirt, size='M').update(stock_quantity=0)
        self._add(quantity=6, order_type='preorder')
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        result = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertEqual(result.status_code, 302)
        self._add(quantity=5, order_type='preorder')
        self.assertEqual(get_cart(self.client)['count'], 0)
        self._add(quantity=4, order_type='preorder')
        self.assertEqual(get_cart(self.client)['count'], 4)

    def test_checkout_snapshots_delivery_window_and_pickup(self):
        self.delivery.eta = 'в согласованный день'
        self.delivery.pickup_address = 'ул. Мастерская, 1'
        self.delivery.save(update_fields=['eta', 'pickup_address'])
        self._add()
        self._choose_delivery(self.delivery)
        self.client.get(reverse('checkout'))
        result = self.client.post(reverse('checkout'), self._checkout_payload(method=self.delivery))
        self.assertEqual(result.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.delivery_eta, 'в согласованный день')
        self.assertEqual(order.pickup_address, 'ул. Мастерская, 1')
        thanks = self.client.get(order.get_absolute_url())
        self.assertContains(thanks, 'Благодарим вас за заказ, Тестовый покупатель')
        self.assertNotContains(thanks, 'Благодарим вас за заказ, NoName')
        self.assertContains(thanks, order.public_number)
        self.assertNotContains(thanks, 'ул. Мастерская, 1')
        self.assertNotContains(thanks, 'status-pill')

    def test_checkout_rejects_phone_without_enough_digits(self):
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        response = self.client.post(reverse('checkout'), self._checkout_payload(phone='aaaaaa'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(response, 'Введите правильный номер телефона')

    def test_checkout_accepts_only_a_russian_contact_phone(self):
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        spaced = self.client.post(reverse('checkout'), self._checkout_payload(phone='8 (999) 123-45-67'))
        self.assertEqual(spaced.status_code, 302)
        self.assertEqual(Order.objects.get().phone, '89991234567')
        for bad in ('79991234567', '+7999123456', '+799912345678', '8 999 123-45-6a', '+8 999 123-45-67'):
            self._add()
            self._choose_delivery()
            self.client.get(reverse('checkout'))
            refused = self.client.post(reverse('checkout'), self._checkout_payload(phone=bad))
            self.assertEqual(refused.status_code, 200, bad)
            self.assertContains(refused, 'Введите правильный номер телефона')

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

    def test_cdek_without_keys_keeps_the_fixed_price(self):
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code', 'price'])
        with patch('shop.cdek.urllib.request.build_opener') as opener:
            order = self._create_order()
        opener.assert_not_called()
        self.assertEqual(order.delivery_price, Decimal('350.00'))
        self.assertEqual(order.delivery_point_code, '')

    @override_settings(**CDEK_SETTINGS)
    def test_cart_labels_live_cdek_without_a_fixed_total(self):
        self._add()
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'по индексу')
        self.assertContains(response, 'data-method-price="quote"')

    @override_settings(**CDEK_SETTINGS)
    def test_cdek_quote_is_stored_and_a_posted_price_is_ignored(self):
        cache.clear()
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code'])
        opener = _CdekOpener([480, 480])
        with patch('shop.cdek.urllib.request.build_opener', return_value=opener):
            self._add()
            self._choose_delivery()
            self.client.get(reverse('checkout'))
            first = self.client.post(reverse('checkout'), self._checkout_payload(price='1', total='1'))
            self.assertEqual(first.status_code, 200)
            self.assertContains(first, 'На Тверской')
            self.assertContains(first, '480')
            self.assertNotContains(first, 'cdek-secret-not-for-pages')
            self.assertEqual(Order.objects.count(), 0)
            second = self.client.post(reverse('checkout'), self._checkout_payload(
                price='1', total='1', delivery_point='MSK1',
            ))
        self.assertEqual(second.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.delivery_price, Decimal('480.00'))
        self.assertEqual(order.total, Decimal('6480.00'))
        weights = [json.loads(body)['packages'][0]['weight'] for body in opener.bodies if 'packages' in body]
        self.assertEqual(weights, [1000, 1000])
        self.assertEqual(order.delivery_point_code, 'MSK1')
        self.assertIn('Тверская, 1', order.delivery_point_address)
        self.assertEqual(order.delivery_eta, '2–5 дн.')
        thanks = self.client.get(order.get_absolute_url())
        self.assertContains(thanks, 'Благодарим вас за заказ')
        self.assertNotContains(thanks, 'MSK1')
        self.assertNotIn('cdek-secret-not-for-pages', opener.bodies)

    @override_settings(**CDEK_SETTINGS)
    def test_cdek_failure_does_not_create_an_order_or_zero_the_price(self):
        cache.clear()
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code'])
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        with patch('shop.cdek.urllib.request.build_opener', return_value=_BrokenOpener()):
            response = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не ответил')
        self.assertEqual(Order.objects.count(), 0)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.price, Decimal('350.00'))

    @override_settings(**CDEK_SETTINGS)
    def test_cdek_zero_price_is_rejected(self):
        cache.clear()
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code'])
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        with patch('shop.cdek.urllib.request.build_opener', return_value=_CdekOpener([0])):
            response = self.client.post(reverse('checkout'), self._checkout_payload())
        self.assertContains(response, 'не ответил')
        self.assertEqual(Order.objects.count(), 0)

    @override_settings(**{**CDEK_SETTINGS, 'CDEK_API_URL': 'https://evil.example/v2'})
    def test_cdek_secret_is_not_sent_to_an_unknown_host(self):
        cache.clear()
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code'])
        self._add()
        self._choose_delivery()
        self.client.get(reverse('checkout'))
        with patch('shop.cdek.urllib.request.build_opener') as opener:
            response = self.client.post(reverse('checkout'), self._checkout_payload())
        opener.assert_not_called()
        self.assertContains(response, 'не ответил')
        self.assertEqual(Order.objects.count(), 0)

    @override_settings(**CDEK_SETTINGS)
    def test_changed_cdek_price_is_shown_again_before_the_order(self):
        cache.clear()
        self.delivery.code = DeliveryMethod.CDEK
        self.delivery.save(update_fields=['code'])
        opener = _CdekOpener([480, 510, 510])
        with patch('shop.cdek.urllib.request.build_opener', return_value=opener):
            self._add()
            self._choose_delivery()
            self.client.get(reverse('checkout'))
            self.client.post(reverse('checkout'), self._checkout_payload())
            second = self.client.post(reverse('checkout'), self._checkout_payload(delivery_point='MSK1'))
            self.assertEqual(second.status_code, 200)
            self.assertContains(second, '510')
            self.assertEqual(Order.objects.count(), 0)
            third = self.client.post(reverse('checkout'), self._checkout_payload(delivery_point='MSK1'))
        self.assertEqual(third.status_code, 302)
        self.assertEqual(Order.objects.get().delivery_price, Decimal('510.00'))


class _Body:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def read(self, limit):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _CdekOpener:
    def __init__(self, prices):
        self.prices = list(prices)
        self.bodies = []

    def open(self, request, timeout):
        if request.data:
            self.bodies.append(request.data.decode())
        if request.full_url.endswith('/oauth/token'):
            return _Body({'access_token': 'tok', 'expires_in': 3600})
        if request.full_url.endswith('/calculator/tariff'):
            body = json.loads(request.data.decode())
            if body['from_location']['postal_code'] != '101000' or 'price' in body:
                raise AssertionError(body)
            return _Body({'total_sum': self.prices.pop(0), 'period_min': 2, 'period_max': 5})
        if '/deliverypoints?' in request.full_url:
            return _Body([{
                'code': 'MSK1',
                'name': 'На Тверской',
                'location': {'city': 'Москва', 'address': 'Тверская, 1'},
            }])
        raise AssertionError(request.full_url)


class _BrokenOpener:
    def open(self, request, timeout):
        raise URLError('down')


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
        self.assertContains(response, 'ПОКУПКИ')
        self.assertContains(response, 'telegram-actions')
        self.assertEqual(len(response.context['purchase_bars']), 14)

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

    def test_manager_open_is_recorded_for_the_store_only(self):
        self.client.force_login(self.customer)
        own = self.client.get(self.alpha_order.get_absolute_url())
        self.assertNotContains(own, 'Кто открывал')
        self.assertFalse(OrderRead.objects.filter(order=self.alpha_order, user=self.customer).exists())
        self.client.force_login(self.manager_user)
        hidden = self.client.get(self.alpha_order.get_absolute_url())
        self.assertNotContains(hidden, 'Кто открывал')
        self.assertTrue(OrderRead.objects.filter(order=self.alpha_order, user=self.manager_user).exists())
        self.client.force_login(self.other_manager_user)
        self.assertEqual(self.client.get(self.alpha_order.get_absolute_url()).status_code, 404)
        self.assertFalse(OrderRead.objects.filter(order=self.alpha_order, user=self.other_manager_user).exists())
        staff = User.objects.create_superuser('store_reader', 'store@example.com', 'StorePass#813')
        self.client.force_login(staff)
        seen = self.client.get(self.alpha_order.get_absolute_url())
        self.assertContains(seen, 'Кто открывал')
        self.assertContains(seen, self.manager_user.username)

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

    def test_manager_sets_assembly_carrier_and_shipped_on_own_order(self):
        self.client.force_login(self.manager_user)
        page = self.client.get(self.alpha_order.get_absolute_url())
        self.assertTrue(page.context['can_change_brand_status'])
        self.assertContains(page, 'В службе доставки')
        url = reverse('brand_order_status', args=[self.alpha_order.pk])
        self.client.post(url, {'status': Order.PROCESSING, 'tracking_number': ''})
        self.alpha_order.refresh_from_db()
        self.assertEqual(self.alpha_order.status, Order.PROCESSING)
        self.client.post(url, {'status': Order.IN_DELIVERY})
        self.alpha_order.refresh_from_db()
        self.assertEqual(self.alpha_order.status, Order.IN_DELIVERY)
        self.client.post(url, {'status': Order.SHIPPED, 'tracking_number': 'CDEK123'})
        self.alpha_order.refresh_from_db()
        self.assertEqual(self.alpha_order.status, Order.SHIPPED)
        self.assertEqual(self.alpha_order.tracking_number, 'CDEK123')

    def test_manager_cannot_cancel_or_change_another_brands_order(self):
        self.client.force_login(self.manager_user)
        self.client.post(reverse('brand_order_status', args=[self.alpha_order.pk]), {'status': Order.CANCELLED})
        self.alpha_order.refresh_from_db()
        self.assertEqual(self.alpha_order.status, Order.COMPLETED)
        cancelled = self.client.get(self.cancelled_order.get_absolute_url())
        self.assertFalse(cancelled.context['can_change_brand_status'])
        self.client.post(reverse('brand_order_status', args=[self.cancelled_order.pk]), {'status': Order.SHIPPED})
        self.cancelled_order.refresh_from_db()
        self.assertEqual(self.cancelled_order.status, Order.CANCELLED)
        mixed = self.client.get(self.mixed_order.get_absolute_url())
        self.assertFalse(mixed.context['can_change_brand_status'])
        self.client.post(reverse('brand_order_status', args=[self.mixed_order.pk]), {'status': Order.SHIPPED})
        self.mixed_order.refresh_from_db()
        self.assertEqual(self.mixed_order.status, Order.NEW)
        self.client.force_login(self.other_manager_user)
        self.assertEqual(self.client.post(reverse('brand_order_status', args=[self.alpha_order.pk]), {
            'status': Order.SHIPPED,
        }).status_code, 404)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(reverse('brand_order_status', args=[self.alpha_order.pk]), {
            'status': Order.SHIPPED,
        }).status_code, 403)
