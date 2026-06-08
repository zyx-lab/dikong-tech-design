from rest_framework import serializers

from apps.access.api_base import StrictSerializer


class LoginRequestSerializer(StrictSerializer):
    username = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False)


class RefreshRequestSerializer(StrictSerializer):
    refreshToken = serializers.CharField(trim_whitespace=True)


class SessionTokenSerializer(serializers.Serializer):
    accessToken = serializers.CharField()
    refreshToken = serializers.CharField()
    tokenType = serializers.CharField()
    expiresIn = serializers.IntegerField()
    refreshExpiresIn = serializers.IntegerField()
