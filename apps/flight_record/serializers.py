from rest_framework import serializers

from apps.flight_record.models import FlightRecord


class FlightRecordReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "id",
            "flight_no",
            "mission",
            "mission_name",
            "route_name",
            "airport_name",
            "drone",
            "drone_name",
            "pilot",
            "pilot_name",
            "start_time",
            "end_time",
            "flight_duration",
            "photo_count",
            "video_count",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

