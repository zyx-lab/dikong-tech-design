from rest_framework import serializers

from apps.access.api_v1.serializers.common import MeTenantSerializer, UserAccountSerializer


class MeProfileResponseSerializer(UserAccountSerializer):
    pass


class MeTenantsItemSerializer(MeTenantSerializer):
    pass
