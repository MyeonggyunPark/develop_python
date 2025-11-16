from django import forms
from .models import Post


class PostForm(forms.ModelForm):

    class Meta:
        model = Post
        fields = ["title", "content", "feeling", "feeling_point"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full p-2 border border-gray-300 rounded mt-2",
                "placeholder": "Enter the title",
            }),
            "content": forms.Textarea(attrs={
                "class": "w-full p-2 border border-gray-300 rounded mt-2",
                "placeholder": "Enter the content",
            }),
            "feeling": forms.TextInput(attrs={
                "class": "w-full p-2 border border-gray-300 rounded mt-2",
                "placeholder": "Enter your feeling",
            }),
            "feeling_point": forms.NumberInput(attrs={
                "class": "w-full p-2 border border-gray-300 rounded mt-2",
                "placeholder": "Enter the feeling point",
            }),
        }