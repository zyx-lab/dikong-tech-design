from rest_framework import serializers

from apps.access.models import EmploymentStatus
from apps.drone.models import DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus


class DroneAssignmentReadSerializer(serializers.ModelSerializer):
    drone_code = serializers.CharField(source="drone.code", read_only=True)
    drone_name = serializers.CharField(source="drone.name", read_only=True)
    staff_no = serializers.CharField(source="staff.staff_no", read_only=True)
    staff_name = serializers.CharField(source="staff.name", read_only=True)

    class Meta:
        model = DroneAssignment
        fields = [
            "id",
            "drone",
            "drone_code",
            "drone_name",
            "staff",
            "staff_no",
            "staff_name",
            "status",
            "start_at",
            "end_at",
            "created_by_staff_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DroneAssignmentCreateSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        drone = attrs["drone"]
        staff = attrs["staff"]

        if drone.status == DroneStatus.RETIRED:
            raise serializers.ValidationError({"drone": "已退役无人机不能创建分配关系"})

        if staff.employment_status != EmploymentStatus.ACTIVE:
            raise serializers.ValidationError({"staff": "仅允许分配给在职人员"})

        if staff.staff_type.code != "pilot_operator":
            raise serializers.ValidationError({"staff": "仅允许分配给飞手类型（pilot_operator）"})

        if DroneAssignment.objects.filter(
            drone=drone,
            staff=staff,
            status=DroneAssignmentStatus.ACTIVE,
        ).exists():
            raise serializers.ValidationError({"non_field_errors": ["该无人机与该飞手已存在生效分配"]})

        return attrs

    def create(self, validated_data):
        # 开始时间由模型自动写入，统一使用 ACTIVE 初始状态。
        validated_data["status"] = DroneAssignmentStatus.ACTIVE
        validated_data["end_at"] = None
        return super().create(validated_data)

    class Meta:
        model = DroneAssignment
        fields = [
            "id",
            "drone",
            "staff",
        ]
        read_only_fields = ["id"]
