from django.db import models

# Create your models here.


class Post(models.Model):
    title = models.CharField(max_length=100)
    content = models.TextField()
    feeling = models.CharField(max_length=80)
    felling_point = models.IntegerField(default=0)
    dt_created = models.DateTimeField(auto_now_add=True)
    dt_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.title
    