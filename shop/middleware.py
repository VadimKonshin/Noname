from django.conf import settings
from django.http import Http404


class ClosedAdminMiddleware:
    """The staff UI is not on the public site, so /admin/ cannot be used to guess a password."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if settings.ADMIN_CLOSED and (path == '/admin' or path.startswith('/admin/')):
            raise Http404()
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Keep storefront scripts and styles on this origin. Django Admin stays untouched."""

    POLICY = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith('/admin'):
            response.headers['Content-Security-Policy'] = self.POLICY
        return response
