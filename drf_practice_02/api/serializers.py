from rest_framework import serializers
from .models import CustomUser, Product, Order, OrderItem

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
    
