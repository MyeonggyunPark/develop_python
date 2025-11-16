from django.shortcuts import render, redirect
from .models import Post
from .forms import PostForm

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
        post_form = PostForm(request.POST)
        if post_form.is_valid():
            post_form.save()
        return redirect("post-list")
    else:    
        post_form = PostForm()
        
        context = {"form": post_form}
        
        return render(request, "posts/post_create.html", context)