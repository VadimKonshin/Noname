"""Database, cache and TLS readiness. These do not need a running Postgres or Redis."""
import importlib.util
import io
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from config.runtime import cache_settings, database_settings
from shop.tls import certificate_summary, site_tls_notes


class RuntimeSettingsTests(SimpleTestCase):
    def test_sqlite_stays_the_default(self):
        config = database_settings({}, Path('/tmp/noname'))
        self.assertEqual(config['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(config['default']['NAME'], Path('/tmp/noname/db.sqlite3'))

    def test_postgres_settings_require_a_name_and_user(self):
        with self.assertRaises(RuntimeError):
            database_settings({'DJANGO_DB_ENGINE': 'postgresql'}, Path('/tmp/noname'))
        config = database_settings({
            'DJANGO_DB_ENGINE': 'postgresql',
            'DJANGO_DB_NAME': 'noname',
            'DJANGO_DB_USER': 'noname',
            'DJANGO_DB_PASSWORD': 'secret',
            'DJANGO_DB_HOST': '127.0.0.1',
            'DJANGO_DB_PORT': '5432',
        }, Path('/tmp/noname'))
        self.assertEqual(config['default']['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['default']['NAME'], 'noname')
        self.assertEqual(config['default']['PASSWORD'], 'secret')
        self.assertEqual(config['default']['CONN_MAX_AGE'], 60)

    def test_cache_is_local_until_a_redis_url_is_set(self):
        local = cache_settings({})
        self.assertEqual(local['default']['BACKEND'], 'django.core.cache.backends.locmem.LocMemCache')
        shared = cache_settings({'DJANGO_CACHE_URL': 'redis://127.0.0.1:6379/1'})
        self.assertEqual(shared['default']['BACKEND'], 'django.core.cache.backends.redis.RedisCache')
        self.assertEqual(shared['default']['LOCATION'], 'redis://127.0.0.1:6379/1')
        with self.assertRaises(RuntimeError):
            cache_settings({'DJANGO_CACHE_URL': 'http://127.0.0.1:11211'})

    def test_local_site_does_not_pretend_to_have_a_certificate(self):
        notes = site_tls_notes(True, 'http://127.0.0.1:8000', False, False)
        self.assertIn('не выпускает', notes[0])
        secure = site_tls_notes(False, 'https://shop.example', True, True)
        self.assertIn('прокси', secure[0])

    def test_certificate_summary_reads_the_common_name(self):
        summary = certificate_summary('api.cdek.ru', {
            'subject': ((('commonName', 'api.cdek.ru'),),),
            'notAfter': 'Jun  1 00:00:00 2027 GMT',
        })
        self.assertEqual(summary['subject'], 'api.cdek.ru')
        self.assertIn('2027', summary['not_after'])

    @override_settings(DEBUG=True, SITE_URL='http://127.0.0.1:8000', SECURE_SSL_REDIRECT=False)
    def test_ssl_status_checks_outbound_certificates_without_issuing_one(self):
        cert = {'subject': ((('commonName', 'api.cdek.ru'),),), 'notAfter': 'Jun  1 00:00:00 2027 GMT'}
        with patch('shop.management.commands.ssl_status.peer_certificate', return_value=cert):
            out = StringIO()
            call_command('ssl_status', stdout=out)
        text = out.getvalue()
        self.assertIn('не выпускает', text)
        self.assertIn('api.cdek.ru', text)
        self.assertIn('2027', text)
        self.assertIn('Публичный сертификат', text)

    def test_env_file_fills_only_blank_allowed_keys(self):
        from config.settings import load_env_file

        keys = ('FOO', 'DJANGO_SITE_URL', 'DJANGO_DB_PASSWORD', 'DJANGO_DB_NAME')
        saved = {key: os.environ.get(key) for key in keys}
        try:
            for key in keys:
                os.environ.pop(key, None)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / '.env'
                path.write_text(
                    'FOO=bar\n'
                    'export DJANGO_DB_NAME=noname\n'
                    'DJANGO_DB_PASSWORD="a b"\n'
                    'DJANGO_SITE_URL=https://shop.noname.test\n',
                    encoding='utf-8',
                )
                load_env_file(path)
                self.assertNotIn('FOO', os.environ)
                self.assertEqual(os.environ['DJANGO_DB_NAME'], 'noname')
                self.assertEqual(os.environ['DJANGO_DB_PASSWORD'], 'a b')
                self.assertEqual(os.environ['DJANGO_SITE_URL'], 'https://shop.noname.test')
                os.environ['DJANGO_SITE_URL'] = 'https://already.example'
                load_env_file(path)
                self.assertEqual(os.environ['DJANGO_SITE_URL'], 'https://already.example')
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_client_ip_uses_the_forwarded_address(self):
        from config.settings import client_ip

        class Request:
            META = {'HTTP_X_FORWARDED_FOR': '[2001:db8::1]'}

        class V4:
            META = {'HTTP_X_FORWARDED_FOR': '203.0.113.5, 198.51.100.10'}

        self.assertEqual(client_ip(Request()), '2001:db8::1')
        self.assertEqual(client_ip(V4()), '198.51.100.10')

    def _deploy(self, filename):
        path = Path(__file__).resolve().parents[1] / 'deploy' / filename
        spec = importlib.util.spec_from_file_location(f'deploy_{path.stem}', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_server_env_rejects_the_example_and_accepts_a_real_host(self):
        envfile = self._deploy('envfile.py')
        example = Path(__file__).resolve().parents[1] / 'deploy' / '.env.example'
        parsed = envfile.read_env(example)
        self.assertEqual(parsed['DJANGO_DEFAULT_FROM_EMAIL'], 'NoName <hello@shop.example.com>')
        errors, _warnings = envfile.production_issues(parsed)
        self.assertTrue(errors)

        ready = {
            'DJANGO_DEBUG': '0',
            'DJANGO_SECRET_KEY': 'a' * 50,
            'DJANGO_ALLOWED_HOSTS': 'shop.noname.test',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://shop.noname.test',
            'DJANGO_SECURE_SSL_REDIRECT': '1',
            'DJANGO_TRUST_X_FORWARDED_PROTO': '1',
            'DJANGO_DB_ENGINE': 'postgresql',
            'DJANGO_DB_NAME': 'noname',
            'DJANGO_DB_USER': 'noname',
            'DJANGO_DB_PASSWORD': 'dbsecretvalue1',
            'DJANGO_DB_HOST': 'postgres',
            'REDIS_PASSWORD': 'redisecretvalue1',
            'DJANGO_SITE_URL': 'https://shop.noname.test',
            'SITE_ADDRESS': 'shop.noname.test',
            'ACME_EMAIL': 'admin@noname.test',
            'TELEGRAM_BOT_TOKEN': '123456:AAExampleToken',
            'TELEGRAM_BOT_USERNAME': '@NoNameBot',
            'DJANGO_EMAIL_HOST': 'smtp.noname.test',
            'DJANGO_ORDER_EMAIL': 'admin@noname.test',
            'DJANGO_DEFAULT_FROM_EMAIL': 'NoName <hello@shop.noname.test>',
        }
        errors, warnings = envfile.production_issues(ready)
        self.assertEqual(errors, [])
        self.assertTrue(any('CDEK' in warning for warning in warnings))
        ready['DJANGO_DB_HOST'] = '127.0.0.1'
        errors, _warnings = envfile.production_issues(ready)
        self.assertTrue(any('DJANGO_DB_HOST' in error for error in errors))
        ready['DJANGO_DB_HOST'] = 'postgres'
        ready.update({
            'SITE_ADDRESS': ':80',
            'DJANGO_ALLOWED_HOSTS': '92.118.88.34',
            'DJANGO_SITE_URL': 'http://92.118.88.34',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'http://92.118.88.34',
            'DJANGO_SECURE_SSL_REDIRECT': '0',
            'DJANGO_SECURE_HSTS_SECONDS': '0',
        })
        errors, _warnings = envfile.production_issues(ready)
        self.assertTrue(any(':80' in error for error in errors))
        ready.update({
            'SITE_ADDRESS': '92.118.88.34',
            'DJANGO_ALLOWED_HOSTS': '92.118.88.34',
            'DJANGO_SITE_URL': 'https://92.118.88.34',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://92.118.88.34',
            'DJANGO_SECURE_SSL_REDIRECT': '1',
            'ACME_PROFILE': 'shortlived',
        })
        errors, _warnings = envfile.production_issues(ready)
        self.assertEqual(errors, [])

    def test_media_archive_keeps_files_inside_the_directory(self):
        archive = self._deploy('media_archive.py')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'media'
            root.mkdir()
            photo = root / 'products' / 'cover.webp'
            photo.parent.mkdir()
            photo.write_bytes(b'webp')
            (root / '.DS_Store').write_bytes(b'no')
            outside = base / 'secret.txt'
            outside.write_text('secret', encoding='utf-8')
            (root / 'leak').symlink_to(outside)
            blob = io.BytesIO()
            self.assertEqual(archive.pack(root, blob), 1)
            blob.seek(0)
            restored = base / 'restored'
            self.assertEqual(archive.unpack(blob, restored), 1)
            self.assertEqual((restored / 'products' / 'cover.webp').read_bytes(), b'webp')
            with self.assertRaises(ValueError):
                archive.member_destination(restored, '../secret.txt')
