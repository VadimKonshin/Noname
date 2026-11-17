"""Letters about a new order, shipment and cancellation."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .commerce import update_order_status
from .mail import notify_order_created
from .models import Brand, BrandManager, DeliveryMethod, Order, OrderItem, Product, ProductVariant

User = get_user_model()


@override_settings(RATELIMIT_ENABLE=False, ORDER_NOTICE_EMAIL='store@example.com')
class OrderMailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alpha = Brand.objects.create(name='АЛЬФА', slug='alpha')
        cls.beta = Brand.objects.create(name='БЕТА', slug='beta')
        cls.alpha_shirt = Product.objects.create(
            brand=cls.alpha, title='Свитер Альфы', description='Шерсть', price=Decimal('3000.00'),
        )
        cls.beta_shirt = Product.objects.create(
            brand=cls.beta, title='Куртка Беты', description='Плащ', price=Decimal('6000.00'),
        )
        ProductVariant.objects.create(product=cls.alpha_shirt, size='M', stock_quantity=5)
        ProductVariant.objects.create(product=cls.beta_shirt, size='L', stock_quantity=5)
        cls.alpha_user = User.objects.create_user('alpha_manager', email='alpha@example.com', password='PassWithLength#178')
        cls.beta_user = User.objects.create_user('beta_manager', email='beta@example.com', password='PassWithLength#178')
        cls.quiet_user = User.objects.create_user('quiet_manager', email='quiet@example.com', password='PassWithLength#178')
        cls.unconfirmed = User.objects.create_user('unconfirmed_manager', email='unconfirmed@example.com', password='PassWithLength#178')
        cls.unconfirmed.profile.email_verified_at = None
        cls.unconfirmed.profile.save(update_fields=['email_verified_at'])
        BrandManager.objects.create(user=cls.alpha_user, brand=cls.alpha, is_approved=True)
        BrandManager.objects.create(user=cls.beta_user, brand=cls.beta, is_approved=True)
        BrandManager.objects.create(user=cls.quiet_user, brand=cls.alpha, is_approved=False)
        BrandManager.objects.create(user=cls.unconfirmed, brand=cls.alpha, is_approved=True)
        cls.delivery = DeliveryMethod.objects.create(name='По городу', price=Decimal('0.00'), requires_address=True)

    def _add(self, product, size):
        self.client.post(reverse('cart_add', args=[product.slug]), {
            'size': size, 'quantity': 1, 'order_type': 'stock', 'go_to_cart': '1',
        })

    def test_checkout_letters_keep_each_brand_to_its_own_items(self):
        self._add(self.alpha_shirt, 'M')
        refused = self.client.post(reverse('cart_add', args=[self.beta_shirt.slug]), {
            'size': 'L', 'quantity': 1, 'order_type': 'stock',
        }, follow=True)
        self.assertContains(refused, 'один бренд и одна доставка')
        self.assertEqual(refused.context['cart']['count'], 1)
        order = Order.objects.create(
            checkout_token='mail-mixed', full_name='Тестовый покупатель', email='buyer@example.com',
            phone='+79991234567', city='Москва', address='ул. Тестовая, д. 1', postal_code='123456',
            comment='Позвоните', delivery_name='По городу', delivery_price=Decimal('0'),
            subtotal=Decimal('9000'), total=Decimal('9000'),
        )
        OrderItem.objects.create(
            order=order, product=self.alpha_shirt, product_title=self.alpha_shirt.title,
            brand_name=self.alpha.name, size='M', quantity=1, unit_price=Decimal('3000'), line_total=Decimal('3000'),
        )
        OrderItem.objects.create(
            order=order, product=self.beta_shirt, product_title=self.beta_shirt.title,
            brand_name=self.beta.name, size='L', quantity=1, unit_price=Decimal('6000'), line_total=Decimal('6000'),
        )
        with self.captureOnCommitCallbacks(execute=True):
            notify_order_created(order.pk)
        by_recipient = {message.to[0]: message for message in mail.outbox}
        self.assertEqual(set(by_recipient), {'buyer@example.com', 'alpha@example.com', 'beta@example.com', 'store@example.com'})
        self.assertNotIn('quiet@example.com', by_recipient)
        self.assertNotIn('unconfirmed@example.com', by_recipient)
        buyer = by_recipient['buyer@example.com']
        self.assertIn('не списывалась', buyer.body)
        self.assertIn('Свитер Альфы', buyer.body)
        self.assertIn('Куртка Беты', buyer.body)
        alpha = by_recipient['alpha@example.com'].body
        beta = by_recipient['beta@example.com'].body
        self.assertIn('Свитер Альфы', alpha)
        self.assertNotIn('Куртка Беты', alpha)
        self.assertIn('ул. Тестовая', alpha)
        self.assertIn('+79991234567', alpha)
        self.assertNotIn('9000', alpha)
        self.assertIn('Куртка Беты', beta)
        self.assertNotIn('Свитер Альфы', beta)
        admin = by_recipient['store@example.com'].body
        self.assertIn('Свитер Альфы', admin)
        self.assertIn('Куртка Беты', admin)

    def test_status_letter_goes_to_the_buyer_only_for_shipped_and_cancelled(self):
        order = Order.objects.create(
            checkout_token='mail-status', full_name='Покупатель', email='buyer@example.com',
            phone='+79991234567', delivery_name='По городу', delivery_price=Decimal('0'),
            subtotal=Decimal('3000'), total=Decimal('3000'),
        )
        OrderItem.objects.create(
            order=order, product=self.alpha_shirt, product_title=self.alpha_shirt.title,
            brand_name=self.alpha.name, size='M', quantity=1, unit_price=Decimal('3000'), line_total=Decimal('3000'),
        )
        with self.captureOnCommitCallbacks(execute=True):
            update_order_status(order.pk, Order.PROCESSING)
        self.assertEqual(len(mail.outbox), 0)
        with self.captureOnCommitCallbacks(execute=True):
            update_order_status(order.pk, Order.SHIPPED)
        with self.captureOnCommitCallbacks(execute=True):
            update_order_status(order.pk, Order.CANCELLED)
        self.assertEqual([message.to for message in mail.outbox], [['buyer@example.com'], ['buyer@example.com']])
        self.assertIn('отправлен', mail.outbox[0].subject)
        self.assertNotIn('Трек:', mail.outbox[0].body)
        self.assertIn('отменён', mail.outbox[1].subject)
        self.assertNotIn('alpha@example.com', [message.to[0] for message in mail.outbox])

    def test_tracking_added_after_shipment_is_mailed_once(self):
        order = Order.objects.create(
            checkout_token='mail-track', full_name='Покупатель', email='buyer@example.com',
            phone='+79991234567', delivery_name='СДЭК', delivery_price=Decimal('350'),
            subtotal=Decimal('3000'), total=Decimal('3350'), status=Order.SHIPPED,
        )
        with self.captureOnCommitCallbacks(execute=True):
            update_order_status(order.pk, Order.SHIPPED, tracking_number='RA123456789RU')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('отправлен', mail.outbox[0].subject)
        self.assertIn('Трек: RA123456789RU', mail.outbox[0].body)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            update_order_status(order.pk, Order.SHIPPED, tracking_number='RA123456789RU')
        self.assertEqual(len(mail.outbox), 0)
