from django.shortcuts import render
from .models import Post
from .forms import Postform

# Create your views here.

def index(request):
    return render(request, "posts/index.html")


def post_list(request):
    
    posts = Post.objects.all()
    context = {
        "posts": posts
    }
    
    return render(request, "posts/post_list.html", context)


def post_detail(request, id):
    post = Post.objects.get(id=id)
    context = {
        "post": post
    }
    
    return render(request, "posts/post_detail.html", context)


def post_create(request):
    post_form = Postform()
    
    context = {"form": post_form}
    
    return render(request, "posts/post_create.html", context)