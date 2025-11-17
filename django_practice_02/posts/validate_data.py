from .models import Post
import random

def validate_post():
    posts = Post.objects.all()

    for post in posts:
        if post.feeling_point < 0 or post.feeling_point > 10:
            print(f"Post ID {post.id} has an invalid feeling_point: {post.feeling_point}")
            
            post.feeling_point = random.randint(0, 10)
            post.save()
            
            print(f"Post ID {post.id} updated to new feeling_point: {post.feeling_point}")