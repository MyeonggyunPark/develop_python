from django.urls import path
from menus import views

urlpatterns = [
    path("menus/", views.index, name="home"),
    path("detail/<str:menu_name>/", views.menu_detail, name="menu-detail"),
]
