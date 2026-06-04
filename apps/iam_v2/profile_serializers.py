from rest_framework import serializers

from apps.iam_v2.serializers import DepartmentReadSerializer
from apps.iam_v2.serializers import AccountQualificationReadSerializer, AccountRoleProfileReadSerializer


class V2SessionAccountSummarySerializer(serializers.Serializer):
    userId = serializers.IntegerField()
    username = serializers.CharField()
    accountProfileId = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    status = serializers.IntegerField()
    department = DepartmentReadSerializer()
    roleCodes = serializers.ListField(child=serializers.CharField())


class V2SessionLoginResponseSerializer(serializers.Serializer):
    accessToken = serializers.CharField()
    refreshToken = serializers.CharField()
    tokenType = serializers.CharField()
    expiresIn = serializers.IntegerField()
    refreshExpiresIn = serializers.IntegerField()
    user = V2SessionAccountSummarySerializer()


class V2MeProfileSerializer(serializers.Serializer):
    userId = serializers.IntegerField()
    username = serializers.CharField()
    accountProfileId = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    status = serializers.IntegerField()
    department = DepartmentReadSerializer()
    roleCodes = serializers.ListField(child=serializers.CharField())
    profiles = AccountRoleProfileReadSerializer(many=True)
    qualifications = AccountQualificationReadSerializer(many=True)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
