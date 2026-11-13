from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction
from .models import Brand, BrandManager, Product


class RegisterForm(UserCreationForm):
    email = forms.EmailField(label='E-mail', required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'password1', 'password2')
        labels = {'username': 'Логин'}

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Этот e-mail уже зарегистрирован.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class BrandManagerRegisterForm(RegisterForm):
    brand = forms.ModelChoiceField(
        label='Бренд',
        queryset=Brand.objects.filter(is_active=True),
        empty_label='Выберите бренд',
    )

    class Meta(RegisterForm.Meta):
        fields = ('username', 'email', 'brand', 'password1', 'password2')

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            BrandManager.objects.create(user=user, brand=self.cleaned_data['brand'])
        return user


class BrandAuthenticationForm(AuthenticationForm):
    def clean(self):
        data = super().clean()
        user = self.user_cache
        if user and not user.is_superuser and not BrandManager.objects.filter(user=user).exists():
            raise forms.ValidationError('Этот аккаунт не зарегистрирован как менеджер бренда.', code='not_brand_manager')
        return data


class ProductForm(forms.ModelForm):
    def __init__(self, *args, managed_brand=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.managed_brand = managed_brand
        if managed_brand:
            self.fields.pop('brand')
            self.fields.pop('is_featured')
        else:
            self.fields['brand'].queryset = Brand.objects.filter(is_active=True) | Brand.objects.filter(pk=self.instance.brand_id)
            self.fields['brand'].required = True
            self.fields['brand'].empty_label = 'Выберите бренд'

    class Meta:
        model = Product
        fields = ('brand', 'title', 'description', 'price', 'cover', 'sizes', 'color', 'is_available', 'is_published', 'is_featured')
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Расскажи о ткани, посадке, принте и уходе за вещью…'}),
            'title': forms.TextInput(attrs={'placeholder': 'Например, ECLIPSE / Oversized Tee'}),
            'price': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'cover': forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
            'sizes': forms.TextInput(attrs={'placeholder': 'S, M, L, XL'}),
        }

    def clean_sizes(self):
        sizes = [part.strip() for part in self.cleaned_data['sizes'].split(',') if part.strip()]
        if not sizes:
            raise forms.ValidationError('Укажите хотя бы один размер.')
        if any(len(part) > 40 or ':' in part for part in sizes):
            raise forms.ValidationError('Длина размера — максимум 40 символов; символ : использовать нельзя.')
        if len(set(sizes)) != len(sizes):
            raise forms.ValidationError('Не повторяйте одинаковые размеры.')
        return ', '.join(sizes)

    def clean_price(self):
        price = self.cleaned_data['price']
        if price <= 0:
            raise forms.ValidationError('Цена должна быть больше нуля.')
        return price
