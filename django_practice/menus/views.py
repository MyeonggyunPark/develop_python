from django.utils import timezone
from django.shortcuts import render

# Create your views here.

def index(requst):
    today = timezone.localtime().date
    context = {"date": today}
    
    return render(requst, "menus/index.html", context)

def menu_detail(request, menu_name):

    context = {}
    if menu_name == "chicken":
        context["name"] = menu_name
        context["features"] = "Crispy, Juicy chicken with Soy sauce"
        context["description"] = "A taste you’ve never experienced before. Is it ribs or chicken?"
        context["price"] = "35"
        
    return render(request, "menus/menu_detail.html", context)
