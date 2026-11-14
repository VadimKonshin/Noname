from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import UserProfile


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, raw=False, **kwargs):
    """Existing and staff-created accounts stay confirmed. Registration clears that afterwards."""
    if raw or not created:
        return
    UserProfile.objects.get_or_create(
        user=instance,
        defaults={'email_verified_at': timezone.now()},
    )
