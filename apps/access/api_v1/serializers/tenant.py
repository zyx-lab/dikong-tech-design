from rest_framework import serializers

from apps.access.api_v1.base import StrictSerializer
from apps.access.api_v1.serializers.common import AuditLogSerializer, MemberSerializer, RoleSerializer, TenantMeMemberSerializer, TenantSummarySerializer


class TenantMeResponseSerializer(serializers.Serializer):
    tenant = TenantSummarySerializer()
    member = TenantMeMemberSerializer()


class TenantMemberCreateSerializer(StrictSerializer):
    userId = serializers.IntegerField()
    displayName = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    roleCodes = serializers.ListField(child=serializers.CharField(), required=False)


class TenantMemberUpdateSerializer(StrictSerializer):
    displayName = serializers.CharField(required=True, allow_blank=True, allow_null=True)


class TenantMemberRolesReplaceSerializer(StrictSerializer):
    roleCodes = serializers.ListField(child=serializers.CharField(), required=True)


class TenantMemberResponseSerializer(MemberSerializer):
    pass


class TenantRoleSerializer(RoleSerializer):
    pass


class TenantAuditLogSerializer(AuditLogSerializer):
    pass
