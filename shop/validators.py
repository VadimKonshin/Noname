"""Reject product photos that are not images, then store a display-sized WebP."""
from django.core.files.uploadedfile import UploadedFile

from .images import prepare_upload


def validate_product_image(uploaded):
    if not isinstance(uploaded, UploadedFile):
        # full_clean() on an already saved photo passes the stored file and must not rewrite it.
        if getattr(uploaded, '_committed', True):
            return
        inner = getattr(uploaded, 'file', None)
        if inner is None:
            return
        uploaded = inner
    prepare_upload(uploaded)
