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


class MediaFileWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})
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
