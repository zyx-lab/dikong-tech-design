from rest_framework import serializers

from apps.access.models import ScopeType
from apps.api_v1.tenant_scope import require_request_tenant
from apps.media_file.models import MediaFile


class MediaFileReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaFile
        fields = [
            "id",
            "flight_record",
            "media_type",
            "file_name",
            "file_url",
            "thumbnail_url",
            "file_size",
            "latitude",
            "longitude",
            "captured_at",
            "is_deleted",
            "deleted_at",
            "created_at",
        ]
        read_only_fields = fields


class MediaFileWriteSerializer(serializers.ModelSerializer):
    def _validate_assigned_scope_target(self, *, flight_record):
        request = self.context.get("request")
        decision = getattr(request, "_authz_decision", None) if request is not None else None
        if decision is None or decision.scope != ScopeType.ASSIGNED:
            return
        if flight_record is None or flight_record.pilot_id != decision.tenant_member_id:
            raise serializers.ValidationError({"flight_record": "ASSIGNED 范围下只能操作当前飞手自己的飞行记录媒体"})

    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        flight_record = attrs.get("flight_record", instance.flight_record if instance is not None else None)
        if flight_record is not None and flight_record.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"flight_record": "仅允许绑定当前租户下的飞行记录"})
        self._validate_assigned_scope_target(flight_record=flight_record)
        return attrs

    class Meta:
        model = MediaFile
        fields = [
            "flight_record",
            "media_type",
            "file_name",
            "file_url",
            "thumbnail_url",
            "file_size",
            "latitude",
            "longitude",
            "captured_at",
        ]
