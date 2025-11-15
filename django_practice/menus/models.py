from django.db import models

# Create your models here.


class Menu(models.Model):
    name = models.CharField(max_length=180)
    description = models.TextField(max_length=120)
    price = models.IntegerField()
    img_path = models.CharField(max_length=255)
    
    def __str__(self):
        return self.name