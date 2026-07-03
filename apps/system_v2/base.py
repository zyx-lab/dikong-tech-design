from rest_framework.generics import GenericAPIView

from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.api_base import EmptySerializer
from apps.common.api_response import BusinessApiResponseMixin


class SystemV2APIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer
