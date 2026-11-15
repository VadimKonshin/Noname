from django.conf import settings
from django.core.management.base import BaseCommand

from shop.tls import OUTBOUND_HOSTS, certificate_summary, peer_certificate, site_tls_notes


class Command(BaseCommand):
    help = 'Показать готовность HTTPS магазина и проверить сертификаты исходящих служб.'

    def handle(self, *args, **options):
        for line in site_tls_notes(
            settings.DEBUG,
            settings.SITE_URL,
            settings.SECURE_SSL_REDIRECT,
            bool(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None)),
        ):
            self.stdout.write(line)
        for host in OUTBOUND_HOSTS:
            try:
                summary = certificate_summary(host, peer_certificate(host))
            except Exception as exc:
                self.stdout.write(f'{host}: сертификат не проверен ({exc.__class__.__name__})')
                continue
            subject = summary['subject'] or 'имя не указано'
            self.stdout.write(f"{host}: {subject}, действует до {summary['not_after']}")
