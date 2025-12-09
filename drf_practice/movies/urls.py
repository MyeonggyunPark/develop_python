from django.urls import path
from . import views

urlpatterns = [
    path("", views.movie_list, name="movie_list"),
    path("<int:pk>/", views.movie_detail, name="movie_detail"),
    path("<int:pk>/reviews/", views.review_list, name="review_list"),
]
