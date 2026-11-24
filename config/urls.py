from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django_ratelimit.decorators import ratelimit


def site_verification(request, filename):
    body = (settings.BASE_DIR / filename).read_bytes()
    return HttpResponse(body, content_type='text/html; charset=UTF-8')


urlpatterns = [
    path('yandex_64e4bd290b97df47.html', site_verification, {'filename': 'yandex_64e4bd290b97df47.html'}),
    path('googleaa830e61bb557a81.html', site_verification, {'filename': 'googleaa830e61bb557a81.html'}),
    path('admin/login/', ratelimit(group='admin_login', key='ip', rate='10/5m', method='POST')(admin.site.login)),
    path('admin/', admin.site.urls),
    path('', include('shop.urls')),
]

# Development only: production media/static files should be served by the web server.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
