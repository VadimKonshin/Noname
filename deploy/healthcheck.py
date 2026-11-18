"""Succeed when Django answers on the public host. Used by Docker, not by visitors."""
import os
import sys
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    host = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1').split(',')[0].strip() or '127.0.0.1'
    request = urllib.request.Request(
        'http://127.0.0.1:8000/',
        headers={'Host': host, 'X-Forwarded-Proto': 'https'},
        method='GET',
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(request, timeout=4)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302):
            sys.exit(1)
    except OSError:
        sys.exit(1)


if __name__ == '__main__':
    main()
