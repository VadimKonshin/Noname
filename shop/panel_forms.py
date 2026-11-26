from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Brand, BrandManager, DeliveryMethod, UserProfile


User = get_user_model()


class PanelBrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = (
            'name', 'description', 'size_chart',
            'offers_cdek', 'offers_courier', 'offers_pickup', 'pickup_address',
            'is_active', 'sort_order',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'size_chart': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Каждая строка — размер. Например: M — грудь 96–100 см'}),
            'pickup_address': forms.TextInput(attrs={'placeholder': 'Город, улица, как найти мастерскую'}),
        }

    def clean_name(self):
        return self.cleaned_data['name'].strip()

    def clean(self):
        cleaned = super().clean()
        address = (cleaned.get('pickup_address') or '').strip()
        pickup = cleaned.get('offers_pickup')
        needs_address = pickup
        if self.instance.pk and self.instance.products.filter(offers_pickup=True, is_deleted=False).exists():
            needs_address = True
        if needs_address and not address:
            self.add_error('pickup_address', 'Для самовывоза нужен адрес мастерской.')
        if not cleaned.get('offers_cdek') and not cleaned.get('offers_courier') and not pickup:
            raise forms.ValidationError('Оставьте хотя бы один способ: СДЭК, курьера или самовывоз.')
        cleaned['pickup_address'] = address
        return cleaned


class PanelUserForm(forms.Form):
    username = forms.CharField(label='Логин', max_length=150)
    email = forms.EmailField(label='E-mail')
    first_name = forms.CharField(label='Имя', max_length=150, required=False)
    last_name = forms.CharField(label='Фамилия', max_length=150, required=False)
    password = forms.CharField(
        label='Пароль', required=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Для нового аккаунта пароль обязателен. При правке пустое поле оставляет прежний.',
    )
    is_active = forms.BooleanField(label='Аккаунт включён', required=False, initial=True)
    is_staff = forms.BooleanField(label='Права администратора магазина', required=False)
    is_superuser = forms.BooleanField(label='Суперпользователь', required=False)
    email_verified = forms.BooleanField(label='Почта подтверждена', required=False, initial=True)

    def __init__(self, *args, instance=None, actor=None, **kwargs):
        self.instance = instance
        self.actor = actor
        super().__init__(*args, **kwargs)
        if instance is None:
            return
        self.fields['username'].initial = instance.username
        self.fields['email'].initial = instance.email
        self.fields['first_name'].initial = instance.first_name
        self.fields['last_name'].initial = instance.last_name
        self.fields['is_active'].initial = instance.is_active
        self.fields['is_staff'].initial = instance.is_staff
        self.fields['is_superuser'].initial = instance.is_superuser
        try:
            verified = instance.profile.email_verified_at is not None
        except UserProfile.DoesNotExist:
            verified = True
        self.fields['email_verified'].initial = verified

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        taken = User.objects.filter(username__iexact=username)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError('Такой логин уже есть.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        taken = User.objects.filter(email__iexact=email)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError('Этот e-mail уже зарегистрирован.')
        return email

    def clean_password(self):
        password = self.cleaned_data.get('password') or ''
        if self.instance is None and not password:
            raise forms.ValidationError('Задайте пароль для нового аккаунта.')
        if password:
            try:
                validate_password(password, self.instance)
            except ValidationError as exc:
                raise forms.ValidationError(exc.messages) from exc
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_superuser'):
            cleaned['is_staff'] = True
        if self.instance is None or self.actor is None:
            return cleaned
        if self.instance.pk == self.actor.pk and (not cleaned.get('is_superuser') or not cleaned.get('is_active')):
            raise forms.ValidationError('Свой аккаунт нельзя выключить и нельзя снять с него права суперпользователя.')
        if self.instance.is_superuser and not cleaned.get('is_superuser'):
            if not User.objects.filter(is_superuser=True).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('Должен остаться хотя бы один суперпользователь.')
        return cleaned


class PanelManagerForm(forms.Form):
    user = forms.ModelChoiceField(label='Пользователь', queryset=User.objects.none())
    brand = forms.ModelChoiceField(label='Бренд', queryset=Brand.objects.none(), required=False)
    is_approved = forms.BooleanField(label='Подтверждён администратором', required=False)
    is_staff = forms.BooleanField(label='Права администратора магазина', required=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        self.fields['brand'].queryset = Brand.objects.order_by('sort_order', 'name')
        self.fields['brand'].empty_label = 'Бренд ещё не создан'
        if instance is None:
            self.fields['user'].queryset = User.objects.filter(brand_manager__isnull=True).order_by('username')
            return
        self.fields['user'].queryset = User.objects.filter(pk=instance.user_id)
        self.fields['user'].initial = instance.user_id
        self.fields['user'].disabled = True
        self.fields['brand'].initial = instance.brand_id
        self.fields['is_approved'].initial = instance.is_approved
        self.fields['is_staff'].initial = instance.user.is_staff
        if instance.user.is_superuser:
            self.fields['is_staff'].disabled = True
            self.fields['is_staff'].help_text = 'Суперпользователь. Этот флажок его не меняет.'

    def clean_user(self):
        user = self.cleaned_data.get('user')
        if user is None:
            return user
        taken = BrandManager.objects.filter(user=user)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError('У этого аккаунта уже есть заявка менеджера.')
        return user


class PanelDeliveryForm(forms.ModelForm):
    class Meta:
        model = DeliveryMethod
        fields = (
            'name', 'code', 'description', 'eta', 'price',
            'requires_address', 'pickup_address', 'is_active', 'sort_order',
        )
        widgets = {
            'description': forms.TextInput(),
            'pickup_address': forms.TextInput(),
        }
