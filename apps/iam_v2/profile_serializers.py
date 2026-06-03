from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import DEPARTMENT_ROLE_CODES, V2AccountQualification
from apps.iam_v2.serializers import DepartmentReadSerializer


class AccountQualificationReadSerializer(serializers.ModelSerializer):
    accountProfileId = serializers.IntegerField(source="account_profile_id", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    qualificationType = serializers.CharField(source="qualification_type", read_only=True)
    certificateNo = serializers.CharField(source="certificate_no", read_only=True)
    issuedAt = serializers.DateField(source="issued_at", allow_null=True, read_only=True)
    expiresAt = serializers.DateField(source="expires_at", allow_null=True, read_only=True)
    isEffective = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2AccountQualification
        fields = [
            "id",
            "accountProfileId",
            "roleCode",
            "qualificationType",
            "certificateNo",
            "issuedAt",
            "expiresAt",
            "status",
            "remark",
            "isEffective",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields

    def get_isEffective(self, obj) -> bool:
        return obj.is_effective()


class AccountQualificationWriteSerializer(StrictSerializer):
    roleCode = serializers.CharField(max_length=64)
    qualificationType = serializers.CharField(max_length=128)
    certificateNo = serializers.CharField(max_length=128)
    issuedAt = serializers.DateField()
    expiresAt = serializers.DateField()
    status = serializers.ChoiceField(choices=DirectoryStatus.choices)
    remark = serializers.CharField()

    def validate_roleCode(self, value):
        role_code = str(value or "").strip()
        if role_code not in DEPARTMENT_ROLE_CODES:
            raise serializers.ValidationError("资质只能归属 v2 部门业务角色")
        return role_code

    def validate(self, attrs):
        attrs = super().validate(attrs)
        issued_at = attrs["issuedAt"]
        expires_at = attrs["expiresAt"]
        if issued_at > expires_at:
            raise serializers.ValidationError({"expiresAt": ["有效期结束日期不能早于签发日期"]})
        return attrs


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


class V2PilotRoleProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    accountProfileId = serializers.IntegerField()
    displayName = serializers.CharField()
    level = serializers.CharField(allow_blank=True)
    status = serializers.IntegerField()
    remark = serializers.CharField(allow_blank=True)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()


class V2RoleProfilesSerializer(serializers.Serializer):
    departmentAdmin = serializers.DictField(allow_null=True)
    taskMonitorDispatcher = serializers.DictField(allow_null=True)
    pilot = V2PilotRoleProfileSerializer(allow_null=True)
    workOrderHandler = serializers.DictField(allow_null=True)


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
    roleProfiles = V2RoleProfilesSerializer()
    qualifications = AccountQualificationReadSerializer(many=True)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
