from rest_framework import serializers

from apps.audit_v2.models import V2AuditLog


class AuditLogReadSerializer(serializers.ModelSerializer):
    actorDepartmentId = serializers.IntegerField(source="actor_department_id", allow_null=True, read_only=True)
    resourceOwnerDepartmentId = serializers.IntegerField(source="resource_owner_department_id", allow_null=True, read_only=True)
    resourceType = serializers.CharField(source="resource_type", read_only=True)
    resourceObjectId = serializers.CharField(source="resource_object_id", read_only=True)
    targetType = serializers.CharField(source="target_type", read_only=True)
    targetId = serializers.CharField(source="target_id", read_only=True)

    class Meta:
        model = V2AuditLog
        fields = [
            "id",
            "action",
            "actorDepartmentId",
            "resourceOwnerDepartmentId",
            "resourceType",
            "resourceObjectId",
            "targetType",
            "targetId",
            "before_data",
            "after_data",
            "created_at",
        ]
        read_only_fields = fields
