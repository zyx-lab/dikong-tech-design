from rest_framework import serializers

from apps.access.api_v1.context import normalize_display_name
from apps.access.models import DirectoryStatus, ScopeType, TenantMemberStatus, TenantStatus, UserStatus


USER_STATUS_LABELS = {
    UserStatus.ACTIVE: "ACTIVE",
    UserStatus.DISABLED: "DISABLED",
}
TENANT_STATUS_LABELS = {
    TenantStatus.ACTIVE: "ACTIVE",
    TenantStatus.DISABLED: "DISABLED",
}
MEMBER_STATUS_LABELS = {
    TenantMemberStatus.ACTIVE: "ACTIVE",
    TenantMemberStatus.DISABLED: "DISABLED",
}
DIRECTORY_STATUS_LABELS = {
    DirectoryStatus.ACTIVE: "ACTIVE",
    DirectoryStatus.DISABLED: "DISABLED",
}


class StaffProfileSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=False)
    phone = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_blank=True, required=False)
    employmentStatus = serializers.SerializerMethodField()
    orgId = serializers.IntegerField(allow_null=True, required=False)

    def get_employmentStatus(self, obj) -> str:
        return "ACTIVE" if obj.employment_status == 1 else "INACTIVE"


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
    hasPlatformAccess = serializers.BooleanField()
    staffProfile = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return USER_STATUS_LABELS[obj.status]

    def get_staffProfile(self, obj) -> dict | None:
        staff = getattr(obj, "staff_profile", None)
        if staff is None:
            return None
        return StaffProfileSerializer(staff).data


class TenantSummarySerializer(serializers.Serializer):
    tenantId = serializers.IntegerField(source="id")
    tenantCode = serializers.CharField(source="code")
    name = serializers.CharField()
    status = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    remark = serializers.CharField(allow_blank=True)
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")

    def get_status(self, obj) -> str:
        return TENANT_STATUS_LABELS[obj.status]

    def get_plan(self, obj) -> str | None:
        del obj
        return None


class TenantDirectorySerializer(serializers.Serializer):
    tenantId = serializers.IntegerField(source="id")
    tenantCode = serializers.CharField(source="code")
    name = serializers.CharField()
    status = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")

    def get_status(self, obj) -> str:
        return TENANT_STATUS_LABELS[obj.status]

    def get_plan(self, obj) -> str | None:
        del obj
        return None


class MemberSerializer(serializers.Serializer):
    memberId = serializers.IntegerField(source="id")
    userId = serializers.IntegerField(source="user.id")
    username = serializers.CharField(source="user.username")
    displayName = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    roleCodes = serializers.ListField(child=serializers.CharField())

    def get_displayName(self, obj) -> str | None:
        return normalize_display_name(obj.display_name)

    def get_status(self, obj) -> str:
        return MEMBER_STATUS_LABELS[obj.status]


class TenantMeMemberSerializer(serializers.Serializer):
    memberId = serializers.IntegerField(source="id")
    userId = serializers.IntegerField(source="user.id")
    displayName = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    roleCodes = serializers.ListField(child=serializers.CharField())

    def get_displayName(self, obj) -> str | None:
        return normalize_display_name(obj.display_name)

    def get_status(self, obj) -> str:
        return MEMBER_STATUS_LABELS[obj.status]


class RoleSerializer(serializers.Serializer):
    roleId = serializers.IntegerField(source="id")
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)


class PermissionGrantSerializer(serializers.Serializer):
    permission = serializers.CharField(source="permission.code")
    scopeType = serializers.ChoiceField(choices=[ScopeType.ALL, ScopeType.OWN, ScopeType.ASSIGNED], source="scope_type")


class RoleDetailSerializer(RoleSerializer):
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")
    permissionGrants = serializers.SerializerMethodField()

    def get_permissionGrants(self, obj) -> list[dict]:
        grants = obj.permission_grants.select_related("permission").order_by("permission__code", "id")
        return PermissionGrantSerializer(grants, many=True).data


class PermissionSerializer(serializers.Serializer):
    permissionId = serializers.IntegerField(source="id")
    code = serializers.CharField()
    name = serializers.CharField()
    module = serializers.CharField()
    resourceCode = serializers.CharField(source="resource_code")
    status = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return DIRECTORY_STATUS_LABELS[obj.status]


class MeTenantSerializer(serializers.Serializer):
    tenantId = serializers.IntegerField(source="tenant.id")
    tenantCode = serializers.CharField(source="tenant.code")
    name = serializers.CharField(source="tenant.name")
    memberId = serializers.IntegerField(source="id")
    roleCodes = serializers.ListField(child=serializers.CharField())


class AuditLogSerializer(serializers.Serializer):
    auditLogId = serializers.IntegerField(source="id")
    tenantId = serializers.IntegerField(source="tenant.id", allow_null=True, required=False)
    tenantCode = serializers.CharField(source="tenant.code", allow_null=True, required=False)
    action = serializers.CharField()
    targetType = serializers.CharField(source="target_type")
    targetId = serializers.CharField(source="target_id")
    operatorUserId = serializers.IntegerField(source="actor_user.id", allow_null=True)
    operatorDisplayName = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")
    summary = serializers.SerializerMethodField()

    def get_operatorDisplayName(self, obj) -> str | None:
        actor = getattr(obj, "actor_user", None)
        if actor is None:
            return None
        staff = getattr(actor, "staff_profile", None)
        if staff and staff.name:
            return staff.name
        return actor.username

    def get_summary(self, obj) -> str:
        return obj.action
