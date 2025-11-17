from django.urls import path
from posts import views


urlpatterns = [
    path("", views.index, name="home"),
    path("posts/", views.PostListView.as_view(), name="post-list"),
    path("posts/new/", views.PostCreateView.as_view(), name="post-create"),
    path("posts/detail/<int:id>/", views.PostDetailView.as_view(), name="post-detail"),
    path("posts/<int:id>/edit/", views.PostUpdateView.as_view(), name="post-update"),
    path("posts/<int:id>/delete/", views.PostDeleteView.as_view(), name="post-delete"),
]
