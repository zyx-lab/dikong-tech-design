from rest_framework import serializers

from apps.api_v1.tenant_scope import require_request_tenant
from apps.drone.models import Drone


class DroneReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Drone
        fields = [
            "id",
            "code",
            "name",
            "model",
            "serial_no",
            "status",
            "org_id",
            "created_by_tenant_member_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DroneWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        # 明确拒绝未开放字段，避免调用方误以为提交成功。
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        code = attrs.get("code", instance.code if instance is not None else None)
        serial_no = attrs.get("serial_no", instance.serial_no if instance is not None else None)

        if code and Drone.objects.filter(tenant=current_tenant, code=code).exclude(pk=getattr(instance, "pk", None)).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})
        if serial_no and Drone.objects.filter(
            tenant=current_tenant,
            serial_no=serial_no,
        ).exclude(pk=getattr(instance, "pk", None)).exists():
            raise serializers.ValidationError({"serial_no": "当前租户下已存在相同出厂序列号"})
        return attrs

    class Meta:
        model = Drone
        # V1 编辑仅允许基础信息，不允许直接写状态字段。
        fields = [
            "code",
            "name",
            "model",
            "serial_no",
            "org_id",
        ]
