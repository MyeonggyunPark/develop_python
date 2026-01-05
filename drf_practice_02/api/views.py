from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import (
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
    AllowAny
)

from .models import CustomUser, Product, Order
from .serializers import (
    PrivateUserSerializer, 
    PublicUserSerializer, 
    ChangePasswordSerializer, 
    ProductSerializer, 
    OrderSerializer
)


class MyProfileViewSet(ModelViewSet):
    serializer_class = PrivateUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_authenticated:
            return CustomUser.objects.filter(id=user.id)

        return CustomUser.objects.none()


class UsersViewSet(ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = PublicUserSerializer
    lookup_field = "username"

    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "create":
            return PrivateUserSerializer

        if self.action == "retrieve":
            target_username = self.kwargs.get("username")

            if (
                self.request.user.is_authenticated
                and self.request.user.username == target_username
            ):
                return PrivateUserSerializer

        if self.request.user.is_superuser:
            return PrivateUserSerializer

        return PublicUserSerializer

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request, username=None):
        user = self.get_object()

        if request.user != user:
            return Response(
                {"error": "본인의 비밀번호만 변경할 수 있습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ChangePasswordSerializer(data=request.data)

        if serializer.is_valid():
            old_password = serializer.data.get("old_password")
            new_password = serializer.data.get("new_password")

            if not user.check_password(old_password):
                return Response(
                    {"error": "현재 비밀번호가 틀립니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(new_password)
            user.save()
            return Response({"status": "password set"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomLoginView(ObtainAuthToken):
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, created = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "user_id": user.pk,
                "username": user.username,
                "nickname": user.nickname,
                "email": user.email,
            }
        )

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.auth.delete()
        return Response(
            {"message": "Successfully logged out."}, status=status.HTTP_200_OK
        )


class ProductViewSet(ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


class OrderViewSet(ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
