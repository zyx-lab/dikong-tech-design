from rest_framework import serializers

from apps.access.api_v1.base import StrictSerializer
from apps.access.api_v1.serializers.common import SessionUserSerializer, UserAccountSerializer


class LoginRequestSerializer(StrictSerializer):
    username = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False)


class RefreshRequestSerializer(StrictSerializer):
    refreshToken = serializers.CharField(trim_whitespace=True)


class RegisterRequestSerializer(StrictSerializer):
    username = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False)
    name = serializers.CharField(trim_whitespace=True)
    phone = serializers.CharField(trim_whitespace=True)


class RegisterByPhoneRequestSerializer(StrictSerializer):
    phone = serializers.CharField(trim_whitespace=True)
    smsCode = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False)


class SessionTokenSerializer(serializers.Serializer):
    accessToken = serializers.CharField()
    refreshToken = serializers.CharField()
    tokenType = serializers.CharField()
    expiresIn = serializers.IntegerField()
    refreshExpiresIn = serializers.IntegerField()


class LoginResponseSerializer(SessionTokenSerializer):
    user = SessionUserSerializer()


class RegisterResponseSerializer(UserAccountSerializer):
    pass
