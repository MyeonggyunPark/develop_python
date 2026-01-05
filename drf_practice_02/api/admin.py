from django.contrib import admin
from .models import CustomUser, Product, Order, OrderItem

@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'email', 'gender', 'is_staff')
    search_fields = ('nickname', 'email', 'gender')
    
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'stock', 'in_stock', 'created_at', 'updated_at')
    search_fields = ('name',)
    list_filter = ('created_at', 'updated_at')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'user', 'status', 'created_at', 'updated_at')
    search_fields = ('order_id', 'user__username')
    list_filter = ('status', 'created_at', 'updated_at')

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'total_price')
    search_fields = ('order__order_id', 'product__name')
