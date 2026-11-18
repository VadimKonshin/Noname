import re

from django import forms
from .models import Order


class CheckoutForm(forms.Form):
    full_name = forms.CharField(label='Имя и фамилия', max_length=150)
    email = forms.EmailField(label='E-mail')
    phone = forms.CharField(
        label='Контактный телефон', max_length=40,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel', 'placeholder': '+7 999 123-45-67'}),
    )
    city = forms.CharField(label='Город', max_length=120, required=False)
    address = forms.CharField(label='Улица, дом, квартира / пункт выдачи', max_length=250, required=False)
    postal_code = forms.CharField(label='Индекс (если требуется)', max_length=30, required=False)
    comment = forms.CharField(label='Комментарий к заказу', required=False,
                              widget=forms.Textarea(attrs={'rows': 3}), max_length=2000)

    def __init__(self, *args, delivery_method=None, cdek_live=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery_method = delivery_method
        self.cdek_live = cdek_live

    def clean_phone(self):
        return clean_contact_phone(self.cleaned_data['phone'])

    def clean(self):
        data = super().clean()
        if self.cdek_live:
            postal = re.sub(r'\D', '', data.get('postal_code') or '')
            if len(postal) != 6:
                self.add_error('postal_code', 'Для расчёта СДЭК укажите индекс из 6 цифр.')
            else:
                data['postal_code'] = postal
            if not (data.get('city') or '').strip():
                self.add_error('city', 'Укажите город для доставки.')
            return data
        if self.delivery_method and self.delivery_method.requires_address:
            if not data.get('city', '').strip():
                self.add_error('city', 'Укажите город для доставки.')
            if not data.get('address', '').strip():
                self.add_error('address', 'Укажите адрес или пункт выдачи.')
        return data


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ('status', 'tracking_number')
        widgets = {
            'tracking_number': forms.TextInput(attrs={
                'placeholder': 'Например, 10012345678901',
                'autocomplete': 'off',
                'spellcheck': 'false',
            }),
        }

    def clean_status(self):
        status = self.cleaned_data['status']
        if self.instance.pk and self.instance.status == Order.CANCELLED and status != Order.CANCELLED:
            raise forms.ValidationError('Отменённый заказ нельзя открыть повторно.')
        return status

    def clean_tracking_number(self):
        return _clean_tracking_number(self.cleaned_data.get('tracking_number'))


def _clean_tracking_number(value):
    value = ''.join((value or '').split())
    if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9\-]{0,39}', value):
        raise forms.ValidationError('Трек: буквы, цифры и дефис, до 40 символов.')
    return value


class ManagerOrderStatusForm(forms.Form):
    """Brand manager: assembling, handed to the carrier, or shipped. Not cancel."""
    status = forms.ChoiceField(
        label='Статус',
        choices=[('', 'Выберите статус'), *Order.MANAGER_STATUS_CHOICES],
        error_messages={
            'required': 'Выберите: собирается, в службе доставки или отправлен.',
            'invalid_choice': 'Выберите: собирается, в службе доставки или отправлен.',
        },
    )
    tracking_number = forms.CharField(
        label='Трек', required=False, max_length=40,
        widget=forms.TextInput(attrs={
            'placeholder': 'Например, 10012345678901',
            'autocomplete': 'off',
            'spellcheck': 'false',
        }),
    )

    def clean_status(self):
        status = self.cleaned_data.get('status')
        if status not in dict(Order.MANAGER_STATUS_CHOICES):
            raise forms.ValidationError('Выберите: собирается, в службе доставки или отправлен.')
        return status

    def clean_tracking_number(self):
        return _clean_tracking_number(self.cleaned_data.get('tracking_number'))


class OrderLookupForm(forms.Form):
    order_id = forms.CharField(
        label='Номер заказа', max_length=8,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric', 'autocomplete': 'off', 'maxlength': '8',
            'placeholder': '8 цифр',
        }),
    )
    phone = forms.CharField(
        label='Телефон из заказа', max_length=40,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel'}),
    )

    def clean_order_id(self):
        raw = re.sub(r'\s', '', self.cleaned_data['order_id'])
        if not re.fullmatch(r'[1-9]\d{7}', raw):
            raise forms.ValidationError('Номер заказа — 8 цифр. Его дают после оформления, это не порядковый номер.')
        return raw

    def clean_phone(self):
        return clean_contact_phone(self.cleaned_data['phone'])


PHONE_ERROR = 'Введите правильный номер телефона: +7 или 8 и ещё 10 цифр, без букв.'


def clean_contact_phone(value):
    """Russian contact number: +7 or 8, then exactly 10 digits. Letters are rejected."""
    raw = (value or '').strip()
    if not raw or re.search(r'[^0-9+\s()\-]', raw):
        raise forms.ValidationError(PHONE_ERROR)
    compact = re.sub(r'[\s()\-]', '', raw)
    if not re.fullmatch(r'(?:\+7|8)\d{10}', compact):
        raise forms.ValidationError(PHONE_ERROR)
    return compact
