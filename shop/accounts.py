"""Account mail: confirmation, password reset and email change. Tokens are stored as hashes."""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .mail import deliver_mail
from .models import EmailToken, UserProfile

logger = logging.getLogger(__name__)

CONFIRM_HOURS = 24
RESET_HOURS = 1
User = get_user_model()


def user_email_verified(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    profile = UserProfile.objects.filter(user_id=user.pk).only('email_verified_at').first()
    if profile is None:
        return True
    return profile.email_verified_at is not None


def ensure_profile(user):
    profile, _created = UserProfile.objects.get_or_create(
        user=user,
        defaults={'email_verified_at': timezone.now()},
    )
    return profile


def resolve_login_name(identifier):
    """Return the username to authenticate, or an error when the address is not confirmed."""
    identifier = (identifier or '').strip()
    if '@' not in identifier:
        return identifier, None
    users = list(User.objects.filter(email__iexact=identifier)[:5])
    verified = [user for user in users if user.is_active and user_email_verified(user)]
    if len(verified) == 1:
        return verified[0].get_username(), None
    if users and not any(user_email_verified(user) for user in users):
        return None, 'Почта ещё не подтверждена. Войдите логином или откройте ссылку из письма.'
    return identifier, None


def issue_email_token(user, purpose, email, hours):
    EmailToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).delete()
    raw = secrets.token_urlsafe(32)
    EmailToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=_digest(raw),
        email=email.strip().lower(),
        expires_at=timezone.now() + timedelta(hours=hours),
    )
    return raw


def find_email_token(raw, purposes):
    digest = _digest(raw)
    if digest is None:
        return None
    return (EmailToken.objects.select_related('user')
            .filter(token_hash=digest, purpose__in=purposes, used_at__isnull=True, expires_at__gt=timezone.now())
            .first())


def consume_email_token(raw, purposes):
    digest = _digest(raw)
    if digest is None:
        return None
    with transaction.atomic():
        token = (EmailToken.objects.select_for_update()
                 .select_related('user')
                 .filter(token_hash=digest, purpose__in=purposes, used_at__isnull=True, expires_at__gt=timezone.now())
                 .first())
        if token is None:
            return None
        token.used_at = timezone.now()
        token.save(update_fields=['used_at'])
        return token


def apply_email_token(token):
    user = token.user
    profile = ensure_profile(user)
    if token.purpose == EmailToken.CHANGE:
        if User.objects.filter(email__iexact=token.email).exclude(pk=user.pk).exists():
            return 'taken'
        user.email = token.email
        user.save(update_fields=['email'])
        profile.pending_email = ''
        result = 'changed'
    elif (user.email or '').lower() != token.email.lower():
        return 'mismatch'
    else:
        result = 'confirmed'
    profile.email_verified_at = timezone.now()
    profile.save(update_fields=['email_verified_at', 'pending_email'])
    return result


def send_confirmation(user):
    if not user.email:
        return
    raw = issue_email_token(user, EmailToken.CONFIRM, user.email, CONFIRM_HOURS)
    _send_link(user, user.email, raw, 'account_email_confirm', CONFIRM_HOURS, 'NoName: подтвердите почту', 'emails/confirm_email')


def send_email_change(user, email):
    raw = issue_email_token(user, EmailToken.CHANGE, email, CONFIRM_HOURS)
    _send_link(user, email, raw, 'account_email_confirm', CONFIRM_HOURS, 'NoName: подтвердите новую почту', 'emails/change_email')


def send_password_reset(email):
    verified = [user for user in User.objects.filter(email__iexact=email, is_active=True)[:5] if user_email_verified(user)]
    if len(verified) != 1:
        return
    user = verified[0]
    raw = issue_email_token(user, EmailToken.RESET, user.email, RESET_HOURS)
    _send_link(user, user.email, raw, 'password_reset_confirm', RESET_HOURS, 'NoName: новый пароль', 'emails/password_reset')


def _send_link(user, recipient, raw, url_name, hours, subject, template):
    link = f'{settings.SITE_URL}{reverse(url_name, args=[raw])}'
    deliver_mail(
        subject,
        f'{template}.txt',
        f'{template}.html',
        {'username': user.username, 'link': link, 'hours': hours, 'email': recipient},
        [recipient],
    )


def _digest(raw):
    if not raw or len(raw) > 200:
        return None
    return hashlib.sha256(raw.encode()).hexdigest()
