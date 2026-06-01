from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.workforce_v2.models import PilotProfile, PilotQualification


class PilotQualificationReadSerializer(serializers.ModelSerializer):
    qualificationType = serializers.CharField(source="qualification_type", read_only=True)
    certificateNo = serializers.CharField(source="certificate_no", read_only=True)
    issuedAt = serializers.DateField(source="issued_at", allow_null=True, read_only=True)
    expiresAt = serializers.DateField(source="expires_at", allow_null=True, read_only=True)
    isEffective = serializers.SerializerMethodField()

    class Meta:
        model = PilotQualification
        fields = [
            "id",
            "qualificationType",
            "certificateNo",
            "issuedAt",
            "expiresAt",
            "status",
            "remark",
            "isEffective",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_isEffective(self, obj) -> bool:
        return obj.is_effective()


class PilotProfileReadSerializer(serializers.ModelSerializer):
    accountProfileId = serializers.IntegerField(source="account_profile_id", read_only=True)
    userId = serializers.IntegerField(source="account_profile.user_id", read_only=True)
    username = serializers.CharField(source="account_profile.user.username", read_only=True)
    departmentId = serializers.IntegerField(source="account_profile.department_id", read_only=True)
    departmentName = serializers.CharField(source="account_profile.department.name", read_only=True)
    qualifications = PilotQualificationReadSerializer(many=True, read_only=True)

    class Meta:
        model = PilotProfile
        fields = [
            "id",
            "accountProfileId",
            "userId",
            "username",
            "departmentId",
            "departmentName",
            "display_name",
            "phone",
            "level",
            "status",
            "remark",
            "qualifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PilotProfileWriteSerializer(StrictSerializer):
    accountProfileId = serializers.IntegerField(min_value=1)
    displayName = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)


class PilotProfileUpdateSerializer(StrictSerializer):
    displayName = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)


class PilotQualificationWriteSerializer(StrictSerializer):
    qualificationType = serializers.CharField(max_length=128)
    certificateNo = serializers.CharField(max_length=128, required=False, allow_blank=True)
    issuedAt = serializers.DateField(required=False, allow_null=True)
    expiresAt = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        issued_at = attrs.get("issuedAt")
        expires_at = attrs.get("expiresAt")
        if issued_at and expires_at and issued_at > expires_at:
            raise serializers.ValidationError({"expiresAt": ["有效期结束日期不能早于签发日期"]})
        return attrs
