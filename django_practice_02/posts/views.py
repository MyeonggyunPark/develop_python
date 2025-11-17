from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
# from django.core.paginator import Paginator
# from django.views import View
from django.views.generic import CreateView, ListView, DetailView, UpdateView

from .models import Post
from .forms import PostForm

# Create your views here.


def index(request):
    return render(request, "posts/index.html")


# def post_list(request):
#     posts = Post.objects.all().order_by("-dt_created")
#     paginator = Paginator(posts, 5)
#     curr_page_number = request.GET.get("page")

#     if curr_page_number is None:
#         curr_page_number = 1

#     page = paginator.get_page(curr_page_number)

#     context = {"page": page}
#     return render(request, "posts/post_list.html", context)

class PostListView(ListView):
    model = Post
    template_name = "posts/post_list.html"
    context_object_name = "posts"
    ordering = ["-dt_created"]
    paginate_by = 5
    page_kwarg = "page"


# def post_detail(request, id):
#     post = get_object_or_404(Post, id=id)
#     context = {"post": post}

#     return render(request, "posts/post_detail.html", context)

class PostDetailView(DetailView):
    model = Post
    template_name = "posts/post_detail.html"
    context_object_name = "post"
    pk_url_kwarg = "id"

# def post_create(request):
#     if request.method == "POST":
#         post_form = PostForm(request.POST)
#         if post_form.is_valid():
#             post_form.save()
#             return redirect("post-list")
#     else:
#         post_form = PostForm()

#     context = {"form": post_form, "submit_label": "Create"}
#     return render(request, "posts/post_create.html", context)

# class PostCreateView(View):
#     def get(self, request):
#         post_form = PostForm()
#         context = {"form": post_form, "submit_label": "Create"}
#         return render(request, "posts/post_create.html", context)
    
#     def post(self, request):
#         post_form = PostForm(request.POST)
#         if post_form.is_valid():
#             post_form.save()
#             return redirect("post-detail", id=post_form.instance.id)
#         context = {"form": post_form, "submit_label": "Create"}
#         return render(request, "posts/post_create.html", context)

class PostCreateView(CreateView):
    model = Post
    form_class = PostForm
    template_name = "posts/post_create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["submit_label"] = "Create"
        return context

    def get_success_url(self):
        return reverse_lazy("post-detail", kwargs={"id": self.object.id})


# def post_update(request, id):
#     post = get_object_or_404(Post, id=id)

#     if request.method == "POST":
#         post_form = PostForm(request.POST, instance=post)
#         if post_form.is_valid():
#             post_form.save()
#             return redirect("post-detail", id=post.id)
#     else:
#         post_form = PostForm(instance=post)

#     context = {"form": post_form, "submit_label": "Update"}
#     return render(request, "posts/post_create.html", context)

class PostUpdateView(UpdateView):
    model = Post
    form_class = PostForm
    template_name = "posts/post_create.html"
    pk_url_kwarg = "id"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["submit_label"] = "Update"
        return context

    def get_success_url(self):
        return reverse_lazy("post-detail", kwargs={"id": self.object.id})

def post_delete(request, id):
    post = get_object_or_404(Post, id=id)    

    if request.method == "POST":
        post.delete()
        return redirect("post-list")

    context = {"post": post}
    return render(request, "posts/post_detail.html", context)
