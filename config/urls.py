from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django_ratelimit.decorators import ratelimit

urlpatterns = [
    path('admin/login/', ratelimit(group='admin_login', key='ip', rate='10/5m', method='POST')(admin.site.login)),
    path('admin/', admin.site.urls),
    path('', include('shop.urls')),
]

# Development only: production media/static files should be served by the web server.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
