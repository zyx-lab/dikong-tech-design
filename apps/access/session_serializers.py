from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import EmploymentStatus, UserStatus


USER_STATUS_LABELS = {
    UserStatus.ACTIVE: "ACTIVE",
    UserStatus.DISABLED: "DISABLED",
}


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


class StaffProfileSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=False)
    phone = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_blank=True, required=False)
    employmentStatus = serializers.SerializerMethodField()
    orgId = serializers.IntegerField(allow_null=True, required=False)

    def get_employmentStatus(self, obj) -> str:
        return "ACTIVE" if obj.employment_status == EmploymentStatus.ACTIVE else "INACTIVE"


class UserAccountSerializer(serializers.Serializer):
    userId = serializers.IntegerField(source="id")
    username = serializers.CharField()
    status = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")
    staffProfile = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return USER_STATUS_LABELS[obj.status]

    def get_staffProfile(self, obj) -> dict | None:
        staff = getattr(obj, "staff_profile", None)
        if staff is None:
            return None
        return StaffProfileSerializer(staff).data


class SessionUserSerializer(serializers.Serializer):
    userId = serializers.IntegerField(source="id")
    username = serializers.CharField()
    status = serializers.SerializerMethodField()
    hasPlatformAccess = serializers.SerializerMethodField()
    staffProfile = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return USER_STATUS_LABELS[obj.status]

    def get_hasPlatformAccess(self, obj) -> bool:
        return bool(getattr(obj, "is_platform_admin", False))

    def get_staffProfile(self, obj) -> dict | None:
        staff = getattr(obj, "staff_profile", None)
        if staff is None:
            return None
        return StaffProfileSerializer(staff).data


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
