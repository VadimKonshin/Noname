import re

from django import forms
from .models import Order


class CheckoutForm(forms.Form):
    full_name = forms.CharField(label='Имя и фамилия', max_length=150)
    email = forms.EmailField(label='E-mail')
    phone = forms.CharField(label='Контактный телефон', max_length=40)
    city = forms.CharField(label='Город', max_length=120, required=False)
    address = forms.CharField(label='Улица, дом, квартира / пункт выдачи', max_length=250, required=False)
    postal_code = forms.CharField(label='Индекс (если требуется)', max_length=30, required=False)
    comment = forms.CharField(label='Комментарий к заказу', required=False,
                              widget=forms.Textarea(attrs={'rows': 3}), max_length=2000)

    def __init__(self, *args, delivery_method=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery_method = delivery_method

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        digits = re.sub(r'\D', '', value)
        if not 10 <= len(digits) <= 15:
            raise forms.ValidationError('Введите телефон: от 10 до 15 цифр, можно с +, пробелами и скобками.')
        return value

    def clean(self):
        data = super().clean()
        if self.delivery_method and self.delivery_method.requires_address:
            if not data.get('city', '').strip():
                self.add_error('city', 'Укажите город для доставки.')
            if not data.get('address', '').strip():
                self.add_error('address', 'Укажите адрес или пункт выдачи.')
        return data


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ('status',)

    def clean_status(self):
        status = self.cleaned_data['status']
        if self.instance.pk and self.instance.status == Order.CANCELLED and status != Order.CANCELLED:
            raise forms.ValidationError('Отменённый заказ нельзя открыть повторно.')
        return status
