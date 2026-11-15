"""Database, cache and TLS readiness. These do not need a running Postgres or Redis."""
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
