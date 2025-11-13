from django.shortcuts import render
from django.http import HttpResponse

# Create your views here.

def index(requst):
    return HttpResponse("Welcome! Restaurant OPEN!")