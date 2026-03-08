from rest_framework import serializers

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
            "created_by_staff_id",
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
