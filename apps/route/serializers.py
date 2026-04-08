import zipfile

from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route


class RouteReadSerializer(serializers.ModelSerializer):
    is_published = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            "id",
            "name",
            "is_published",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))


class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    kmz_file = serializers.FileField(
        required=True,
        allow_empty_file=False,
        help_text="航线 KMZ 文件。",
        error_messages={
            "required": "未提交文件。",
            "empty": "提交的文件为空。",
        },
    )

    class Meta:
        model = Route
        fields = ["name", "kmz_file"]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "kmz_file": {"help_text": "航线 KMZ 文件。"},
        }

    def validate_kmz_file(self, value):
        try:
            value.seek(0)
            with zipfile.ZipFile(value, "r") as archive:
                archive.namelist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
            raise serializers.ValidationError("上传文件必须是有效 KMZ/ZIP 文件")
        finally:
            value.seek(0)
        return value

    def _strip_kmz_file(self, validated_data):
        validated_data.pop("kmz_file", None)
        return validated_data

    def create(self, validated_data):
        return super().create(self._strip_kmz_file(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._strip_kmz_file(validated_data))


class RouteCreateSerializer(RouteWriteSerializer):
    pass


class RouteUpdateSerializer(RouteWriteSerializer):
    pass
