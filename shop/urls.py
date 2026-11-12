from django.contrib.auth import views as auth_views
from django.urls import path
from django_ratelimit.decorators import ratelimit
from . import views, commerce_views

login_view = ratelimit(group='login', key='ip', rate='10/5m', method='POST')(
    auth_views.LoginView.as_view(template_name='accounts/login.html', redirect_authenticated_user=True)
)

urlpatterns = [
    path('', views.home, name='home'),
    path('catalog/', views.catalog, name='catalog'),
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
