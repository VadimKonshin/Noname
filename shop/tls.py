"""What can be checked before the shop has a public name and a proxy."""
import socket
import ssl

import certifi

OUTBOUND_HOSTS = ('api.cdek.ru', 'api.edu.cdek.ru', 'api.telegram.org')


def peer_certificate(host, port=443, timeout=5):
    context = ssl.create_default_context(cafile=certifi.where())
    raw = socket.create_connection((host, port), timeout)
    try:
        with context.wrap_socket(raw, server_hostname=host) as secure:
            return secure.getpeercert()
    except Exception:
        raw.close()
        raise


def certificate_summary(host, cert):
    subject = ''
    for item in cert.get('subject', ()):
        for key, value in item:
            if key == 'commonName':
                subject = value
    return {'host': host, 'subject': subject, 'not_after': cert.get('notAfter', '')}


def site_tls_notes(debug, site_url, ssl_redirect, trust_proxy):
    notes = []
    if debug:
        notes.append('Локальный сайт открыт по HTTP: Django не выпускает и не проверяет сертификат самого магазина.')
    elif not str(site_url).lower().startswith('https://'):
        notes.append('DJANGO_DEBUG=0, но DJANGO_SITE_URL не начинается с https://.')
    else:
        notes.append('Сайт помечен как HTTPS. Сертификат выпускает прокси перед Django, не сам Django.')
    if ssl_redirect and debug:
        notes.append('Редирект на HTTPS включён при отладке. Для локального runserver его нужно выключить.')
    elif not debug and ssl_redirect:
        notes.append('Редирект на HTTPS включён.')
    if not debug and not trust_proxy:
        notes.append('DJANGO_TRUST_X_FORWARDED_PROTO выключен: заголовок прокси сам по себе не считается HTTPS.')
    notes.append('Публичный сертификат домена отсюда не выпускается: нет имени сервера и обратного прокси.')
    return notes
