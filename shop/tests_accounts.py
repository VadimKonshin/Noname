"""Email confirmation, login by confirmed mail, password reset and email change."""
import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Brand, BrandManager, EmailToken, Product
from .tests import mail_path

User = get_user_model()


@override_settings(RATELIMIT_ENABLE=False)
class AccountEmailTests(TestCase):
    password = 'PassWithLength#178'

    def test_login_accepts_a_confirmed_email_only(self):
        user = User.objects.create_user('buyer', email='buyer@example.com', password=self.password)
        response = self.client.post(reverse('login'), {'username': 'buyer@example.com', 'password': self.password})
        self.assertRedirects(response, reverse('home'))
        self.client.logout()
        user.profile.email_verified_at = None
        user.profile.save(update_fields=['email_verified_at'])
        refused = self.client.post(reverse('login'), {'username': 'buyer@example.com', 'password': self.password})
        self.assertContains(refused, 'не подтверждена')
        self.assertNotIn('_auth_user_id', self.client.session)
        accepted = self.client.post(reverse('login'), {'username': 'buyer', 'password': self.password})
        self.assertRedirects(accepted, reverse('home'))

    def test_confirmation_link_is_hashed_and_replaced_by_a_resend(self):
        self.client.post(reverse('register'), {
            'username': 'new_person', 'email': 'buyer@example.com',
            'password1': self.password, 'password2': self.password,
        })
        first = mail_path(mail.outbox[0].body)
        raw = first.rstrip('/').rsplit('/', 1)[-1]
        stored = EmailToken.objects.get()
        self.assertEqual(stored.token_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotEqual(stored.token_hash, raw)
        self.client.post(reverse('account_email_resend'))
        self.assertContains(self.client.get(first), 'недействительна')
        self.client.get(mail_path(mail.outbox[-1].body))
        user = User.objects.get(username='new_person')
        self.assertIsNotNone(user.profile.email_verified_at)

    def test_confirmation_link_expires(self):
        user = User.objects.create_user('buyer', email='buyer@example.com', password=self.password)
        user.profile.email_verified_at = None
        user.profile.save(update_fields=['email_verified_at'])
        self.client.force_login(user)
        self.client.post(reverse('account_email_resend'))
        token = EmailToken.objects.get()
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=['expires_at'])
        self.assertContains(self.client.get(mail_path(mail.outbox[0].body)), 'недействительна')
        user.profile.refresh_from_db()
        self.assertIsNone(user.profile.email_verified_at)

    def test_password_reset_reaches_only_a_confirmed_email_and_burns(self):
        user = User.objects.create_user('buyer', email='buyer@example.com', password=self.password)
        plain = User.objects.create_user('plain', email='plain@example.com', password=self.password)
        plain.profile.email_verified_at = None
        plain.profile.save(update_fields=['email_verified_at'])
        self.client.post(reverse('password_reset'), {'email': 'missing@example.com'})
        self.client.post(reverse('password_reset'), {'email': 'plain@example.com'})
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse('password_reset'), {'email': 'buyer@example.com'})
        self.assertEqual(mail.outbox[0].to, ['buyer@example.com'])
        path = mail_path(mail.outbox[0].body)
        self.client.post(path, {'new_password1': 'short', 'new_password2': 'short'})
        self.assertIsNone(EmailToken.objects.get().used_at)
        self.client.post(path, {'new_password1': 'AnotherPass#902', 'new_password2': 'AnotherPass#902'})
        self.assertIsNotNone(EmailToken.objects.get().used_at)
        self.assertContains(self.client.post(path, {
            'new_password1': 'AnotherPass#902', 'new_password2': 'AnotherPass#902',
        }), 'недействительна')
        self.assertTrue(self.client.login(username='buyer', password='AnotherPass#902'))
        self.assertFalse(self.client.login(username='buyer', password=self.password))

    def test_email_change_waits_for_the_new_address(self):
        user = User.objects.create_user('buyer', email='buyer@example.com', password=self.password)
        self.client.force_login(user)
        refused = self.client.post(reverse('account_email'), {'password': 'wrong-password', 'email': 'new@example.com'})
        self.assertContains(refused, 'Неверный пароль')
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse('account_email'), {'password': self.password, 'email': 'new@example.com'})
        user.refresh_from_db()
        self.assertEqual(user.email, 'buyer@example.com')
        self.assertEqual(mail.outbox[0].to, ['new@example.com'])
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'new@example.com', 'password': self.password})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.client.get(mail_path(mail.outbox[0].body))
        user.refresh_from_db()
        self.assertEqual(user.email, 'new@example.com')
        self.assertIsNotNone(user.profile.email_verified_at)
        self.client.post(reverse('login'), {'username': 'new@example.com', 'password': self.password})
        self.assertIn('_auth_user_id', self.client.session)
        self.client.logout()
        old = self.client.post(reverse('login'), {'username': 'buyer@example.com', 'password': self.password})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(old, 'Неверный логин или пароль')

    def test_approved_manager_stays_out_of_the_cabinet_until_email_is_confirmed(self):
        user = User.objects.create_user('brand_manager', email='manager@example.com', password=self.password)
        user.profile.email_verified_at = None
        user.profile.save(update_fields=['email_verified_at'])
        brand = Brand.objects.create(name='ПОЧТА', slug='pochta')
        BrandManager.objects.create(user=user, brand=brand, is_approved=True)
        Product.objects.create(brand=brand, title='Вещь', description='Описание', price='1000.00')
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse('brand_dashboard')), reverse('account_email'))
        self.assertRedirects(self.client.get(reverse('product_create')), reverse('account_email'))
        self.assertContains(self.client.get(reverse('account_email')), 'Кабинет бренда откроется')
        user.profile.email_verified_at = timezone.now()
        user.profile.save(update_fields=['email_verified_at'])
        self.assertEqual(self.client.get(reverse('brand_dashboard')).status_code, 200)
