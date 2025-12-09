from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK

from .models import Movie
from .serializers import MovieSerializer

# Create your views here.


@api_view(["GET"])
def movie_list(request):
    movies = Movie.objects.all()
    serializer = MovieSerializer(movies, many=True)
    return Response(serializer.data, status=HTTP_200_OK)
