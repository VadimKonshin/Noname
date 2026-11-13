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
from .models import Brand, BrandManager, Product, ProductImage, ProductVariant


User = get_user_model()


def sample_png(name='test.png'):
    output = io.BytesIO()
    Image.new('RGB', (30, 30), color='#333333').save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


def variant_payload(product=None):
    variants = list(product.variants.all()) if product else [None]
    data = {
        'variants-TOTAL_FORMS': str(len(variants)),
        'variants-INITIAL_FORMS': str(len(variants) if product else 0),
        'variants-MIN_NUM_FORMS': '0',
        'variants-MAX_NUM_FORMS': '1000',
    }
    for index, variant in enumerate(variants):
        if variant:
            data[f'variants-{index}-id'] = str(variant.pk)
        data[f'variants-{index}-size'] = variant.size if variant else 'M'
        data[f'variants-{index}-stock_quantity'] = str(variant.stock_quantity if variant else 5)
    return data


@override_settings(RATELIMIT_ENABLE=False)
class StoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username='manager', password='SecurePass813!', is_staff=True, is_superuser=True)
        cls.customer = User.objects.create_user(username='buyer', password='SecurePass813!')
        cls.brand = Brand.objects.create(name='ПУСТОТА', slug='pustota')
        cls.product = Product.objects.create(brand=cls.brand, title='Тестовая футболка', description='Мягкий хлопок', price=Decimal('2990.00'), is_featured=True)
        ProductVariant.objects.bulk_create([
            ProductVariant(product=cls.product, size=size, stock_quantity=5)
            for size in ('S', 'M', 'L', 'XL')
        ])

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
                    'color': 'Чёрный', 'is_published': 'on',
                    'cover': sample_png('cover.png'),
                    'gallery_images': [sample_png('a.png'), sample_png('b.png')],
                    **variant_payload(),
                })
                item = Product.objects.get(title='Новая футболка')
                self.assertRedirects(response, item.get_absolute_url())
                self.assertEqual(item.images.count(), 2)
                self.assertTrue(item.cover.name)
                with Image.open(item.cover.path) as cover:
                    cover.load()

    def test_bad_gallery_image_rejected(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('product_create'), {
            'brand': self.brand.pk, 'title': 'С плохим фото', 'description': 'Описание', 'price': '1000',
            'color': 'Чёрный', 'is_published': 'on',
            'gallery_images': SimpleUploadedFile('wrong.png', b'not a real picture', content_type='image/png'),
            **variant_payload(),
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
                    'color': 'Белый', 'is_published': 'on',
                    'delete_images': [str(image.pk)],
                    **variant_payload(self.product),
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

    def test_size_buttons_start_unselected_and_only_real_sizes_show(self):
        self.product.variants.all().delete()
        ProductVariant.objects.bulk_create([
            ProductVariant(product=self.product, size='M', stock_quantity=2),
            ProductVariant(product=self.product, size='XL', stock_quantity=3),
        ])
        detail = self.client.get(self.product.get_absolute_url())
        self.assertContains(detail, 'data-size-picker')
        self.assertContains(detail, 'value="M"')
        self.assertContains(detail, 'value="XL"')
        self.assertNotContains(detail, 'value="S"')
        self.assertNotContains(detail, 'name="size" value="M" checked')
        self.assertContains(detail, 'ВЫБЕРИ РАЗМЕР')
        catalog = self.client.get(reverse('catalog'))
        self.assertNotContains(catalog, 'data-size-picker')
        self.assertContains(catalog, 'СМОТРЕТЬ')
        self.assertContains(catalog, reverse('brand_storefront', args=[self.brand.slug]))

    def test_inactive_brand_hides_products(self):
        self.brand.is_active = False
        self.brand.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('catalog')), self.product.title)
        self.assertNotContains(self.client.get(reverse('home')), self.product.title)
        self.assertEqual(self.client.get(reverse('brand_storefront', args=[self.brand.slug])).status_code, 404)
        self.assertEqual(self.client.post(reverse('cart_add', args=[self.product.slug]), {'size': 'M', 'quantity': 1}).status_code, 404)

    def test_similar_brand_name_is_rejected(self):
        spoof = Brand(name='ПУСТ' + 'O' + 'ТА', slug='lookalike-brand')
        with self.assertRaises(ValidationError):
            spoof.full_clean()

    def test_product_shows_material_and_brand_chart(self):
        self.product.composition = '100% хлопок'
        self.product.fit = 'свободная'
        self.product.care = 'стирка при 30'
        self.product.save()
        self.brand.size_chart = 'M: 96–100'
        self.brand.save()
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, '100% хлопок')
        self.assertContains(response, 'свободная')
        self.assertContains(response, 'стирка при 30')
        self.assertContains(response, '96–100')

    def test_store_info_pages_are_linked(self):
        for name in ('info_delivery', 'info_returns', 'info_contacts'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertContains(self.client.get(reverse('home')), reverse('info_delivery'))
        self.assertContains(self.client.get(reverse('info_delivery')), 'не расчёт')

    def test_demo_carriers_disabled_and_local_zones_active(self):
        from .models import DeliveryMethod
        self.assertFalse(DeliveryMethod.objects.filter(name__contains='(демо)', is_active=True).exists())
        city = DeliveryMethod.objects.get(name='Доставка по городу')
        self.assertTrue(city.is_active)
        self.assertTrue(city.requires_address)
        self.assertEqual(city.eta, '1–3 дня')
        pickup = DeliveryMethod.objects.get(name='Самовывоз из мастерской')
        self.assertFalse(pickup.requires_address)
        self.assertTrue(pickup.pickup_address)

    def test_brand_page_lists_its_products(self):
        response = self.client.get(reverse('brand_storefront', args=[self.brand.slug]))
        self.assertContains(response, self.product.title)
        self.assertContains(response, self.brand.name)


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

    def test_storefront_sets_content_security_policy(self):
        response = self.client.get(reverse('home'))
        self.assertIn("default-src 'self'", response['Content-Security-Policy'])
        self.assertFalse(self.client.get('/admin/login/').has_header('Content-Security-Policy'))


@override_settings(RATELIMIT_ENABLE=False)
class BrandAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'SecurePass813!'
        cls.brand = Brand.objects.create(name='FIRST BRAND', slug='first-brand')
        cls.other_brand = Brand.objects.create(name='OTHER BRAND', slug='other-brand')
        cls.manager_user = User.objects.create_user(username='brand_manager', password=cls.password)
        cls.manager = BrandManager.objects.create(user=cls.manager_user, brand=cls.brand, is_approved=True)
        cls.other_user = User.objects.create_user(username='other_manager', password=cls.password)
        BrandManager.objects.create(user=cls.other_user, brand=cls.other_brand, is_approved=True)
        cls.customer = User.objects.create_user(username='ordinary_customer', password=cls.password)
        cls.product = Product.objects.create(
            brand=cls.brand, title='Own product', description='Own description', price=Decimal('2000.00'),
        )
        cls.other_product = Product.objects.create(
            brand=cls.other_brand, title='Other product', description='Other description', price=Decimal('3000.00'),
        )

    def test_brand_registration_requires_admin_approval(self):
        brand_count = Brand.objects.count()
        response = self.client.post(reverse('brand_register'), {
            'username': 'new_brand_manager',
            'email': 'manager@example.com',
            'password1': 'AnotherSecurePass#831',
            'password2': 'AnotherSecurePass#831',
        })
        self.assertRedirects(response, reverse('brand_pending'))
        manager = BrandManager.objects.get(user__username='new_brand_manager')
        self.assertFalse(manager.is_approved)
        self.assertIsNone(manager.brand)
        self.assertEqual(Brand.objects.count(), brand_count)
        self.assertEqual(self.client.get(reverse('brand_dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('brand_products')).status_code, 403)
        self.assertEqual(self.client.get(reverse('brand_create')).status_code, 403)

    def test_approved_manager_creates_only_one_brand(self):
        user = User.objects.create_user(username='approved_without_brand', password=self.password)
        manager = BrandManager.objects.create(user=user, is_approved=True)
        response = self.client.post(reverse('brand_login'), {
            'username': user.username, 'password': self.password,
        })
        self.assertRedirects(response, reverse('brand_create'))
        response = self.client.post(reverse('brand_create'), {
            'name': 'CREATED BRAND', 'description': 'Created after approval',
        })
        self.assertRedirects(response, reverse('brand_dashboard'))
        manager.refresh_from_db()
        self.assertEqual(manager.brand.name, 'CREATED BRAND')
        response = self.client.post(reverse('brand_create'), {
            'name': 'SECOND BRAND', 'description': 'Not allowed',
        })
        self.assertRedirects(response, reverse('brand_dashboard'))
        self.assertFalse(Brand.objects.filter(name='SECOND BRAND').exists())
        self.assertEqual(Brand.objects.filter(managers__user=user).count(), 1)

    def test_brand_can_have_multiple_managers(self):
        second_user = User.objects.create_user(username='second_brand_manager', password=self.password)
        BrandManager.objects.create(user=second_user, brand=self.brand, is_approved=True)
        self.assertEqual(self.brand.managers.count(), 2)
        self.client.force_login(second_user)
        response = self.client.get(reverse('brand_products'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.title)

    def test_manager_account_is_marked_in_user_admin(self):
        admin = User.objects.create_superuser(username='admin_marker', password=self.password, email='admin@example.com')
        self.client.force_login(admin)
        response = self.client.get('/admin/auth/user/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Менеджер')

    def test_brand_login_redirects_approved_and_pending_managers(self):
        response = self.client.post(reverse('brand_login'), {
            'username': self.manager_user.username, 'password': self.password,
        })
        self.assertRedirects(response, reverse('brand_dashboard'))
        self.client.logout()
        pending_user = User.objects.create_user(username='pending_manager', password=self.password)
        BrandManager.objects.create(user=pending_user, brand=self.brand)
        response = self.client.post(reverse('brand_login'), {
            'username': pending_user.username, 'password': self.password,
        })
        self.assertRedirects(response, reverse('brand_pending'))

    def test_customer_cannot_login_through_brand_form(self):
        response = self.client.post(reverse('brand_login'), {
            'username': self.customer.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не зарегистрирован как менеджер бренда')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_manager_creates_product_only_for_own_brand(self):
        self.client.force_login(self.manager_user)
        form_page = self.client.get(reverse('product_create'))
        self.assertNotIn('brand', form_page.context['form'].fields)
        self.assertNotIn('is_featured', form_page.context['form'].fields)
        response = self.client.post(reverse('product_create'), {
            'brand': self.other_brand.pk,
            'title': 'Manager product',
            'description': 'Created by manager',
            'price': '4500',
            'color': 'Чёрный',
            'is_available': 'on',
            'is_published': 'on',
            'is_featured': 'on',
            **variant_payload(),
        })
        product = Product.objects.get(title='Manager product')
        self.assertRedirects(response, product.get_absolute_url())
        self.assertEqual(product.brand, self.brand)
        self.assertFalse(product.is_featured)

    def test_manager_cannot_change_or_delete_other_brand_product(self):
        self.client.force_login(self.manager_user)
        self.assertEqual(self.client.get(reverse('product_edit', args=[self.other_product.slug])).status_code, 404)
        self.assertEqual(self.client.post(reverse('product_delete', args=[self.other_product.slug])).status_code, 404)
        self.other_product.soft_delete()
        self.assertEqual(self.client.post(reverse('product_restore', args=[self.other_product.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('dashboard_products')).status_code, 403)

    def test_brand_product_list_contains_only_own_products(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('brand_products'))
        self.assertContains(response, self.product.title)
        self.assertNotContains(response, self.other_product.title)
        self.assertContains(response, 'Изменить')
        self.assertContains(response, 'Удалить')

    def test_manager_can_delete_and_restore_own_product(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('product_delete', args=[self.product.slug]))
        self.assertRedirects(response, reverse('brand_products'))
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_deleted)
        response = self.client.post(reverse('product_restore', args=[self.product.slug]))
        self.assertRedirects(response, reverse('product_edit', args=[self.product.slug]))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_deleted)
        self.assertFalse(self.product.is_published)

    def test_brand_deletion_requires_manager_password(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('brand_delete'), {
            'brand_name': self.brand.name,
            'password': 'WrongPassword#831',
        })
        self.assertEqual(response.status_code, 200)
        self.brand.refresh_from_db()
        self.assertFalse(self.brand.is_deleted)

    def test_manager_soft_deletes_brand_and_products(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('brand_delete'), {
            'brand_name': self.brand.name,
            'password': self.password,
        })
        self.assertRedirects(response, reverse('brand_deleted'))
        self.brand.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(self.brand.is_deleted)
        self.assertFalse(self.brand.is_active)
        self.assertTrue(self.product.is_deleted)
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse('brand_products')).status_code, 403)
        self.assertEqual(self.client.get(reverse('brand_deleted')).status_code, 200)

    def test_product_permission_does_not_unlock_other_brands(self):
        from django.contrib.auth.models import Permission
        permission = Permission.objects.get(codename='add_product', content_type__app_label='shop')
        self.manager_user.user_permissions.add(permission)
        self.client.force_login(self.manager_user)
        self.assertNotIn('brand', self.client.get(reverse('product_create')).context['form'].fields)
        self.client.post(reverse('product_create'), {
            'brand': self.other_brand.pk,
            'title': 'Scoped product',
            'description': 'Still own brand',
            'price': '4500',
            'color': 'Чёрный',
            'is_available': 'on',
            'is_published': 'on',
            **variant_payload(),
        })
        product = Product.objects.get(title='Scoped product')
        self.assertEqual(product.brand, self.brand)

    def test_manager_edits_brand_text_not_name(self):
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('brand_edit'), {
            'name': 'HIJACKED',
            'description': 'Новое описание мастерской',
            'size_chart': 'M — грудь 96–100',
        })
        self.assertRedirects(response, reverse('brand_dashboard'))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.name, 'FIRST BRAND')
        self.assertEqual(self.brand.description, 'Новое описание мастерской')
        self.assertIn('96–100', self.brand.size_chart)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('brand_edit')).status_code, 403)
