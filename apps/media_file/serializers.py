from datetime import datetime

from rest_framework import serializers

from apps.media_file.models import MediaFile


class MediaFileReadSerializer(serializers.ModelSerializer):
    mission_id = serializers.SerializerMethodField()
    device_sn = serializers.SerializerMethodField()
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

    def get_mission_id(self, obj) -> int | None:
        return getattr(getattr(obj, "dji_index", None), "mission_id", None)

    def get_device_sn(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "device_sn", "")

    def get_dji_file_id(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "dji_file_id", "")

    def get_sync_status(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "sync_status", "")

    def get_last_sync_at(self, obj) -> datetime | None:
        return getattr(getattr(obj, "dji_index", None), "last_sync_at", None)
