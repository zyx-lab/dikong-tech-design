from django.urls import path

from apps.inspection_v2.consumers import DrcSessionConsumer
from apps.resource_v2.consumers import CameraResultsConsumer, DjiMqttConsumer


websocket_urlpatterns = [
    path("ws/v2/dji/mqtt", DjiMqttConsumer.as_asgi()),
    path("ws/v2/drc/sessions/<uuid:session_id>", DrcSessionConsumer.as_asgi()),
    path("ws/v2/cameras/<int:id>/results", CameraResultsConsumer.as_asgi()),
]
