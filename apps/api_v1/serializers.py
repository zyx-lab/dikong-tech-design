from rest_framework import serializers


class RejectUnknownFieldsMixin:
    unknown_field_error = "该字段在此接口不可写"

    def validate(self, attrs):
        initial_data = getattr(self, "initial_data", None)
        if isinstance(initial_data, dict):
            unknown_fields = sorted(set(initial_data.keys()) - set(self.fields.keys()))
            if unknown_fields:
                raise serializers.ValidationError({field: self.unknown_field_error for field in unknown_fields})
        return super().validate(attrs)

