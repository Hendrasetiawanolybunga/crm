from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('products/', views.products, name='products'),

    # Auth
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Cart
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:product_id>/', views.add_to_cart, name='cart_add'),
    path('cart/remove/<int:product_id>/', views.remove_from_cart, name='cart_remove'),
    path('cart/update/', views.update_cart, name='cart_update'),
    path('checkout/', views.checkout, name='checkout'),
    path('orders/<int:order_id>/pay/', views.payment_upload, name='payment_upload'),
    path('products/<int:product_id>/', views.product_detail, name='product_detail'),

    # Account
    path('account/manage/', views.account_manage, name='account_manage'),

    # Orders
    path('orders/', views.order_history, name='order_history'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/feedback/', views.submit_feedback, name='submit_feedback'),
]
