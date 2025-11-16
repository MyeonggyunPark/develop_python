from django import forms


class PostForm(forms.Form):
    title = forms.CharField(max_length=100, label="Title")
    content = forms.CharField(widget=forms.Textarea, label="Content")
    feeling = forms.CharField(max_length=80, label="Feeling")
    feeling_point = forms.IntegerField(min_value=0, max_value=10, label="Feeling Point")
