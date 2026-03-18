from rest_framework import serializers

from apps.access.api_v1.base import StrictSerializer
from apps.access.api_v1.serializers.tenant import TenantMemberResponseSerializer

class PlatformTenantCreateSerializer(StrictSerializer):
    tenantCode = serializers.CharField(trim_whitespace=True)
    name = serializers.CharField(trim_whitespace=True)
    remark = serializers.CharField(required=False, allow_blank=True, allow_null=True)

class PlatformInitializeAdminSerializer(StrictSerializer):
    userId = serializers.IntegerField()
    displayName = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PlatformInitializeAdminResponseSerializer(TenantMemberResponseSerializer):
    tenantId = serializers.IntegerField()
