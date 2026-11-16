from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """start / finish: a US place name, address, or "lat,lng"."""
    start = serializers.CharField(max_length=200)
    finish = serializers.CharField(max_length=200)
