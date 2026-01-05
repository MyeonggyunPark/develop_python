from rest_framework import serializers
from .models import CustomUser, Product, Order, OrderItem


class PrivateUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'password', 'nickname', 'email', 'gender', 'last_login', 'date_joined']
        read_only_fields = ['last_login', 'date_joined']

    def create(self, validated_data):
        password = validated_data.pop("password")

        user = CustomUser(**validated_data)

        user.set_password(password)

        user.save()

        return user


class PublicUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ["id", "username", "nickname"]


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)


class ProductSerializer(serializers.ModelSerializer):

    class Meta:
        model = Product
        fields = ['id', 'name', 'price', 'stock']
        
    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price must be a positive value.")
        return value

class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    orderitem_total_price = serializers.DecimalField(source="total_price", max_digits=10, decimal_places=2, read_only=True)
    class Meta:
        model = OrderItem
        fields = ['product_name', 'quantity', 'orderitem_total_price']

class OrderSerializer(serializers.ModelSerializer):
    order_items = OrderItemSerializer(many=True, read_only=True)
    order_total_price = serializers.SerializerMethodField()
    
    class Meta:
        model = Order
        fields = ["user", "order_id", "status", "created_at", "order_items", "order_total_price"]
    
    def get_order_total_price(self, obj):
        return sum([item.total_price for item in obj.order_items.all()])    
