from rest_framework import serializers
from .models import Movie, Review

class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["id", "movie", "username", "star", "comment", "created"]
        extra_kwargs = {
            "movie": {"read_only": True},
        }

class MovieSerializer(serializers.ModelSerializer):
    reviews = ReviewSerializer(many=True, read_only=True)
    class Meta:
        model = Movie
        fields = [
            "id",
            "name",
            "reviews",
            "opening_date",
            "running_time",
            "overview",
        ]
        read_only_fields = ["reviews"]
    # id = serializers.IntegerField(read_only=True)
    # name = serializers.CharField()
    # opening_date = serializers.DateField()
    # running_time = serializers.IntegerField()
    # overview = serializers.CharField()

    # def create(self, validated_data):
    #     return Movie.objects.create(**validated_data)

    # def update(self, instance, validated_data):
    #     instance.name = validated_data.get("name", instance.name)
    #     instance.opening_date = validated_data.get("opening_date", instance.opening_date)
    #     instance.running_time = validated_data.get("running_time", instance.running_time)
    #     instance.overview = validated_data.get("overview", instance.overview)
    #     instance.save()
    #     return instance


