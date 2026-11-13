from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction
from django.forms import BaseInlineFormSet, inlineformset_factory
from .models import Brand, BrandManager, Product, ProductVariant


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
    class Meta(RegisterForm.Meta):
        fields = ('username', 'email', 'password1', 'password2')

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            BrandManager.objects.create(user=user)
        return user


class BrandAuthenticationForm(AuthenticationForm):
    def clean(self):
        data = super().clean()
        user = self.user_cache
        if user and not user.is_superuser and not BrandManager.objects.filter(user=user).exists():
            raise forms.ValidationError('Этот аккаунт не зарегистрирован как менеджер бренда.', code='not_brand_manager')
        return data


class BrandProfileForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ('description', 'size_chart')
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Коротко расскажите о бренде'}),
            'size_chart': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Например: M — грудь 96–100 см, длина 72 см'}),
        }


class BrandCreateForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ('name', 'description')
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Название вашего бренда'}),
            'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Коротко расскажите о бренде'}),
        }

    def clean_name(self):
        return self.cleaned_data['name'].strip()


class BrandMessageForm(forms.Form):
    sender_name = forms.CharField(label='Имя', max_length=120)
    sender_email = forms.EmailField(label='Почта для ответа')
    body = forms.CharField(
        label='Сообщение',
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 6, 'placeholder': 'Вопрос о размере, наличии или заказе'}),
    )

    def __init__(self, *args, lock_email=False, **kwargs):
        super().__init__(*args, **kwargs)
        if lock_email:
            self.fields['sender_email'].disabled = True
            self.fields['sender_email'].help_text = 'Ответ придёт на почту аккаунта. Подменить её в письме нельзя.'

    def clean_sender_name(self):
        name = ' '.join(self.cleaned_data['sender_name'].split())
        if len(name) < 2:
            raise forms.ValidationError('Напишите имя, чтобы менеджер понял, кто пишет.')
        return name

    def clean_sender_email(self):
        return self.cleaned_data['sender_email'].strip().lower()

    def clean_body(self):
        body = self.cleaned_data['body'].strip()
        if len(body) < 10:
            raise forms.ValidationError('Напишите вопрос чуть подробнее, хотя бы пару слов.')
        return body


class BrandDeleteForm(forms.Form):
    brand_name = forms.CharField(label='Название бренда', max_length=120)
    password = forms.CharField(label='Пароль аккаунта', widget=forms.PasswordInput)

    def __init__(self, *args, user=None, brand=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.brand = brand

    def clean(self):
        data = super().clean()
        if data.get('brand_name') != self.brand.name:
            self.add_error('brand_name', 'Введите название бренда точно как указано на странице.')
        if data.get('password') and not self.user.check_password(data['password']):
            self.add_error('password', 'Неверный пароль.')
        return data


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ('size', 'stock_quantity')
        widgets = {
            'size': forms.TextInput(attrs={'placeholder': 'Например, M'}),
            'stock_quantity': forms.NumberInput(attrs={'min': 0}),
        }

    def clean_size(self):
        size = self.cleaned_data['size'].strip()
        if not size:
            raise forms.ValidationError('Укажите размер.')
        if ':' in size:
            raise forms.ValidationError('Символ : использовать нельзя.')
        return size


class BaseProductVariantFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        sizes = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            sizes.append(form.cleaned_data['size'].casefold())
        if not sizes:
            raise forms.ValidationError('Добавьте хотя бы один размер и укажите его остаток.')
        if len(sizes) != len(set(sizes)):
            raise forms.ValidationError('Не повторяйте одинаковые размеры.')


ProductVariantFormSet = inlineformset_factory(
    Product,
    ProductVariant,
    form=ProductVariantForm,
    formset=BaseProductVariantFormSet,
    extra=1,
    can_delete=True,
)


class ProductForm(forms.ModelForm):
    def __init__(self, *args, managed_brand=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.managed_brand = managed_brand
        self.fields['preorder_days'].required = False
        if managed_brand:
            self.fields.pop('brand')
            self.fields.pop('is_featured')
        else:
            self.fields['brand'].queryset = Brand.objects.filter(is_active=True, is_deleted=False) | Brand.objects.filter(pk=self.instance.brand_id)
            self.fields['brand'].required = True
            self.fields['brand'].empty_label = 'Выберите бренд'

    class Meta:
        model = Product
        fields = ('brand', 'title', 'description', 'composition', 'fit', 'care', 'price', 'cover', 'color', 'is_available', 'allow_preorder', 'preorder_days', 'is_published', 'is_featured')
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Расскажи о ткани, посадке, принте и уходе за вещью…'}),
            'composition': forms.TextInput(attrs={'placeholder': 'Например, 100% хлопок, 240 г/м²'}),
            'fit': forms.TextInput(attrs={'placeholder': 'Например, свободная'}),
            'care': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Стирка, глажка, сушка'}),
            'title': forms.TextInput(attrs={'placeholder': 'Например, ECLIPSE / Oversized Tee'}),
            'price': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'cover': forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
            'preorder_days': forms.NumberInput(attrs={'min': 1}),
        }

    def clean_price(self):
        price = self.cleaned_data['price']
        if price <= 0:
            raise forms.ValidationError('Цена должна быть больше нуля.')
        return price

    def clean_preorder_days(self):
        return self.cleaned_data.get('preorder_days') or 14
