from django.utils import timezone
from django.shortcuts import render
from menus.models import Menu

# Create your views here.

def index(requst):
    menus = Menu.objects.all()
    today = timezone.localtime().date
    
    context = {
        "date": today,
        "menus": menus
    }
    
    return render(requst, "menus/index.html", context)

def menu_detail(request, id):
    menu = Menu.objects.get(pk=id)
        
    context = {
        "menu": menu
    }
    return render(request, "menus/menu_detail.html", context)
