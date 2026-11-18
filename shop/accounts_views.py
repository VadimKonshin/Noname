"""Confirmation, password reset and email change pages."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from .access import brand_account_home, get_brand_manager
from .accounts import (
    apply_email_token, clear_console_link, console_mail_link, consume_email_token, ensure_profile,
    find_email_token, remember_console_link, send_confirmation, send_email_change, send_password_reset,
    show_console_mail, uses_console_mail,
)
from .forms import EmailChangeForm, PasswordResetRequestForm, StoreSetPasswordForm
from .models import EmailToken

logger = logging.getLogger(__name__)


def _mail_result(sent, local):
    if show_console_mail():
        return local
    if uses_console_mail():
        return 'Письмо сейчас не отправляется.'
    return sent


def _resent_message(sent):
    return _mail_result(
        f'{sent} Предыдущая больше не действует.',
        'Почтовый сервер не подключён: новая ссылка напечатана в консоли сервера и показана на этой странице. Предыдущая больше не действует.',
    )


TOKEN_RESULTS = {
    'confirmed': 'Почта подтверждена.',
    'changed': 'Новая почта сохранена. Входить можно и по ней.',
    'taken': 'Эта почта уже занята другим аккаунтом. Укажите другую.',
    'mismatch': 'Ссылка больше не совпадает с почтой аккаунта.',
}


@login_required
@ratelimit(group='email_change', key='ip', rate='5/h', method='POST')
@require_http_methods(['GET', 'POST'])
def account_email(request):
    profile = ensure_profile(request.user)
    form = EmailChangeForm(request.POST if request.method == 'POST' else None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        profile.pending_email = email
        profile.save(update_fields=['pending_email'])
        try:
            link = send_email_change(request.user, email)
        except Exception:
            logger.exception('Email change mail failed')
            messages.warning(request, 'Новая почта ещё не сохранена, и письмо не ушло. Запросите ссылку ещё раз.')
        else:
            remember_console_link(request, link)
            messages.success(request, _mail_result(
                'Ссылка для новой почты отправлена. Прежняя почта работает, пока вы не нажмёте кнопку в письме.',
                'Почтовый сервер не подключён: ссылка для новой почты напечатана в консоли сервера и показана на этой странице. Прежняя почта работает, пока вы не нажмёте кнопку.',
            ))
        return redirect('account_email')
    return render(request, 'accounts/email.html', {
        'form': form,
        'profile': profile,
        'manager': get_brand_manager(request.user, approved_only=False),
        'console_mail_link': console_mail_link(request),
    })


@login_required
@ratelimit(group='email_resend', key='ip', rate='5/h', method='POST')
@require_POST
def account_email_resend(request):
    profile = ensure_profile(request.user)
    try:
        if profile.pending_email:
            link = send_email_change(request.user, profile.pending_email)
            remember_console_link(request, link)
            messages.success(request, _resent_message('Ссылка для новой почты отправлена ещё раз.'))
        elif profile.email_verified_at is None and request.user.email:
            link = send_confirmation(request.user)
            remember_console_link(request, link)
            messages.success(request, _resent_message('Ссылка отправлена ещё раз.'))
        elif profile.email_verified_at is None:
            messages.error(request, 'У аккаунта нет почты. Укажите её в форме ниже.')
        else:
            messages.info(request, 'Почта уже подтверждена.')
    except Exception:
        logger.exception('Confirmation resend failed')
        messages.warning(request, 'Письмо не отправилось. Попробуйте ещё раз чуть позже.')
    return redirect('account_email')


@ratelimit(group='email_confirm', key='ip', rate='30/h', method=['GET', 'POST'])
@require_http_methods(['GET', 'POST'])
def account_email_confirm(request, token):
    found = find_email_token(token, (EmailToken.CONFIRM, EmailToken.CHANGE))
    if found is None:
        return render(request, 'accounts/token_invalid.html', {'purpose': 'почты'})
    if request.method != 'POST':
        return render(request, 'accounts/email_confirm.html', {
            'email': found.email,
            'changing': found.purpose == EmailToken.CHANGE,
        })
    with transaction.atomic():
        taken = consume_email_token(token, (EmailToken.CONFIRM, EmailToken.CHANGE))
        if taken is None:
            return render(request, 'accounts/token_invalid.html', {'purpose': 'почты'})
        result = apply_email_token(taken)
    text = TOKEN_RESULTS[result]
    if result in ('confirmed', 'changed'):
        clear_console_link(request)
        messages.success(request, text)
    else:
        messages.error(request, text)
    if request.user.is_authenticated and request.user.pk == taken.user_id:
        return redirect(brand_account_home(request.user) if result in ('confirmed', 'changed') else 'account_email')
    return redirect('login')


@ratelimit(group='password_reset', key='ip', rate='5/h', method='POST')
@require_http_methods(['GET', 'POST'])
def password_reset(request):
    form = PasswordResetRequestForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            link = send_password_reset(form.cleaned_data['email'])
        except Exception:
            logger.exception('Password reset mail failed')
            messages.error(request, 'Письмо не отправилось. Попробуйте ещё раз чуть позже.')
        else:
            remember_console_link(request, link)
            messages.success(request, _mail_result(
                'Если эта почта подтверждена, мы отправили ссылку для нового пароля. Она действует один час и сгорает после смены пароля.',
                'Если эта почта подтверждена, ссылка напечатана в консоли сервера и показана на этой странице. Она действует один час и сгорает после смены пароля.',
            ))
        return redirect('password_reset')
    return render(request, 'accounts/password_reset.html', {
        'form': form,
        'console_mail_link': console_mail_link(request),
    })


@ratelimit(group='password_reset_confirm', key='ip', rate='10/h', method='POST')
@require_http_methods(['GET', 'POST'])
def password_reset_confirm(request, token):
    found = find_email_token(token, (EmailToken.RESET,))
    if found is None:
        return render(request, 'accounts/token_invalid.html', {'purpose': 'пароля'})
    form = StoreSetPasswordForm(found.user, request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            taken = consume_email_token(token, (EmailToken.RESET,))
            if taken is None:
                return render(request, 'accounts/token_invalid.html', {'purpose': 'пароля'})
            form.user = taken.user
            form.save()
        clear_console_link(request)
        messages.success(request, 'Пароль обновлён. Войдите с новым паролем.')
        return redirect('login')
    return render(request, 'accounts/password_reset_confirm.html', {'form': form})
