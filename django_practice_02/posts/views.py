from django.shortcuts import render, redirect
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
    if request.method == "POST":
        title = request.POST.get("title")
        content = request.POST.get("content")
        feeling = request.POST.get("feeling")
        feeling_point = request.POST.get("feeling_point")
        Post.objects.create(
            title=title,
            content=content,
            feeling=feeling,
            feeling_point=feeling_point
        )
        return redirect("post-list")
    else:    
        post_form = Postform()
        
        context = {"form": post_form}
        
        return render(request, "posts/post_create.html", context)