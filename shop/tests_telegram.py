"""Telegram notifications for orders and brand contact, plus the buyers group link."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from urllib.error import URLError

from django.core.cache import cache
from django.utils import timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Brand, BrandManager, BrandMessage, DeliveryMethod, Order, OrderItem, Product, ProductVariant
from .telegram import handle_update, notify_brand_contact, notify_new_order

User = get_user_model()

TELEGRAM = {
    'TELEGRAM_BOT_TOKEN': '123456:TESTTOKEN',
    'TELEGRAM_BOT_USERNAME': 'NoNameNotificationsBot',
    'TELEGRAM_NEWS_URL': 'https://t.me/noname_news',
}


class FakeResponse:
    def __init__(self, payload=b'{"ok": true, "result": {"message_id": 1}}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def sent_payload(request):
    return json.loads(request.data.decode())


@override_settings(RATELIMIT_ENABLE=False, **TELEGRAM)
class TelegramTests(TestCase):
    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user('customer', email='client@example.org', password='PassWithLength#178')
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota')
        cls.shirt = Product.objects.create(
            brand=cls.brand, title='MOON / Черная футболка', description='Хлопок',
            price=Decimal('3000.00'), is_published=True, is_available=True,
        )
        ProductVariant.objects.create(product=cls.shirt, size='M', stock_quantity=10)
        cls.delivery = DeliveryMethod.objects.create(name='Пункт выдачи', price=Decimal('350.00'), requires_address=True)
        cls.manager_user = User.objects.create_user('brand_manager', email='manager@example.org', password='PassWithLength#178')
        cls.manager = BrandManager.objects.create(
            user=cls.manager_user, brand=cls.brand, is_approved=True, telegram_chat_id='4242',
        )

    def _order(self):
        self.client.force_login(self.member)
        self.client.post(reverse('cart_add', args=[self.shirt.slug]), {
            'size': 'M', 'quantity': 2, 'order_type': 'stock', 'go_to_cart': '1',
        })
        self.client.post(reverse('cart_delivery'), {'delivery_method': self.delivery.pk, 'next': '1'})
        self.client.get(reverse('checkout'))
        return self.client.post(reverse('checkout'), {
            'checkout_token': self.client.session['checkout_token'],
            'full_name': 'Тестовый покупатель', 'email': 'client@example.org',
            'phone': '+79991234567', 'city': 'Москва', 'address': 'ул. Тестовая, д. 1',
            'postal_code': '123456', 'comment': 'Позвоните перед доставкой',
        })

    def test_news_link_is_visible_only_for_a_telegram_url(self):
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'https://t.me/noname_news')
        self.assertContains(self.client.get(reverse('info_contacts')), 'Группа для покупателей')
        with override_settings(TELEGRAM_NEWS_URL=''):
            self.assertNotContains(self.client.get(reverse('home')), 't.me')
        with override_settings(TELEGRAM_NEWS_URL='javascript:alert(1)'):
            self.assertNotContains(self.client.get(reverse('home')), 'javascript:')

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_checkout_without_telegram_does_not_call_the_api(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen') as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                self._order()
            urlopen.assert_not_called()
        self.assertEqual(Order.objects.count(), 1)

    def test_checkout_sends_the_full_order_to_managers(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._order()
        self.assertEqual(response.status_code, 302)
        payload = sent_payload(urlopen.call_args.args[0])
        self.assertEqual(payload['chat_id'], '4242')
        self.assertEqual(payload['parse_mode'], 'HTML')
        text = payload['text']
        self.assertIn('Новый заказ №', text)
        self.assertIn('+79991234567', text)
        self.assertIn('client@example.org', text)
        self.assertIn('Тестовый покупатель', text)
        self.assertIn('ПУСТОТА', text)
        self.assertIn('MOON / Черная футболка', text)
        self.assertIn('размер M', text)
        self.assertIn('2 шт.', text)
        self.assertIn('3000 ₽', text)
        self.assertIn('6000 ₽', text)
        self.assertIn('Сумма ваших позиций: 6000 ₽', text)
        self.assertIn('Пункт выдачи', text)
        self.assertNotIn('6350', text)
        self.assertIn('123456, Москва, ул. Тестовая, д. 1', text)
        self.assertIn('Позвоните перед доставкой', text)
        self.assertIn('в наличии', text)
        self.assertContains(self.client.get(Order.objects.get().get_absolute_url()), 'https://t.me/NoNameNotificationsBot')

    def test_telegram_outage_still_keeps_the_order(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen', side_effect=URLError('down')):
            with self.captureOnCommitCallbacks(execute=True):
                response = self._order()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get().phone, '+79991234567')

    def test_contact_request_goes_to_managers_with_escaped_text(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('brand_message', args=[self.brand.slug]), {
                    'sender_name': '<b>Гость</b>',
                    'sender_email': 'guest@example.com',
                    'body': 'Есть ли размер M <script> на следующей неделе?',
                    'product': self.shirt.slug,
                })
        self.assertRedirects(response, reverse('brand_storefront', args=[self.brand.slug]))
        payload = sent_payload(urlopen.call_args.args[0])
        self.assertEqual(payload['chat_id'], '4242')
        self.assertEqual(urlopen.call_count, 1)
        text = payload['text']
        self.assertIn('связаться с менеджером', text)
        self.assertIn('ПУСТОТА', text)
        self.assertIn('MOON / Черная футболка', text)
        self.assertIn('guest@example.com', text)
        self.assertIn('&lt;b&gt;Гость&lt;/b&gt;', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('<script>', text)
        self.assertEqual(BrandMessage.objects.count(), 1)

    def test_direct_order_escapes_markup_and_marks_preorder(self):
        from unittest.mock import patch
        order = Order.objects.create(
            user=self.member, checkout_token='telegram-order',
            full_name='Имя & <script>', email='client@example.org', phone='+79990001122',
            delivery_name='Самовывоз', delivery_eta='сегодня', pickup_address='мастерская & склад',
            delivery_price=Decimal('0'), subtotal=Decimal('3000.50'), total=Decimal('3000.50'),
            comment='a & b',
        )
        OrderItem.objects.create(
            order=order, product_title='Архив', brand_name='ПУСТОТА', size='L',
            quantity=1, unit_price=Decimal('3000.50'), line_total=Decimal('3000.50'),
            is_preorder=True, preorder_days=14,
        )
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                notify_new_order(order.pk)
        text = sent_payload(urlopen.call_args.args[0])['text']
        self.assertIn('Имя &amp; &lt;script&gt;', text)
        self.assertIn('мастерская &amp; склад', text)
        self.assertIn('a &amp; b', text)
        self.assertIn('предзаказ, 14 дн.', text)
        self.assertIn('3000.50 ₽', text)
        self.assertNotIn('<script>', text)

    def test_missing_order_does_not_raise(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen') as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                notify_new_order(999999)
                notify_brand_contact(999999)
        urlopen.assert_not_called()

    def test_each_manager_receives_only_their_brand(self):
        from unittest.mock import patch
        other = Brand.objects.create(name='ДРУГОЙ', slug='drugoy')
        other_user = User.objects.create_user('other_manager', email='other@example.org', password='PassWithLength#178')
        BrandManager.objects.create(user=other_user, brand=other, is_approved=True, telegram_chat_id='7777')
        order = Order.objects.create(
            checkout_token='two-brands', full_name='Покупатель', email='buyer@example.org',
            phone='+79990001122', delivery_name='Самовывоз', delivery_price=Decimal('0'),
            subtotal=Decimal('5000'), total=Decimal('5000'),
        )
        OrderItem.objects.create(
            order=order, product_title='MOON / Черная футболка', brand_name='ПУСТОТА', size='M',
            quantity=1, unit_price=Decimal('3000'), line_total=Decimal('3000'),
        )
        OrderItem.objects.create(
            order=order, product_title='Чужая вещь', brand_name='ДРУГОЙ', size='S',
            quantity=1, unit_price=Decimal('2000'), line_total=Decimal('2000'),
        )
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                notify_new_order(order.pk)
        by_chat = {
            sent_payload(call.args[0])['chat_id']: sent_payload(call.args[0])['text']
            for call in urlopen.call_args_list
        }
        self.assertIn('MOON / Черная футболка', by_chat['4242'])
        self.assertNotIn('Чужая вещь', by_chat['4242'])
        self.assertIn('Чужая вещь', by_chat['7777'])
        self.assertNotIn('MOON', by_chat['7777'])

    def test_unconfirmed_manager_is_not_notified(self):
        from unittest.mock import patch
        quiet = User.objects.create_user('quiet_manager', email='quiet@example.org', password='PassWithLength#178')
        quiet.profile.email_verified_at = None
        quiet.profile.save(update_fields=['email_verified_at'])
        BrandManager.objects.create(user=quiet, brand=self.brand, is_approved=True, telegram_chat_id='9090')
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                self._order()
        chats = [sent_payload(call.args[0])['chat_id'] for call in urlopen.call_args_list]
        self.assertIn('4242', chats)
        self.assertNotIn('9090', chats)

    def test_unapproved_manager_is_not_notified(self):
        from unittest.mock import patch
        BrandManager.objects.filter(pk=self.manager.pk).update(is_approved=False)
        with patch('shop.telegram.urlopen') as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                self._order()
        urlopen.assert_not_called()

    def test_buyer_status_needs_the_order_phone(self):
        from unittest.mock import patch
        order = Order.objects.create(
            checkout_token='status-order', full_name='Секретное имя', email='buyer@example.org',
            phone='+7 (999) 123-45-67', delivery_name='Самовывоз', delivery_price=Decimal('0'),
            subtotal=Decimal('3000'), total=Decimal('3000'), status=Order.SHIPPED,
        )
        OrderItem.objects.create(
            order=order, product_title='MOON / Черная футболка', brand_name='ПУСТОТА', size='M',
            quantity=1, unit_price=Decimal('3000'), line_total=Decimal('3000'),
        )
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': f'{order.pk} +79990000000', 'chat': {'id': 51, 'type': 'private'}}})
        hidden = sent_payload(urlopen.call_args.args[0])['text']
        self.assertIn('не найден', hidden)
        self.assertNotIn('Секретное имя', hidden)
        order.refresh_from_db()
        self.assertEqual(order.buyer_telegram_chat_id, '')
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': f'{order.pk} 89991234567', 'chat': {'id': 50, 'type': 'private'}}})
        text = sent_payload(urlopen.call_args.args[0])['text']
        self.assertIn('Отправлен', text)
        self.assertIn('MOON / Черная футболка', text)
        self.assertNotIn('Секретное имя', text)
        self.assertIn('этот чат', text)
        order.refresh_from_db()
        self.assertEqual(order.buyer_telegram_chat_id, '50')
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': f'{order.pk} 89991234567', 'chat': {'id': 52, 'type': 'private'}}})
        kept = sent_payload(urlopen.call_args.args[0])['text']
        self.assertIn('Отправлен', kept)
        self.assertIn('первым проверил', kept)
        self.assertNotIn('этот чат', kept)
        order.refresh_from_db()
        self.assertEqual(order.buyer_telegram_chat_id, '50')
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': f'{order.pk} +79990000000', 'chat': {'id': 51, 'type': 'private'}}})
        order.refresh_from_db()
        self.assertEqual(order.buyer_telegram_chat_id, '50')

    def test_manager_connects_from_private_start_link(self):
        from unittest.mock import patch
        token = 'personal-bind-token'
        self.manager.telegram_chat_id = None
        self.manager.telegram_bind_hash = hashlib.sha256(token.encode()).hexdigest()
        self.manager.telegram_bind_expires = timezone.now() + timedelta(minutes=10)
        self.manager.save()
        with patch('shop.telegram.urlopen', return_value=FakeResponse()):
            handle_update({'message': {'text': f'/start {token}', 'chat': {'id': 555, 'type': 'private'}}})
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.telegram_chat_id, '555')
        self.assertEqual(self.manager.telegram_bind_hash, '')
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': '/start expired-token', 'chat': {'id': 556, 'type': 'private'}}})
        self.assertIn('устарела', sent_payload(urlopen.call_args.args[0])['text'])

    def test_group_chat_cannot_read_an_order(self):
        from unittest.mock import patch
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': '1 +79991234567', 'chat': {'id': -100, 'type': 'group'}}})
        self.assertIn('личном чате', sent_payload(urlopen.call_args.args[0])['text'])

    def test_cabinet_gives_the_manager_a_personal_link(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('brand_telegram_connect'), follow=True)
        self.assertContains(response, 'https://t.me/NoNameNotificationsBot?start=')
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.telegram_bind_hash)
        self.client.post(reverse('brand_telegram_disconnect'))
        self.manager.refresh_from_db()
        self.assertIsNone(self.manager.telegram_chat_id)

    def test_shipped_and_cancelled_ping_the_buyer_and_each_brand(self):
        from unittest.mock import patch
        from .commerce import update_order_status
        other = Brand.objects.create(name='ДРУГОЙ', slug='drugoy')
        other_user = User.objects.create_user('status_manager', email='other-status@example.com', password='PassWithLength#178')
        BrandManager.objects.create(user=other_user, brand=other, is_approved=True, telegram_chat_id='7777')
        order = Order.objects.create(
            checkout_token='status-push', full_name='Секретное имя', email='buyer@example.org',
            phone='+79991234567', delivery_name='Самовывоз', delivery_price=Decimal('0'),
            subtotal=Decimal('5000'), total=Decimal('5000'), buyer_telegram_chat_id='50',
        )
        OrderItem.objects.create(
            order=order, product=self.shirt, product_title=self.shirt.title, brand_name=self.brand.name,
            size='M', quantity=1, unit_price=Decimal('3000'), line_total=Decimal('3000'),
        )
        OrderItem.objects.create(
            order=order, product_title='Чужая вещь', brand_name=other.name, size='S',
            quantity=1, unit_price=Decimal('2000'), line_total=Decimal('2000'),
        )
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                update_order_status(order.pk, Order.PROCESSING)
        urlopen.assert_not_called()
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                update_order_status(order.pk, Order.SHIPPED)
        by_chat = {
            sent_payload(call.args[0])['chat_id']: sent_payload(call.args[0])['text']
            for call in urlopen.call_args_list
        }
        self.assertIn('Отправлен', by_chat['50'])
        self.assertIn(self.shirt.title, by_chat['50'])
        self.assertIn('Чужая вещь', by_chat['50'])
        self.assertNotIn('Секретное имя', by_chat['4242'])
        self.assertIn(self.shirt.title, by_chat['4242'])
        self.assertNotIn('Чужая вещь', by_chat['4242'])
        self.assertIn('Чужая вещь', by_chat['7777'])
        self.assertNotIn(self.shirt.title, by_chat['7777'])

    def test_unconfirmed_manager_link_does_not_connect(self):
        from unittest.mock import patch
        quiet = User.objects.create_user('quiet_bind', email='quiet-bind@example.org', password='PassWithLength#178')
        quiet.profile.email_verified_at = None
        quiet.profile.save(update_fields=['email_verified_at'])
        token = 'quiet-bind-token'
        manager = BrandManager.objects.create(
            user=quiet, brand=self.brand, is_approved=True,
            telegram_bind_hash=hashlib.sha256(token.encode()).hexdigest(),
            telegram_bind_expires=timezone.now() + timedelta(minutes=10),
        )
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({'message': {'text': f'/start {token}', 'chat': {'id': 818, 'type': 'private'}}})
        manager.refresh_from_db()
        self.assertIsNone(manager.telegram_chat_id)
        self.assertEqual(manager.telegram_bind_hash, '')
        self.assertIn('устарела', sent_payload(urlopen.call_args.args[0])['text'])

    def test_status_checks_for_one_phone_are_shared_across_chats(self):
        from unittest.mock import patch
        phone = '+70001110001'
        with patch('shop.telegram.PHONE_STATUS_LIMIT', 2):
            for offset in range(2):
                with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                    handle_update({
                        'message': {
                            'text': f'{700000 + offset} {phone}',
                            'chat': {'id': 9000 + offset, 'type': 'private'},
                        },
                    })
                self.assertIn('не найден', sent_payload(urlopen.call_args.args[0])['text'])
            with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                handle_update({
                    'message': {'text': f'700099 {phone}', 'chat': {'id': 9099, 'type': 'private'}},
                })
            self.assertIn('Слишком много', sent_payload(urlopen.call_args.args[0])['text'])
        with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
            handle_update({
                'message': {'text': '700100 +70001110002', 'chat': {'id': 9100, 'type': 'private'}},
            })
        self.assertIn('не найден', sent_payload(urlopen.call_args.args[0])['text'])

    def test_one_chat_cannot_keep_guessing_with_new_phones(self):
        from unittest.mock import patch
        with patch('shop.telegram.CHAT_STATUS_LIMIT', 2):
            for offset in range(2):
                with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                    handle_update({
                        'message': {
                            'text': f'{710000 + offset} +7000222000{offset}',
                            'chat': {'id': 7200, 'type': 'private'},
                        },
                    })
                self.assertIn('не найден', sent_payload(urlopen.call_args.args[0])['text'])
            with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                handle_update({
                    'message': {'text': '710099 +70002220009', 'chat': {'id': 7200, 'type': 'private'}},
                })
            self.assertIn('Слишком много', sent_payload(urlopen.call_args.args[0])['text'])

    def test_bind_attempts_are_limited_per_chat(self):
        from unittest.mock import patch
        with patch('shop.telegram.BIND_LIMIT', 2):
            for token in ('nope-a', 'nope-b'):
                with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                    handle_update({
                        'message': {'text': f'/start {token}', 'chat': {'id': 6400, 'type': 'private'}},
                    })
                self.assertIn('устарела', sent_payload(urlopen.call_args.args[0])['text'])
            with patch('shop.telegram.urlopen', return_value=FakeResponse()) as urlopen:
                handle_update({
                    'message': {'text': '/start nope-c', 'chat': {'id': 6400, 'type': 'private'}},
                })
            self.assertIn('Слишком много', sent_payload(urlopen.call_args.args[0])['text'])
