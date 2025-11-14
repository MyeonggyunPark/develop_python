from django.urls import path
from menus import views

urlpatterns = [
    path("menus/", views.index, name="home"),
]
