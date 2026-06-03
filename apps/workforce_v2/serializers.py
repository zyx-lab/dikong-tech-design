from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.workforce_v2.models import PilotProfile


class PilotProfileReadSerializer(serializers.ModelSerializer):
    accountProfileId = serializers.IntegerField(source="account_profile_id", read_only=True)
    userId = serializers.IntegerField(source="account_profile.user_id", read_only=True)
    username = serializers.CharField(source="account_profile.user.username", read_only=True)
    departmentId = serializers.IntegerField(source="account_profile.department_id", read_only=True)
    departmentName = serializers.CharField(source="account_profile.department.name", read_only=True)

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
            "level",
            "status",
            "remark",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PilotProfileWriteSerializer(StrictSerializer):
    accountProfileId = serializers.IntegerField(min_value=1)
    displayName = serializers.CharField(max_length=128)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)


class PilotProfileUpdateSerializer(StrictSerializer):
    displayName = serializers.CharField(max_length=128)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)
