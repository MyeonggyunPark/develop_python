from django.utils import timezone
from django.shortcuts import render

# Create your views here.

def index(requst):
    today = timezone.localtime().date
    context = {"date": today}
    
    return render(requst, "menus/index.html", context)