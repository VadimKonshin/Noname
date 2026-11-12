"""Run with: python manage.py test"""
import io
import tempfile
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from .models import Brand, Product, ProductImage


User = get_user_model()


def sample_png(name='test.png'):
    output = io.BytesIO()
    Image.new('RGB', (30, 30), color='#333333').save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


@override_settings(RATELIMIT_ENABLE=False)
class StoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username='manager', password='SecurePass813!', is_staff=True, is_superuser=True)
        cls.customer = User.objects.create_user(username='buyer', password='SecurePass813!')
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota')
        cls.product = Product.objects.create(brand=cls.brand, title='Тестовая футболка', description='Мягкий хлопок', price=Decimal('2990.00'), is_featured=True)

    def test_home_and_catalog(self):
        self.assertContains(self.client.get(reverse('home')), self.product.title)
        self.assertContains(self.client.get(reverse('catalog')), self.product.title)

    def test_product_detail(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мягкий хлопок')

    def test_search(self):
        self.assertContains(self.client.get(reverse('catalog'), {'q': 'хлопок'}), self.product.title)
        self.assertNotContains(self.client.get(reverse('catalog'), {'q': 'несуществующий'}), self.product.title)

    def test_private_product_hidden_from_catalog(self):
        self.product.is_published = False
        self.product.save()
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('catalog')), self.product.title)

    def test_registration(self):
        resp = self.client.post(reverse('register'), {
            'username': 'new_person', 'email': 'buyer@example.com',
            'password1': 'HardToGuess837!', 'password2': 'HardToGuess837!',
        })
        self.assertRedirects(resp, reverse('home'))
        self.assertTrue(User.objects.filter(username='new_person').exists())
        self.assertEqual(str(self.client.session['_auth_user_id']), str(User.objects.get(username='new_person').pk))

    def test_anonymous_cannot_add_product(self):
        response = self.client.get(reverse('product_create'))
        self.assertRedirects(response, '/login/?next=/products/add/')

    def test_normal_user_cannot_add_product(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('product_create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('product_create'), {'title': 'Not allowed'}).status_code, 403)

    def test_staff_create_and_upload_multiple_pictures(self):
        with tempfile.TemporaryDirectory() as temp:
            with override_settings(MEDIA_ROOT=temp):
                self.client.force_login(self.staff)
                response = self.client.post(reverse('product_create'), {
                    'brand': self.brand.pk, 'title': 'Новая футболка', 'description': 'Описание вещи', 'price': '3990',
                    'sizes': 'M, L, XL', 'color': 'Чёрный', 'is_published': 'on',
                    'cover': sample_png('cover.png'),
                    'gallery_images': [sample_png('a.png'), sample_png('b.png')],
                })
                item = Product.objects.get(title='Новая футболка')
                self.assertRedirects(response, item.get_absolute_url())
                self.assertEqual(item.images.count(), 2)
                self.assertTrue(item.cover.name)

    def test_bad_gallery_image_rejected(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('product_create'), {
            'brand': self.brand.pk, 'title': 'С плохим фото', 'description': 'Описание', 'price': '1000',
            'sizes': 'M', 'color': 'Чёрный', 'is_published': 'on',
            'gallery_images': SimpleUploadedFile('wrong.png', b'not a real picture', content_type='image/png'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(title='С плохим фото').exists())

    def test_staff_can_edit_and_remove_gallery_image(self):
        with tempfile.TemporaryDirectory() as temp:
            with override_settings(MEDIA_ROOT=temp):
                image = ProductImage.objects.create(product=self.product, image=sample_png())
                self.client.force_login(self.staff)
                response = self.client.post(reverse('product_edit', kwargs={'slug': self.product.slug}), {
                    'brand': self.brand.pk, 'title': 'Изменённое название', 'description': 'Новый текст', 'price': '3490',
                    'sizes': 'S, M', 'color': 'Белый', 'is_published': 'on',
                    'delete_images': [str(image.pk)],
                })
                self.assertEqual(response.status_code, 302)
                self.product.refresh_from_db()
                self.assertEqual(self.product.title, 'Изменённое название')
                self.assertFalse(self.product.images.exists())

    def test_unique_slugs(self):
        another = Product.objects.create(title=self.product.title, description='Другая', price=2000)
        self.assertNotEqual(another.slug, self.product.slug)

    def test_brand_filter_and_ticker_from_database(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'ПУСТОТА')
        other = Brand.objects.create(name='DISTRICT', slug='district')
        other_item = Product.objects.create(brand=other, title='Другая модель', description='Хлопок', price=3990)
        selected = self.client.get(reverse('catalog'), {'brand': other.slug})
        self.assertContains(selected, other_item.title)
        self.assertNotContains(selected, self.product.title)
        self.assertContains(self.client.get(reverse('home')), other.name)

    def test_size_buttons_start_unselected_and_missing_are_disabled(self):
        self.product.sizes = 'M, XL'
        self.product.save()
        detail = self.client.get(self.product.get_absolute_url())
        self.assertContains(detail, 'data-size-picker')
        self.assertContains(detail, 'value="S" disabled')
        self.assertContains(detail, 'value="M"')
        self.assertNotContains(detail, 'name="size" value="M" checked')
        self.assertContains(detail, 'ВЫБЕРИ РАЗМЕР')
        catalog = self.client.get(reverse('catalog'))
        self.assertContains(catalog, 'data-size-picker')
        self.assertContains(catalog, 'name="go_to_cart" value="1"')


class SecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        brand = Brand.objects.create(name='Security brand', slug='security-brand')
        self.product = Product.objects.create(brand=brand, title='Security product', description='Test', price=1000)

    def tearDown(self):
        cache.clear()

    def test_staff_flag_alone_does_not_grant_management_access(self):
        staff = User.objects.create_user(username='limited_staff', password='SecurePass813!', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('product_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

    def test_model_image_validation_rejects_non_image(self):
        image = ProductImage(
            product=self.product,
            image=SimpleUploadedFile('fake.png', b'not an image', content_type='image/png'),
        )
        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_model_image_validation_rejects_oversized_file(self):
        uploaded = sample_png()
        uploaded.size = 8 * 1024 * 1024 + 1
        image = ProductImage(product=self.product, image=uploaded)
        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_csrf_is_required_for_cart_changes(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('cart_add', args=[self.product.slug]), {'size': 'M', 'quantity': 1})
        self.assertEqual(response.status_code, 403)

    def test_login_rate_limit(self):
        for _ in range(10):
            response = self.client.post(reverse('login'), {'username': 'nobody', 'password': 'wrong-password'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('login'), {'username': 'nobody', 'password': 'wrong-password'})
        self.assertEqual(response.status_code, 429)

    def test_admin_login_rate_limit(self):
        for _ in range(10):
            response = self.client.post('/admin/login/', {'username': 'nobody', 'password': 'wrong-password'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post('/admin/login/', {'username': 'nobody', 'password': 'wrong-password'})
        self.assertEqual(response.status_code, 429)

    def test_registration_rate_limit(self):
        for _ in range(5):
            self.client.post(reverse('register'), {})
        self.assertEqual(self.client.post(reverse('register'), {}).status_code, 429)

    def test_checkout_rate_limit(self):
        for _ in range(10):
            self.client.post(reverse('checkout'), {})
        self.assertEqual(self.client.post(reverse('checkout'), {}).status_code, 429)
