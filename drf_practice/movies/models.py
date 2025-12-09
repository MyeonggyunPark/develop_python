from django.db import models

# Create your models here.

class Movie(models.Model):
    name = models.CharField(max_length=30)
    opening_date = models.DateField()
    running_time = models.IntegerField()
    overview = models.TextField()
    
    def __str__(self):
        return self.name