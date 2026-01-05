from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views


router = DefaultRouter()
router.register(r"my-profile", views.MyProfileViewSet, basename="my-profile")
router.register(r"users", views.UsersViewSet, basename="users")
router.register(r"products", views.ProductViewSet)
router.register(r"orders", views.OrderViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
]
