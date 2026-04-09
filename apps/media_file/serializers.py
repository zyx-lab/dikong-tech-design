from rest_framework import serializers

from apps.access.models import ScopeType
from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.media_file.models import MediaFile
from apps.mission.models import Mission, MissionStatus


class MediaFileReadSerializer(serializers.ModelSerializer):
    mission_id = serializers.IntegerField(read_only=True, allow_null=True)
    device_sn = serializers.CharField(read_only=True)
    dji_file_id = serializers.SerializerMethodField()
    sync_status = serializers.SerializerMethodField()
    last_sync_at = serializers.SerializerMethodField()

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "flight_record",
            "mission_id",
            "device_sn",
            "media_type",
            "file_name",
            "thumbnail_url",
            "file_size",
            "latitude",
            "longitude",
            "captured_at",
            "dji_file_id",
            "sync_status",
            "last_sync_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_dji_file_id(self, obj):
        return getattr(getattr(obj, "dji_index", None), "dji_file_id", "")

    def get_sync_status(self, obj):
        return getattr(getattr(obj, "dji_index", None), "sync_status", "")

    def get_last_sync_at(self, obj):
        return getattr(getattr(obj, "dji_index", None), "last_sync_at", None)


class MediaFileBindMissionSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    mission_id = serializers.IntegerField(min_value=1)
    media_file_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        tenant = require_request_tenant(self.context)
        mission = Mission.objects.filter(
            tenant=tenant,
            id=attrs["mission_id"],
            is_deleted=False,
        ).first()
        if mission is None:
            raise serializers.ValidationError({"mission_id": ["任务不存在或已删除"]})
        if mission.status != MissionStatus.DRONE_BOUND:
            raise serializers.ValidationError({"mission_id": ["仅允许绑定到已绑定无人机的任务"]})
        if not mission.device_sn:
            raise serializers.ValidationError({"mission_id": ["任务缺少 device_sn，无法绑定媒体"]})
        request = self.context.get("request")
        decision = getattr(request, "_authz_decision", None) if request is not None else None
        if decision is not None and decision.scope == ScopeType.ASSIGNED and mission.pilot_id != decision.tenant_member_id:
            raise serializers.ValidationError({"mission_id": ["ASSIGNED 范围下只能绑定到当前飞手自己的任务"]})
        attrs["mission"] = mission
        attrs["media_file_ids"] = list(dict.fromkeys(attrs["media_file_ids"]))
        return attrs


class MediaFileBindMissionResultSerializer(serializers.Serializer):
    mission_id = serializers.IntegerField()
    media_file_ids = serializers.ListField(child=serializers.IntegerField())
    updated_count = serializers.IntegerField()
