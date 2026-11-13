from django.contrib.auth import views as auth_views
from django.urls import path
from django_ratelimit.decorators import ratelimit
from . import views, commerce_views

login_view = ratelimit(group='login', key='ip', rate='10/5m', method='POST')(
    auth_views.LoginView.as_view(template_name='accounts/login.html', redirect_authenticated_user=True)
)
brand_login_view = ratelimit(group='brand_login', key='ip', rate='10/5m', method='POST')(
    views.BrandLoginView.as_view()
)

urlpatterns = [
    path('', views.home, name='home'),
    path('brand/register/', views.brand_register, name='brand_register'),
    path('brand/login/', brand_login_view, name='brand_login'),
    path('brand/pending/', views.brand_pending, name='brand_pending'),
    path('brand/create/', views.brand_create, name='brand_create'),
    path('brand/edit/', views.brand_edit, name='brand_edit'),
    path('brand/delete/', views.brand_delete, name='brand_delete'),
    path('brand/deleted/', views.brand_deleted, name='brand_deleted'),
    path('brand/', commerce_views.brand_dashboard, name='brand_dashboard'),
    path('brand/orders/', commerce_views.brand_orders, name='brand_orders'),
    path('brand/products/', views.brand_products, name='brand_products'),
    path('catalog/', views.catalog, name='catalog'),
    path('delivery/', views.info_page, {'page': 'delivery'}, name='info_delivery'),
    path('returns/', views.info_page, {'page': 'returns'}, name='info_returns'),
    path('contacts/', views.info_page, {'page': 'contacts'}, name='info_contacts'),
    path('brands/<str:slug>/', views.brand_storefront, name='brand_storefront'),
    path('cart/', commerce_views.cart, name='cart'),
    path('cart/add/<str:slug>/', commerce_views.cart_add, name='cart_add'),
    path('cart/update/', commerce_views.cart_update, name='cart_update'),
    path('cart/delivery/', commerce_views.cart_delivery, name='cart_delivery'),
    path('checkout/', commerce_views.checkout, name='checkout'),
    path('orders/', commerce_views.my_orders, name='my_orders'),
    path('orders/<int:pk>/', commerce_views.order_detail, name='order_detail'),
    path('dashboard/', commerce_views.dashboard, name='dashboard'),
    path('dashboard/orders/', commerce_views.dashboard_orders, name='dashboard_orders'),
    path('dashboard/orders/<int:pk>/status/', commerce_views.change_order_status, name='change_order_status'),
    path('dashboard/products/', commerce_views.dashboard_products, name='dashboard_products'),

    path('catalog/<str:slug>/', views.product_detail, name='product_detail'),
    path('register/', views.register, name='register'),
    path('login/', login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('products/add/', views.product_create, name='product_create'),
    path('products/<str:slug>/edit/', views.product_edit, name='product_edit'),
    path('products/<str:slug>/delete/', commerce_views.product_delete, name='product_delete'),
    path('products/<str:slug>/restore/', commerce_views.product_restore, name='product_restore'),
]
