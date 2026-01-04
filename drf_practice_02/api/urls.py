from django.urls import path
from . import views


urlpatterns = [
    path("products/", views.ProductList.as_view()),
    path("products/<int:pk>/", views.ProductDetail.as_view()),
    path("orders/", views.OrderList.as_view()),
]
