from rest_framework import serializers

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
