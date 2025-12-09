from django.contrib import admin
from .models import Movie

# Register your models here.

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("name", "opening_date", "running_time")
    ordering = ("-opening_date",)