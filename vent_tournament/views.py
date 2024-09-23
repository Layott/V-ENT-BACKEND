from django.shortcuts import render
from imports import api_view

# Create your views here.


@api_view(['POST'])
def create_tournament(request):
    