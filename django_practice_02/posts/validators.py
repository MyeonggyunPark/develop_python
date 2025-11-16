from django.core.exceptions import ValidationError


def validate_feeling_text(value):
    if value.isdigit():
        raise ValidationError("Feeling text cannot be purely numeric.")