from django.urls import path
from posts import views


urlpatterns = [
    path("", views.index, name="home"),
    path("posts/", views.post_list, name="post-list"),
    path("posts/new/", views.post_create, name="post-create"),
    path("posts/detail/<int:id>/", views.post_detail, name="post-detail"),
    path("posts/<int:id>/edit/", views.post_update, name="post-update"),
    path("posts/<int:id>/delete/", views.post_delete, name="post-delete"),
]
