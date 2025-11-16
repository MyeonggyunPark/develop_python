from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator, MinLengthValidator
from .validators import validate_feeling_text
from datetime import timedelta

# Create your models here.


class Post(models.Model):
    title = models.CharField(max_length=100)
    content = models.TextField(validators=[MinLengthValidator(10, message="Content must be at least 10 characters long.")])
    feeling = models.CharField(max_length=80, validators=[validate_feeling_text])
    feeling_point = models.IntegerField(
        default=0, 
        validators=[
            MinValueValidator(0, message="Feeling point must be at least 0."), 
            MaxValueValidator(10, message="Feeling point cannot exceed 10.")
        ]
    )
    dt_created = models.DateTimeField(auto_now_add=True)
    dt_updated = models.DateTimeField(auto_now=True)

    def is_updated(self):
        return (self.dt_updated - self.dt_created) > timedelta(seconds=1)

    def __str__(self):
        return self.title
    